"""Offline integration checks for the standalone RefSeq comparison pilot.

Run without the app's optional dependencies:
uv run --no-project --with duckdb --with pyarrow python tests/test_refseq_pilot.py
"""

import gzip
import importlib.util
import tempfile
import unittest
from pathlib import Path

import duckdb

spec = importlib.util.spec_from_file_location(
    "refseq_pilot", Path(__file__).resolve().parents[1] / "scripts/compare_refseq_go.py"
)
pilot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pilot)


def gaf(accession, term, qualifier="enables", taxon="taxon:160488"):
    return (
        "\t".join(
            [
                "DB",
                accession,
                "",
                qualifier,
                term,
                "PMID:1",
                "IEA",
                "HMM:test",
                "F",
                "protein",
                "",
                "protein",
                taxon,
                "20260101",
                "test",
                "",
                "",
            ]
        )
        + "\n"
    )


class PilotTests(unittest.TestCase):
    def test_reject_wrong_taxon(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.gaf"
            path.write_text(gaf("p", "GO:0000001", taxon="taxon:9606"))
            with self.assertRaisesRegex(ValueError, "shape/taxon"):
                pilot.read_gaf(path, "ncbi")

    def test_sequence_mapping_and_novelty(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # n1/u1 identical, n2/u2 cross-reference but sequence mismatch,
            # n3/u3/u4 ambiguous: only n1/u1 may enter the comparison.
            with gzip.open(root / "ncbi.faa.gz", "wt") as out:
                out.write(">n1 protein\nMAAAA\n>n2 mismatch\nMCCCC\n>n3 ambiguous\nMGGGG\n")
            (root / "uniprot.tsv").write_text(
                "Entry\tRefSeq\tSequence\tGene Names (primary)\tProtein names\n"
                "u1\tn1;\tMAAAA\tgene\tprotein\n"
                "u2\tn2;\tMTTTT\tgene\tprotein\n"
                "u3\tn3;\tMGGGG\tgene\tprotein\n"
                "u4\tn3;\tMGGGG\tgene\tprotein\n"
            )
            with gzip.open(root / "ncbi.gaf.gz", "wt") as out:
                out.write(
                    "!gaf-version: 2.2\n"
                    + gaf("n1", "GO:0000001")
                    + gaf("n1", "GO:0000003")
                    + gaf("n1", "GO:0000004")
                    + gaf("n1", "GO:9999999")
                    + gaf("n2", "GO:0000001")
                    + gaf("n3", "GO:0000001")
                )
            (root / "uniprot.gaf").write_text(
                "!gaf-version: 2.2\n" + gaf("u1", "GO:0000002") + gaf("u1", "GO:0000003", "NOT|enables")
            )
            (root / "go-basic.obo").write_text(
                "format-version: 1.2\ndata-version: test\n\n"
                "[Term]\nid: GO:0000001\nname: parent\nnamespace: molecular_function\n\n"
                "[Term]\nid: GO:0000002\nname: child\nnamespace: molecular_function\nis_a: GO:0000001\n\n"
                "[Term]\nid: GO:0000003\nname: separate\nnamespace: molecular_function\n\n"
                "[Term]\nid: GO:0000004\nname: obsolete\nnamespace: molecular_function\nis_obsolete: true\n"
            )
            report = pilot.build(root, {})
            self.assertEqual(report["metrics"]["mapping"][0]["unambiguous_identical_pairs"], 1)
            with duckdb.connect(str(root / "comparison.duckdb"), read_only=True) as con:
                self.assertEqual(
                    con.execute("SELECT refseq,uniprot FROM protein_map").fetchall(), [("n1", "u1")]
                )
                candidates = dict(
                    con.execute("SELECT term,covered_by_uniprot_descendant FROM ncbi_candidates").fetchall()
                )
                self.assertEqual(candidates, {"GO:0000001": True, "GO:0000003": False})
                self.assertEqual(con.execute("SELECT count(*) FROM pairs").fetchone()[0], 3)
                # Negative assertions and empty symbols remain in raw data.
                self.assertEqual(
                    con.execute("SELECT count(*) FROM annotations WHERE qualifier='NOT|enables'").fetchone()[
                        0
                    ],
                    1,
                )


if __name__ == "__main__":
    unittest.main()

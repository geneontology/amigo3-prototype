import hashlib
import json
import shutil
from pathlib import Path

import pytest

from amigo3.build import build
from amigo3.sources import write_json

# Species as config/sources.yaml declares them; lineages come from a UniProt-shaped fixture.
SPECIES = {
    "human": {"label": "Human", "taxon": "NCBITaxon:9606"},
    "pombe": {"label": "Fission yeast", "taxon": "NCBITaxon:4896", "aliases": ["SCHPO"]},
    "worm": {"label": "C. elegans", "taxon": "NCBITaxon:6239"},
    "PSEPK": {"label": "P. putida KT2440", "taxon": "NCBITaxon:160488", "aliases": ["PSEPU"]},
}
TAXONOMY = Path(__file__).parent / "fixtures/uniprot-taxonomy.json"


@pytest.fixture(scope="session")
def reaction_data(built_data, tmp_path_factory):
    """Real Rhea quartet, with a second synthetic GO owner to test shared chemicals."""
    data = tmp_path_factory.mktemp("reactions") / "data"
    shutil.copytree(built_data, data)
    manifest = json.loads((data / "sources.json").read_text())
    go = data / manifest["go"]["path"]
    with go.open("a") as stream:
        stream.write(
            "\n[Term]\nid: GO:0047034\nname: 15-hydroxyicosatetraenoate dehydrogenase activity\nnamespace: molecular_function\nis_a: GO:0003674\n"
        )
    manifest["go"]["sha256"] = hashlib.sha256(go.read_bytes()).hexdigest()
    rdf = Path(__file__).parent / "fixtures/rhea.rdf"
    for identifier, kind, content in (
        ("rhea", "rhea_rdf", rdf.read_text()),
        (
            "rhea_go",
            "rhea_go",
            "RHEA_ID\tDIRECTION\tMASTER_ID\tID\n23260\tUN\t23260\tGO:0047034\n23261\tLR\t23260\tGO:0016301\n23260\tUN\t23260\tGO:0016301\n",
        ),
    ):
        path = data / (identifier + ".txt")
        path.write_text(content)
        manifest[identifier] = {
            "id": identifier,
            "kind": kind,
            "path": path.name,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "url": "https://example.org/" + identifier,
        }
    write_json(data / "sources.json", manifest)
    build(data, repository=data / "review_repo")
    return data


@pytest.fixture(scope="session")
def chemical_data(reaction_data, tmp_path_factory):
    data = tmp_path_factory.mktemp("chemical-pages") / "data"
    shutil.copytree(reaction_data, data)
    repository = data / "review_repo"
    model_path = repository / "gocams/test/test-src.yaml"
    model = json.loads(model_path.read_text())
    model.setdefault("molecules", []).extend(
        [
            {"id": "gomodel:test/nad", "term": "CHEBI:57540"},
            {"id": "gomodel:test/other", "term": "CHEBI:999999"},
        ]
    )
    model.setdefault("objects", []).extend(
        [
            {"id": "CHEBI:57540", "label": "Model NAD label"},
            {"id": "CHEBI:999999", "label": "Model-only chemical"},
            {"id": "CHEBI:888888", "label": "Unused vocabulary entry"},
        ]
    )
    model["activities"][0].setdefault("molecular_associations", []).append(
        {"predicate": "RO:0002233", "molecule": "gomodel:test/nad"}
    )
    model_path.write_text(json.dumps(model))
    module_path = repository / "modules/test.yaml"
    module = json.loads(module_path.read_text())
    module["module"].setdefault("concepts", []).append({"term": {"id": "CHEBI:57540", "label": "NAD"}})
    module_path.write_text(json.dumps(module))
    build(data, repository=repository)
    return data


@pytest.fixture(scope="session")
def megamodel_documents():
    def activity(model, name, gene, chemical=None, predicate=None):
        a = {
            "id": f"gomodel:{model}/{name}",
            "enabled_by": {"term": gene},
            "molecular_function": {"term": "GO:0004672"},
            "part_of": {"term": "GO:0008150"},
            "occurs_in": {"term": "GO:0005829"},
        }
        if chemical:
            a["molecular_associations"] = [{"predicate": predicate, "molecule": chemical}]
        return a

    docs = []
    for name, title in [("mega-a", "Seed pathway"), ("mega-b", "Neighbor pathway")]:
        first = activity(name, "a", "UniProtKB:P00001", "CHEBI:123", "RO:0002234")
        last = activity(name, "b", "UniProtKB:P00002", "CHEBI:123", "RO:0002233")
        if name == "mega-b":
            first["enabled_by"]["term"], last["enabled_by"]["term"] = (
                last["enabled_by"]["term"],
                first["enabled_by"]["term"],
            )
        first["causal_associations"] = [
            {
                "predicate": "RO:0002629",
                "downstream_activity": last["id"],
                "evidence": [{"reference": "PMID:123"}],
            }
        ]
        docs.append(
            {
                "id": "gomodel:" + name,
                "title": title,
                "taxon": "NCBITaxon:9606",
                "status": "production",
                "activities": [first, last],
                "objects": [
                    {"id": "UniProtKB:P00001", "label": "GENE1"},
                    {"id": "UniProtKB:P00002", "label": "GENE2"},
                    {"id": "GO:0004672", "label": "protein kinase activity"},
                    {"id": "GO:0005829", "label": "cytosol"},
                ],
            }
        )
    return docs


@pytest.fixture(scope="session")
def megamodel_data(built_data, megamodel_documents, tmp_path_factory):
    data = tmp_path_factory.mktemp("megamodels") / "data"
    shutil.copytree(built_data, data)
    repository = data / "review_repo"
    worm = {
        "id": "gomodel:worm-test",
        "title": "Worm isolated model",
        "taxon": "NCBITaxon:6239",
        "status": "production",
        "activities": [
            {
                "id": "gomodel:worm-test/a",
                "enabled_by": {"term": "WB:WBGene00000001"},
                "molecular_function": {"term": "GO:0004672"},
            }
        ],
    }
    for document in [*megamodel_documents, worm]:
        local = document["id"].split(":")[1]
        path = repository / "gocams" / local / f"{local}-src.yaml"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(document))
    build(data, repository / "genes", repository)
    return data


def gaf(
    subject,
    symbol,
    term,
    evidence="IDA",
    relation="enables",
    form="",
    extensions="",
    with_from="",
    species="human",
    reference="PMID:123",
):
    db, local = subject.split(":", 1)
    fields = [
        db,
        local,
        symbol,
        relation,
        term,
        reference,
        evidence,
        with_from,
        "F",
        symbol + " protein",
        "",
        "protein",
        "taxon:9606" if species == "human" else "NCBITaxon:4896",
        "20260101",
        "TestDB",
        extensions,
        form,
    ]
    return "\t".join(fields)


def gpi(subject, symbol, xrefs="", canonical=None, taxon="NCBITaxon:9606", object_type="PR:000000001"):
    return "\t".join(
        [
            subject,
            symbol,
            symbol + " protein",
            "alias",
            object_type,
            taxon,
            "",
            canonical or subject,
            "",
            xrefs,
            "",
        ]
    )


@pytest.fixture(scope="session")
def built_data(tmp_path_factory):
    data = tmp_path_factory.mktemp("data")
    sources = {
        "human_gaf": (
            "gaf",
            "human",
            "!gaf-version: 2.2\n"
            + "\n".join(
                [
                    gaf("UniProtKB:P00001", "GENE1", "GO:0004672"),
                    gaf(
                        "UniProtKB:P00001",
                        "GENE1",
                        "GO:0003674",
                        form="UniProtKB:P00001-2",
                        extensions="occurs_in(CL:1)|occurs_in(CL:2),part_of(GO:1)",
                        with_from="UniProtKB:A|UniProtKB:B,UniProtKB:C",
                    ),
                    gaf("UniProtKB:P00002", "GENE2", "GO:0004672", relation="NOT|enables"),
                ]
            )
            + "\n",
        ),
        "pombe_gaf": (
            "gaf",
            "pombe",
            "!gaf-version: 2.2\n"
            + gaf(
                "PomBase:SPTEST.1",
                "test1",
                "GO:0016301",
                evidence="IEA",
                species="pombe",
                reference="GO_REF:0000002",
            )
            + "\n",
        ),
        "human_gpi": (
            "gpi",
            "human",
            "!gpi-version: 2.0\n"
            + "\n".join(
                [
                    gpi(
                        "UniProtKB:P00001",
                        "GENE1",
                        "HGNC:HGNC:1|Ensembl:ENSG00000000001.3|RefSeq:NP_000001.1|UniProtKB:P00001-PRO_0000000001",
                    ),
                    gpi("UniProtKB:P00001-2", "GENE1", "", canonical="UniProtKB:P00001"),
                    gpi("UniProtKB:P00002", "GENE2", "HGNC:2"),
                    gpi("RNAcentral:UNANNOTATED_9606", "unannotated"),
                ]
            )
            + "\n",
        ),
        "pombe_gpi": (
            "gpi",
            "pombe",
            "!gpi-version: 2.0\n"
            + gpi("PomBase:SPTEST.1", "test1", "UniProtKB:Q00001", taxon="NCBITaxon:4896")
            + "\n",
        ),
        "hgnc": (
            "hgnc",
            None,
            "hgnc_id\tsymbol\tname\tstatus\tuniprot_ids\tensembl_gene_id\talias_symbol\tprev_symbol\tentrez_id\nHGNC:1\tGENE1\tGene one\tApproved\tP00001\tENSG00000000001\tOLD1\t\t1\nHGNC:2\tGENE2\tGene two\tApproved\tP00002\tENSG00000000002\t\t\t2\nHGNC:3\tGENE3\tGene without annotations\tApproved\t\t\t\t\t3\n",
        ),
        "eco_mapping": ("eco_mapping", None, "IDA\tDefault\tECO:0000314\nIEA\tDefault\tECO:0000501\n"),
        "go": (
            "obo",
            None,
            """format-version: 1.2
data-version: test-release

[Term]
id: GO:0003674
name: molecular function
namespace: molecular_function

[Term]
id: GO:0016301
alt_id: GO:9916301
name: kinase activity
subset: goslim_generic
namespace: molecular_function
is_a: GO:0003674 ! molecular function

[Term]
id: GO:0004672
name: protein kinase activity
namespace: molecular_function
is_a: GO:0016301 ! kinase activity
relationship: regulates GO:9999999 ! must not enter closure

[Term]
id: GO:9999999
name: regulation target
namespace: molecular_function
""",
        ),
    }
    manifest = {}
    for source_id, (kind, species, content) in sources.items():
        path = data / f"{source_id}.txt"
        path.write_text(content)
        manifest[source_id] = {
            "id": source_id,
            "kind": kind,
            "species": species,
            "path": path.name,
            "url": f"https://example.org/{source_id}",
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    shutil.copyfile(TAXONOMY, data / "taxonomy.json")
    manifest["taxonomy"] = {
        "id": "taxonomy",
        "kind": "uniprot_taxonomy",
        "loaded_species": SPECIES,
        "path": "taxonomy.json",
        "url": "https://example.org/taxonomy",
        "sha256": hashlib.sha256(TAXONOMY.read_bytes()).hexdigest(),
    }
    write_json(data / "sources.json", manifest)
    reviews = data / "review_repo" / "genes"
    review_path = reviews / "human" / "GENE1" / "GENE1-ai-review.yaml"
    review_path.parent.mkdir(parents=True)
    review_path.write_text(
        json.dumps(
            {
                "id": "P00001",
                "gene_symbol": "GENE1",
                "description": "Frozen review summary.",
                "existing_annotations": [
                    {
                        "term": {"id": "GO:0004672"},
                        "evidence_type": "IDA",
                        "original_reference_id": "PMID:123",
                        "review": {"action": "ACCEPT", "summary": "Kinase activity supported."},
                    },
                    {
                        "term": {"id": "GO:0004672"},
                        "evidence_type": "IDA",
                        "original_reference_id": "PMID:999",
                        "review": {"action": "REMOVE", "reason": "A different reference."},
                    },
                    {
                        "term": {"id": "GO:0003674"},
                        "evidence_type": "IDA",
                        "original_reference_id": "PMID:123",
                        "isoform": "P00001-2",
                        "review": {"action": "MODIFY", "reason": "More specific term needed."},
                    },
                    {
                        "term": {"id": "GO:0004672"},
                        "evidence_type": "IDA",
                        "original_reference_id": "PMID:123",
                        "review": {"action": "NEW", "summary": "A proposal stays separate."},
                    },
                ],
            }
        )
    )
    repository = reviews.parent
    docs = {
        "gocams/test/test-src.yaml": {
            "id": "gomodel:test",
            "title": "Test causal model",
            "taxon": "NCBITaxon:9606",
            "status": "production",
            "activities": [
                {
                    "id": "gomodel:test/a",
                    "enabled_by": {"term": "UniProtKB:P00001"},
                    "molecular_function": {"term": "GO:0004672"},
                    "molecular_associations": [{"predicate": "RO:0002233", "molecule": "gomodel:test/input"}],
                }
            ],
            "molecules": [{"id": "gomodel:test/input", "term": "UniProtKB:P00002"}],
            "objects": [{"id": "UniProtKB:P00001", "label": "GENE1"}],
        },
        # GoCamReview shape from ai-gene-review: findings are keyed by activity id.
        "gocams/test/test-review.yaml": {
            "model": "gomodel:test",
            "title": "Test causal model",
            "status": "COMPLETE",
            "summary": "A one-step kinase model.",
            "notes": "Model-level reviewer note.",
            "activity_reviews": [
                {
                    "activity_id": "gomodel:test/a",
                    "gene_product": "UniProtKB:P00001",
                    "molecular_function": "GO:0004672",
                    "verdict": "UNCERTAIN",
                    "consistency": "CONSISTENT",
                    "qc_flags": ["DIRECT_VS_INDIRECT_CAUSAL", "MISSING_LOCATION"],
                    "gene_review": "file:human/GENE1/GENE1-ai-review.yaml",
                    "notes": "Kinase typing matches the gene review; the edge is likely indirect.",
                    "evidence": [
                        {
                            "source_id": "PMID:123",
                            "statement": "GENE1 phosphorylates its target.",
                            "supporting_text": "Synthetic phosphorylation evidence.",
                        }
                    ],
                },
                {
                    "activity_id": "gomodel:test/retired",
                    "gene_product": "UniProtKB:P00002",
                    "verdict": "WRONG",
                    "consistency": "NO_GENE_REVIEW",
                    "qc_flags": ["BINDING_AS_FUNCTION"],
                    "notes": "Binding is not a function.",
                },
            ],
            "references": [{"id": "PMID:123", "title": "Synthetic kinase paper"}],
        },
        "modules/test.yaml": {
            "id": "MODULE:test",
            "title": "Test module",
            "status": "DRAFT",
            "description": "Alternative routes.",
            "module": {
                "id": "root",
                "label": "Root",
                "module_type": "PATHWAY",
                "concepts": [{"term": {"id": "GO:0016301", "label": "kinase activity"}}],
                "gocam_associations": [{"model": "gomodel:test"}],
                "variant_sets": [
                    {
                        "id": "route",
                        "selection": "ONE",
                        "axis": "taxon",
                        "variants": [
                            {
                                "id": "a",
                                "label": "Route A",
                                "module_type": "REACTION",
                                "annotons": [
                                    {
                                        "id": "a1",
                                        "participant": {
                                            "family": {
                                                "term": {"id": "PANTHER:PTHR00001:SF1"},
                                                "representative_members": [
                                                    {"term": {"id": "UniProtKB:P00001"}}
                                                ],
                                            }
                                        },
                                    }
                                ],
                            },
                            {"id": "b", "label": "Route B", "module_type": "REACTION"},
                        ],
                    }
                ],
            },
        },
        "interpro/panther/PTHR00001/PTHR00001-metadata.yaml": {
            "metadata": {"name": {"name": "OFFICIAL FAMILY NAME"}, "counters": {"proteins": 1000}}
        },
        "interpro/panther/PTHR00001/PTHR00001-review.yaml": {
            "family_id": "PANTHER:PTHR00001",
            "family_name": "OFFICIAL FAMILY NAME",
            "summary": "Not every member has the same function.",
            "review_status": "COMPLETE",
            "functional_coherence": "HETEROGENEOUS",
        },
    }
    for name, document in docs.items():
        path = repository / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(document))
    family = repository / "interpro/panther/PTHR00001"
    (family / "PTHR00001-entries.csv").write_bytes(
        b"id,name,source_tax_id,source_tax_name,gene,subfamily,subfamily_name\r\nP00001,Protein one,9606,Human,GENE1,PTHR00001:SF1,OFFICIAL SUBFAMILY\r\nQ99999,Other protein,12345,Other species,OTHER,PTHR00001:SF2,OTHER SUBFAMILY\r\n"
    )
    (family / "PTHR00001-paint.tsv").write_text(
        "family\tnode\tgo_id\tevidence\tnegated\tseeds\nPTHR00001\tPTN123\tGO:0004672\tIBD\ttrue\tUniProtKB:P00001\n"
    )
    report = repository / "genes/human/GENE1/GENE1-deep-research-openscientist.md"
    report.write_text("""---
provider: openscientist
model: test-model
start_time: '2026-10-01T12:00:00'
template_variables:
  uniprot_accession: P00001
  organism: human
artifacts:
  - path: evidence.csv
  - path: missing.pdf
  - path: ../../../../secret.txt
---
## Question
Prompt-only-token: investigate GENE1.
## Output
# GENE1 research
A distinctiveflavorkinase result, with [evidence](evidence.csv).

| Evidence | Judgment |
| --- | --- |
| Experiment | Supported |

<script>alert('unsafe')</script>
[bad](javascript:alert(1))
![external](https://example.org/tracking.png)
""")
    (report.parent / "evidence.csv").write_bytes(b"gene,result\r\nGENE1,yes\r\n")
    (report.parent / (report.name + ".citations.md")).write_text(
        "# References\n[PubMed](https://pubmed.ncbi.nlm.nih.gov/123/)"
    )
    (repository / "modules/test-deep-research-manual.md").write_text(
        "# Module research\nAlternative routes studied."
    )
    hypothesis = report.parent / "GENE1-hypotheses/mechanism/openscientist.md"
    hypothesis.parent.mkdir(parents=True)
    hypothesis.write_text("# Focused mechanism\nHypothesis with no frontmatter.")
    (hypothesis.parent / "prompt.md").write_text("This is not a report")
    projects = repository / "projects"
    projects.mkdir()
    (projects / "TEST.md").write_text("""---
title: Test research project
maturity: IN_PROGRESS
tags: [BIOLOGY_DOMAIN]
species: [human]
genes: [GENE1, not_a_gene]
related_projects: [OTHER.md]
created: 2025-01-15
owners: [Ada Lovelace, {name: Grace Hopper, orcid: "0000-0000"}]
manifest:
  slides:
    - href: TEST/slides.html
      title: Slides
---
# Test research project
A project-specific searchablephrase.

[Gene](../genes/human/GENE1/GENE1-ai-review.yaml)
[Module](../modules/test.yaml)
[Research](../genes/human/GENE1/GENE1-deep-research-openscientist.md)
[Details](#work-plan)
[Outside](../../secret.txt)
[index.html](TEST/results/run1/index.html)
GO:0016301 PANTHER:PTHR00001 gomodel:test

| Gene | UniProt |
| --- | --- |
| GENE2 | P00002 |

## Work plan
- [ ] Follow up
<script>alert('unsafe')</script>
""")
    (projects / "OTHER.md").write_text("# Another project\nRelated work.")
    (projects / "README.md").write_text("Not a project")
    (projects / "TEST").mkdir()
    (projects / "TEST/slides.html").write_bytes(b"<html><script>untrusted()</script></html>")
    (projects / "TEST/notes.md").write_text("Not a standalone project")
    (projects / "TEST/results/run1").mkdir(parents=True)
    (projects / "TEST/results/run1/index.html").write_text("<html>Run 1</html>")
    from rule_fixtures import write_rules

    write_rules(repository)
    build(data, reviews, repository)
    return data


@pytest.fixture(scope="session")
def hierarchy_data(multispecies_data, tmp_path_factory):
    """Two slim parents of kinase activity, exercising real DAG queries and UI."""
    import shutil

    data = tmp_path_factory.mktemp("hierarchies")
    manifest = json.loads((multispecies_data / "sources.json").read_text())
    for source in manifest.values():
        shutil.copyfile(multispecies_data / source["path"], data / source["path"])
    path = data / manifest["go"]["path"]
    path.write_text(
        path.read_text().replace(
            "is_a: GO:0003674 ! molecular function",
            "is_a: GO:9000001\nis_a: GO:9000002",
        )
        + """
[Term]
id: GO:9000001
name: branch one
subset: goslim_generic
is_a: GO:0003674

[Term]
id: GO:9000002
name: branch two
subset: goslim_generic
is_a: GO:0003674
"""
    )
    manifest["go"]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    (data / "sources.json").write_text(json.dumps(manifest))
    build(data)
    return data


@pytest.fixture(scope="session")
def multispecies_data(built_data, tmp_path_factory):
    """A MOD with dual IDs and a GAF-only bacterium, without live downloads."""
    import shutil

    data = tmp_path_factory.mktemp("multispecies")
    manifest = json.loads((built_data / "sources.json").read_text())
    for source in manifest.values():
        shutil.copyfile(built_data / source["path"], data / source["path"])
    for source_id, kind, species, taxon, content in [
        (
            "worm_gaf",
            "gaf",
            "worm",
            "6239",
            "!gaf-version: 2.2\n"
            + gaf("WB:WBGene00000001", "aap-1", "GO:0004672").replace("taxon:9606", "taxon:6239")
            + "\n",
        ),
        (
            "worm_gpi",
            "gpi",
            "worm",
            "6239",
            "!gpi-version: 2.0\n"
            + gpi("WB:WBGene00000001", "aap-1", "UniProtKB:G5EDP9", taxon="NCBITaxon:6239")
            + "\n"
            + gpi("WB:WBGene00000001", "aap-1", "UniProtKB:QWORM2", taxon="NCBITaxon:6239")
            + "\n",
        ),
        (
            "putida_gaf",
            "gaf",
            "PSEPK",
            "160488",
            "!gaf-version: 2.2\n"
            + gaf("UniProtKB:Q88RL4", "katE", "GO:0004672").replace("taxon:9606", "taxon:160488")
            + "\n",
        ),
    ]:
        path = data / (source_id + ".txt")
        path.write_text(content)
        manifest[source_id] = {
            "id": source_id,
            "kind": kind,
            "species": species,
            "taxon": "NCBITaxon:" + taxon,
            "path": path.name,
            "sha256": hashlib.sha256(content.encode()).hexdigest(),
        }
    write_json(data / "sources.json", manifest)
    reviews = data / "repo/genes"
    for org, symbol, accession in [("worm", "aap-1", "G5EDP9"), ("PSEPK", "katE", "Q88RL4")]:
        folder = reviews / org / symbol
        folder.mkdir(parents=True)
        write_json(
            folder / (symbol + "-ai-review.yaml"),
            {
                "id": accession,
                "gene_symbol": symbol,
                "description": "Review for " + symbol,
                "existing_annotations": [
                    {
                        "term": {"id": "GO:0004672"},
                        "evidence_type": "IDA",
                        "original_reference_id": "PMID:123",
                        "review": {"action": "ACCEPT", "reason": "Fixture assessment."},
                    }
                ],
            },
        )
    build(data, reviews=reviews)
    return data


@pytest.fixture(scope="session")
def ontology_data(hierarchy_data, tmp_path_factory):
    """A mixed-relation diamond, a zero-count child, and regulatory context."""
    data = tmp_path_factory.mktemp("ontology-data")
    manifest = json.loads((hierarchy_data / "sources.json").read_text())
    for source in manifest.values():
        shutil.copyfile(hierarchy_data / source["path"], data / source["path"])
    path = data / manifest["go"]["path"]
    path.write_text(
        path.read_text().replace("is_a: GO:9000002", "relationship: BFO:0000050 GO:9000002")
        + """
[Term]
id: GO:9000003
name: empty branch
is_a: GO:0016301
"""
    )
    manifest["go"]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    (data / "sources.json").write_text(json.dumps(manifest))
    build(data)
    return data


@pytest.fixture(scope="session")
def product_types_data(built_data, tmp_path_factory):
    data = tmp_path_factory.mktemp("product-types") / "data"
    shutil.copytree(built_data, data)
    # A MOD gene with an RNA product and a second type mapped to an HGNC gene.
    with (data / "pombe_gpi.txt").open("a") as stream:
        stream.write(gpi("PomBase:TRNA1", "trna1", taxon="NCBITaxon:4896", object_type="SO:0000253") + "\n")
    with (data / "human_gpi.txt").open("a") as stream:
        stream.write(gpi("RNAcentral:RNA1_9606", "RNA1", "HGNC:1", object_type="SO:0000276") + "\n")
        # Complexes must remain distinct even with an HGNC cross-reference.
        stream.write(gpi("ComplexPortal:CPX-1", "complex1", "HGNC:1", object_type="GO:0032991") + "\n")
    # GAF-only complexes also get a biological type, with no namespace guessing.
    fields = gaf("TestDB:ASSEMBLY1", "assembly1", "GO:0004672").split("\t")
    fields[11] = "protein_complex"
    with (data / "human_gaf.txt").open("a") as stream:
        stream.write("\t".join(fields) + "\n")
    manifest = json.loads((data / "sources.json").read_text())
    for source in manifest.values():
        source["sha256"] = hashlib.sha256((data / source["path"]).read_bytes()).hexdigest()
    write_json(data / "sources.json", manifest)
    build(data)
    return data


@pytest.fixture(scope="session")
def product_hierarchy_data(product_types_data, tmp_path_factory):
    data = tmp_path_factory.mktemp("product-hierarchy") / "data"
    shutil.copytree(product_types_data, data)
    with (data / "human_gpi.txt").open("a") as stream:
        stream.write(gpi("RNAcentral:LONG_9606", "LONG", "HGNC:1", object_type="SO:0001463") + "\n")
    with (data / "pombe_gpi.txt").open("a") as stream:
        for identifier, symbol, kind in [
            ("PomBase:NCRNA", "nc1", "SO:0000655"),
            ("PomBase:LONGRNA", "long1", "SO:0001904"),
        ]:
            stream.write(gpi(identifier, symbol, taxon="NCBITaxon:4896", object_type=kind) + "\n")
    manifest = json.loads((data / "sources.json").read_text())
    for source in manifest.values():
        source["sha256"] = hashlib.sha256((data / source["path"]).read_bytes()).hexdigest()
    write_json(data / "sources.json", manifest)
    build(data)
    return data


@pytest.fixture(scope="session")
def biology_data(chemical_data, tmp_path_factory):
    """Pinned real NAD/proton depictions and T/CD4-cell differentiation axioms."""
    data = tmp_path_factory.mktemp("biology-pages") / "data"
    shutil.copytree(chemical_data, data)
    manifest = json.loads((data / "sources.json").read_text())
    go = data / manifest["go"]["path"]
    with go.open("a") as stream:
        for identifier, name, parent in (
            ("GO:0030154", "cell differentiation", "GO:0008150"),
            ("GO:0030217", "T cell differentiation", "GO:0030154"),
            ("GO:0043367", "CD4-positive, alpha-beta T cell differentiation", "GO:0030217"),
        ):
            stream.write(
                f"\n[Term]\nid: {identifier}\nname: {name}\nnamespace: biological_process\nis_a: {parent}\n"
            )
    manifest["go"]["sha256"] = hashlib.sha256(go.read_bytes()).hexdigest()
    for identifier, kind, filename in (
        ("rhea_structures", "rhea_sdf", "chemical-structures.sdf"),
        ("go_plus", "go_plus_json", "go-differentiation.json"),
        ("cl", "cl_json", "cell-types.json"),
    ):
        source = Path(__file__).parent / "fixtures" / filename
        path = data / filename
        shutil.copyfile(source, path)
        manifest[identifier] = {
            "id": identifier,
            "kind": kind,
            "path": path.name,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "url": "https://example.org/" + filename,
        }
    write_json(data / "sources.json", manifest)
    build(data, repository=data / "review_repo")
    return data

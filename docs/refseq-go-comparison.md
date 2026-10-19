# RefSeq prokaryotic GO versus UniProt-GOA: KT2440 pilot

This experiment downloads NCBI's assembly-level GO annotations and compares them
with the same UniProt-GOA proteome file configured for AmiGO 3. It produces a
standalone DuckDB database; it does not add or replace application annotations.

## Pilot result (downloaded 2026-10-18)

The snapshots contain 8,263 NCBI annotation rows on 3,415 proteins and 25,487
UniProt-GOA rows on 3,901 proteins. The sequence catalogs contain 5,448 and 5,529
proteins respectively. **4,501 unambiguous identical-sequence pairs** matched;
all 4,501 also have explicit versioned RefSeq cross-references in UniProt.
One RefSeq protein matches two UniProt entries and is excluded as ambiguous.
Of the GAF subjects, 2,921 NCBI and 3,328 UniProt proteins enter that matched
universe. Thus the results below do not cover the full input proteomes.

After alternative-ID normalization and excluding obsolete/unknown terms:

| Distinct protein–GO pairs on matched proteins | Count |
| --- | ---: |
| Shared | 4,823 |
| NCBI only | 2,165 |
| UniProt only | 10,278 |
| NCBI total | 6,988 |
| UniProt total | 15,101 |

Of the 2,165 NCBI-only pairs, 262 are covered by a more specific UniProt term.
The remaining **1,903 pairs span 1,248 proteins and 511 distinct GO terms**.
Of these, 368 have a broader UniProt term; 1,535 have neither an ancestor nor a
descendant match. These two categories' protein counts overlap.
CDD architecture IDs support 1,220 of the 1,903 pairs, HMMs support 665, and
other/empty identifiers account for 18. This is evidence of complementary
annotations in these downloads, not validation of their correctness.
NCBI supplies active positive annotations for **248 matched proteins with no
qualifying UniProt annotation**.

| Aspect | Shared | NCBI only | UniProt only |
| --- | ---: | ---: | ---: |
| Molecular function | 2,684 | 1,214 | 4,621 |
| Biological process | 1,534 | 656 | 3,130 |
| Cellular component | 605 | 295 | 2,527 |

Comparing qualifier, extension, and form ID as well as term gives 4,737 shared,
2,251 NCBI-only, and 10,365 UniProt-only assertions. The primary table is explicitly
a term-coverage comparison, not full annotation equivalence.

Examples for review (identical sequence and explicit accession cross-reference):

- **rmf / A0A140FW57 / WP_003248687.1:** NCBI adds ribosome binding
  (GO:0043022) and cytosolic ribosome hibernation (GO:0141014), supported by
  `HMM:NF011162.1`. UniProt has regulation of translation (GO:0006417) and
  cytoplasm (GO:0005737). This illustrates a candidate gain in specificity.
- **hglS / Q88CC1 / WP_010955773.1:** NCBI supplies dioxygenase activity
  (GO:0051213) via `ArchId:11611460`, while UniProt supplies oxidoreductase
  activity (GO:0016491) via InterPro. This is an architecture-derived candidate
  refinement.
- **secG / A0A140FWQ9 / WP_020193781.1:** NCBI names the protein preprotein
  translocase subunit SecG but assigns sialic acid transport/activity
  (GO:0015739, GO:0015136) via `HMM:TIGR00810.1`. UniProt instead includes
  protein secretion and protein-transporting ATPase activity (GO:0015450). Neither
  provider is treated as a gold standard. This apparent
  inconsistency is a concrete mapping audit candidate, not a safe automatic addition.

Release caveats: NCBI's GAF was generated **2025-06-12**, using GO **2025-03-16**;
UniProt's was generated **2026-10-11**, citing GO **2026-09-27**. The REST sequence
catalog reports UniProt release **2026_03**. The downloaded GO-basic used for
normalization/ancestry reports **2026-07-26**, older than the UniProt GAF's cited
ontology. There were no unknown GO IDs in either input against that GO-basic,
but ancestry can still differ between releases. NCBI has 121 obsolete-term rows,
which are retained in raw data and excluded from the primary comparison.
Neither input has NOT rows. NCBI has 5,296 rows with empty symbols, which are
retained; one input row has a nonempty extension or form ID. These mismatched
release dates prevent interpreting differences as a controlled quality benchmark.

The [saved machine-readable summary](refseq-go-pilot-summary.json) records these
metrics, headers, source URLs, and checksums. The full database and row-level
exports remain local. Offline integration tests verify sequence ambiguity,
sequence mismatches despite cross-references, taxon rejection, negation, obsolete
and unknown IDs, and ancestor coverage. Real-data pair totals were also checked
independently with Python sets.

## Downloads and reproduction

```bash
uv run scripts/compare_refseq_go.py
# Deliberately advance all upstream snapshots:
uv run scripts/compare_refseq_go.py --refresh
# Query the completed local database:
duckdb data/refseq-pilot/comparison.duckdb
```

The script has its own small dependency declaration (DuckDB and Arrow); it does not need
the application environment or Rust build. It reuses inputs only when URL and
SHA-256 match the saved manifest. Each download records its URL, retrieval time,
HTTP release metadata when available, byte count, and SHA-256. The assembly
report and GAF headers retain taxonomy and annotation/ontology release context.
The assembly accession alone does **not** pin the annotation release: NCBI can
update the files at that URL without changing the assembly accession.

Inputs:

- [NCBI KT2440 assembly directory](https://ftp.ncbi.nlm.nih.gov/genomes/all/GCF/000/007/565/GCF_000007565.2_ASM756v2/):
  `GCF_000007565.2_ASM756v2_gene_ontology.gaf.gz`,
  `GCF_000007565.2_ASM756v2_protein.faa.gz`, and the assembly report.
- [UniProt-GOA KT2440 GAF](https://ftp.ebi.ac.uk/pub/databases/GO/goa/proteomes/109.P_putida_KT2440.goa).
  This is the URL in `config/sources.yaml`, fetched anew for this experiment;
  an existing app build may have older cached bytes.
- [UniProt REST API](https://rest.uniprot.org/uniprotkb/stream?query=organism_id%3A160488&format=tsv&fields=accession%2Cxref_refseq%2Csequence%2Cgene_primary%2Cprotein_name):
  protein sequences and RefSeq cross-references for taxon 160488.
- [GO-basic](https://current.geneontology.org/ontology/go-basic.obo): term names,
  alternative IDs, obsolete flags, and `is_a`/`part_of` ancestry.

For another organism, identify a versioned RefSeq assembly of the **same strain**
as the GOA proteome and check its directory for `*_gene_ontology.gaf.gz`.
Do not substitute NCBI's eukaryotic InterProScan pipeline or assume `gene2go`
represents all of these prokaryotic WP protein annotations. The present script is
intentionally fixed to KT2440 (NCBITaxon:160488) and rejects other GAF taxa.

## Comparison policy

1. Preserve all 17 GAF fields and source line numbers in `annotations`.
2. Match proteins by exact full sequence equality within the selected strain.
   Retain only one-to-one matches; report ambiguous mappings separately.
   RefSeq accession versions are preserved, and explicit UniProt cross-references
   are recorded as supporting evidence. Symbols are never mapping keys.
3. Use that matched protein universe for both providers, including proteins
   annotated by only one provider. Collapse positive annotations to distinct
   protein–GO pairs for the primary comparison. Evidence multiplicity is not
   counted as additional functional coverage.
4. Normalize GO alternative IDs; exclude obsolete and unknown IDs from the
   primary comparison, and report their raw row counts. Do not guess replacements
   for obsolete terms. Preserve NOT assertions but exclude them from positive
   coverage. A separate `relation_differences` view also compares qualifier,
   annotation extension, and gene product form ID.
5. For each NCBI-only pair, check whether UniProt has a descendant of that term
   or an ancestor of it. The reflexive `is_a`/`part_of` closure excludes regulation
   edges. This is a diagnostic of term specificity, **not** a claim that all
   qualifiers/relations support identical logical propagation, nor evidence of
   annotation correctness.

These are sequence-level comparisons, not genome-locus counts. WP proteins may
represent identical sequences at multiple loci or in other organisms; assembly
membership and strain scope come from the selected downloads. Unmapped and
ambiguous proteins are excluded from pair comparisons, not classified as missing
annotations. The experiment does not align differing sequences or resolve old
accessions using gene names.

## Outputs and inspection

All raw inputs and outputs are under `data/refseq-pilot/` (git-ignored).
`summary.json` includes metrics, GAF headers, and the source manifest.
`comparison.duckdb` contains raw annotations, protein catalogs, sequence mapping,
GO terms/ancestry, and comparison tables/views. `differences.tsv` lists all pair
results; `ncbi_candidates.tsv` annotates NCBI-only pairs with names and ancestry
checks; `candidate_evidence.tsv` retains the NCBI evidence and raw line numbers.

```sql
SELECT status, count(*) FROM differences GROUP BY status;
SELECT * FROM ncbi_candidates
WHERE NOT covered_by_uniprot_descendant ORDER BY protein, term;
SELECT source, accession, term, qualifier, evidence, reference, with_from
FROM matched WHERE protein = 'Q88CC1' ORDER BY source, term;
```

## Why CDD could add information

The [February 2022 announcement](https://ncbiinsights.ncbi.nlm.nih.gov/2022/02/24/gene-ontology-refseq/)
described transfer from protein-family models: Pfam mappings, TIGRFAMs,
NCBIFAMs, and BlastRules. The
[May 2023 update](https://ncbiinsights.ncbi.nlm.nih.gov/2023/05/10/go-terms-100m-refseq-records/)
reported over 100 million annotated prokaryotic proteins after adding CDD
architecture propagation in March 2023. NCBI's
[PGAP documentation](https://www.ncbi.nlm.nih.gov/refseq/annotation_prok/)
describes architecture-based rules alongside HMM and BLAST-based families.
Shared membership in InterPro does not establish equivalence between these rules
and the InterPro-to-GO annotations delivered by UniProt. The downloaded GAF's
`with/from` identifiers let us measure that difference directly.

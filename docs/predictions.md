# Preloaded external annotations

The **Predictions** navigation link opens `/predictions`. Choose a preloaded set
to see mapping coverage, overlap, candidate refinements and unmatched terms against
the server's frozen annotation build. Filter by comparison, GO aspect, protein,
term or model identifier; inspect the original GAF row and matching baseline evidence.
Download filtered rows as TSV, the full summary as JSON, or the frozen source files.

The sidebar facets cover comparison category, GO aspect, protein mapping, model
identifier type and evidence code. Select several values within a facet to combine
them with OR; separate facets combine with AND. Counts exclude the facet's own
selection, so alternative categories remain available while drilling down. They
count source annotation rows, not distinct pairs. Summary cards show whole-set
pair counts and can be clicked to filter the corresponding source rows. Active
filters are removable chips and are preserved in the URL and TSV export.
Model types distinguish HMMs, CDD architectures (`ArchId`), BlastRules (`NBR…`)
and cluster identifiers; unfamiliar identifiers remain visible as unclassified.

Gene pages have a **Predictions** tab, including a count, category selection,
original evidence, baseline matches and gene-scoped exports. **Explore with facets**
opens the full comparison page with an exact gene-link filter. This collects the
gene's linked protein claims for navigation; each comparison still uses only the
specific mapped protein's annotations, including isoform boundaries.

## Load the NCBI KT2440 example

After the normal `amigo3 fetch`, prepare the pilot inputs and opt into the preload:

```bash
uv run scripts/compare_refseq_go.py
uv run amigo3 build --external-sets config/external-sets.yaml
uv run amigo3 serve --backend duckdb
```

The pilot downloads assembly `GCF_000007565.2`, maps RefSeq proteins to UniProt
using identical full-length sequences, and writes the GAF, mapping, subject catalog
and provenance under `data/refseq-pilot/preload/`. It retains all candidate mappings
so ambiguous proteins are reported rather than expanded into multiple claims.
See the [pilot report](refseq-go-comparison.md) for the independent analysis.

The build compares against its configured `putida_gaf` baseline and GO ontology,
which may differ in date from the pilot downloads. Source headers, hashes and
ontology release are visible under **Sources, dates and mapping coverage**.

`--external-sets` can be combined with `--repository /path/to/ai-gene-review`.
Supply the option on each build that should include predictions. Builds without
it show an empty predictions page. Solr deployments use the same frozen DuckDB
comparison tables; publish/restart the app as usual when switching builds.

## Configure another GAF set

Use `config/external-sets.yaml` as the template. Each set requires a unique slug
`id`, `label`, `species`, `taxon`, `baseline_sources`, `gaf` and `subjects`.
Paths are relative to the configuration file. `mapping`, `provenance`, `gene_xrefs`, `method`,
`description` and assembly metadata are optional.

- **GAF:** GAF 2.2, optionally gzip compressed, with one configured subject taxon.
  Empty symbols and duplicate evidence rows are retained. Product-form IDs in
  column 17 take precedence over the canonical ID.
- **Subjects:** TSV with `subject_id` and `taxon_id`, defining the target protein
  catalog independently of which proteins have annotations. Use prefixed IDs.
- **Mapping:** TSV with `source_id`, `target_id`, `taxon_id`, `method`. Multiple
  distinct targets make a source ambiguous. Multiple methods for the same target
  are preserved. Exact identifiers already in the target catalog can map directly.
  No symbol matching, accession-version stripping or gene-level pooling occurs.
- **Provenance:** optional JSON documenting releases, mapping procedure, input
  hashes and other source context. Preserve accession versions in mapping inputs.
- **Gene cross-references:** optional TSV with `subject_id`, `taxon_id`, `gene_id`
  (prefixed `NCBIGene:`), `gene_taxon_id`, `gene_label`, `organism` and `source`.
  The subject must belong to the configured target catalog and taxon. The Gene ID
  taxon is recorded independently and shown as same taxon, different taxon or
  unverified. These are navigation references, never protein-mapping evidence.
- **Baseline sources:** source IDs from `sources.json`, restricted to the configured
  taxon. If a source is missing or incompatible, rows remain browsable with
  `baseline_unavailable` status instead of being reported as novel annotations.

Source bytes are copied and checked during the build. Their hashes participate in
the build identity; changing an input creates a new build. Releases include the
frozen artifacts and separate `external_sets.parquet` / `external_claims.parquet`.
External rows do not change published annotation counts or enrichment inputs.

## Interpret the counts

Summary categories use distinct mapped protein–GO pairs. The results table and
filter counts use original annotation rows, so repeated evidence remains inspectable.
The comparison cohort contains proteins with mapped, active positive external GO
claims; baseline-only counts use that same cohort. An empty GAF establishes zero
supplied rows, not a complete set of evaluated proteins.

Positive active terms are classified in priority order: exact term, covered by a
more specific baseline term, candidate refinement of a broader baseline term,
then other unmatched term. Specificity uses **is_a only**. All matched terms are
retained for inspection. This differs from the pilot's is_a/part_of diagnostic.
Alternative GO IDs normalize to their canonical term while preserving the source ID.
Obsolete/unknown terms, negative assertions and mapping exceptions remain visible
outside the positive comparison cohort.

Assertion equality separately checks mapped product, canonical term, relation,
polarity, interacting taxa and literal extension. Evidence is retained separately;
equivalent extensions with different literal encodings are not treated as equal.
Explicit opposite-polarity assertions are flagged only with matching context.
Overlap is agreement with this baseline, not biological accuracy or a review verdict.

## NCBI Gene cross-references

The pilot downloads UniProt GeneID cross-references and checks their taxon using
NCBI Gene ESummary. Both inputs are cached with URLs, dates and checksums; the
resulting `gene_xrefs.tsv` is frozen with the preload and included in row exports.
The UI preserves multiple IDs and labels their organism and provenance.

For `Q88QV4` (`pqqA`), UniProt supplies `NCBIGene:309073029` and
`NCBIGene:98111427`. NCBI reports Pseudomonas benzopyrenica (2993566) and
Pseudomonas ogarae (1114970), respectively. These are explicitly labeled
**Different taxon — cross-reference only**. They are not asserted to be KT2440
gene loci. The October 2026 download contains no exact KT2440-taxon Gene ID
matches among the supplied cross-references.

## API

- `GET /api/predictions`: sets, summary counts, policies and build ID.
- `GET /api/predictions/{id}/claims`: rows, with `status`, `aspect`, `q`, `offset`
  and `size` (1–200), plus `mapping_status`, `model_type`, `evidence_code` and
  exact `gene` link filters. Facet parameters accept comma-separated values;
  responses include self-excluding facet counts.
- `GET /api/predictions/gene/{gene_id}`: claims across preloaded sets linked to
  this gene, with category counts, optional `status`, `offset` and `size`.
- `GET /api/predictions/{id}/export`: all filtered rows as TSV; `format=json`
  returns the full set summary and provenance independently of row filters.
- `GET /api/predictions/{id}/sources/{kind}`: frozen `gaf`, `mapping`, `subjects`,
  `gene_xrefs` or `provenance` artifact, when configured.

Set-specific endpoints accept `view=<build_id>` and reject a different build.
Browser links pin the build and filters. TSV headers record the build, set and filters.

GAF uploads and AIGR/BioReason export ingestion are subsequent adapters; see the
[broader design and issue #8](external-predictions.md).

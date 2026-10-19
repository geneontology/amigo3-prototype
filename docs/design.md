# Implementation decisions

## go-db

Inspected the local `~/repos/go-db` checkout at `bf705aa`.
[Repository](https://github.com/cmungall/go-db), particularly
[`gaf.ddl.sql`](https://github.com/cmungall/go-db/blob/main/src/go_db/sql/gaf.ddl.sql)
and [`amigo_views.sql`](https://github.com/cmungall/go-db/blob/main/src/go_db/sql/amigo_views.sql).

Adopted the raw-source → normalized DuckDB tables → denormalized search projection
pattern, and the separation of relationship-specific ontology closures. AmiGO 3
keeps explicit source-record IDs, negation, and the Boolean grouping of with/from
and extensions. It uses a GPI 2.0 adapter rather than go-db's GPI 1.2 table.

## Patrick Kalita's Standard Annotation Backend

Located Patrick's work in
[geneontology/standard-annotation-backend](https://github.com/geneontology/standard-annotation-backend).
Relevant PRs include entity catalogs (#35), closure-aware search (#30), audit
events (#15), and reviewable changesets (#17). The architecture separates the
canonical annotation payload from backend identity, immutable versions, audit
metadata, and changeset lifecycle.

AmiGO 3 depends on the same published
[`go-standard-annotation-schema==0.0.5`](https://github.com/geneontology/go-standard-annotation-schema)
package and uses its GPI reader. Projection names such as `db_object_id`,
`ontology_class_id`, `negation`, `references`, and `assigned_by` align with that
schema. The current annotation projection is **not** a claim of full SAB payload
compatibility: GAF relations remain labels, evidence codes receive default ECO
mappings, and disjunctive GAF expressions remain grouped on the source record.
The derived `method` field names the pipeline behind a row (for example, IEA via
InterPro2GO or ARBA) from the GO_REF in the reference column, using the table in
`src/amigo3/annotation_methods.yaml`; it is a browsing facet, not a SAB field.
A future SAB adapter can accept canonical Annotation payloads directly.

Import identity is `<source>:<checksum-prefix>:<line>`; a separate fingerprint
hashes the whole original row. This is deterministic for a source snapshot, not a
promise of identity across releases. SAB's UUIDs should be retained when consuming
SAB records. No SAB duplicate-policy equivalence is inferred from these hashes.

SAB's duplicate policy is pairwise and non-transitive for overlapping multi-reference
sets. We do not collapse source annotations according to that policy. We preserve
each source row and check that integration has neither multiplied nor dropped rows.

## linkml-store

[linkml-store](https://github.com/linkml/linkml-store) supplies the API design
inspiration: collections, schema discovery, typed filters, projections, and facets.
AmiGO 3's small query model adds named filter exclusions and a combined hits/facets
response pinned to a build. It does not depend on or fork linkml-store. The contract
and adapters can later be contributed upstream after experience with the browser.

## Publication boundary

DuckDB computes the records; Parquet carries the complete searchable projection;
LinkML describes their types; Solr handles interactive retrieval and faceting.
The publisher builds a dedicated core, verifies counts, and only then writes the
publication pointer. The backend is private to the API. The frontend never emits
Solr query syntax.

The MVP uses full rebuilds and a single Solr core per build, containing typed
collections. It retains old cores for reproducibility. Both the browser and API are
read-only; there are no upstream writes or changeset application semantics yet.

## Gene pages and review context

Gene links navigate to `/genes/{id}`. The gene detail endpoint reads the complete
annotation set from the pinned integration database, so the page has no search
page-size cap. Search and facets still use Solr. A gene page can be bookmarked or
reloaded and uses inline disclosure for evidence and source rows.

Review YAML documents are frozen in the `reviews` table with source paths and
checksums. The page displays summaries and core functions, then connects review
entries to annotation rows using subject, GO term, evidence, reference, negation
and isoform. This is a context link: review entries generally omit relation,
with/from and extensions, so one entry can be relevant to several distinct rows.
All matches are kept and proposals are not applied. Unmatched entries and proposed
additions remain accessible below the annotation list. An older build without
review documents displays its summaries without reading live repository files.

## GO and AmiGO visual style

The visual refresh follows the [GO website](https://geneontology.org/) and its
[current stylesheet](https://geneontology.org/css/main.css): navy `#00174f`,
blue `#4252af`, a white masthead, green search buttons, Lato body text, and Raleway
headings. Compact collection tabs, bordered filters and pale blue alternating
rows take their cues from AmiGO's
[stylesheet](https://github.com/geneontology/amigo/blob/master/static/css/amigo.css)
and [header template](https://github.com/geneontology/amigo/blob/master/templates/html/bs3/common/header.tmpl).
The live AmiGO page presented browser verification during inspection, so its
public source was used to supplement the GO website screenshot.

These are independently written styles for the current HTML and JavaScript.
There is no Bootstrap, jQuery, Perl template layer, remote stylesheet or new UI
runtime dependency. `web/theme.css` defines fonts and shared design tokens;
`web/style.css` handles the shell and collection browser, and `web/gene.css`
handles gene pages. Fonts and the GO icon are local assets, with source attribution
and font licenses under `web/assets/`. The GO icon links to the GO website.

## Ontology and biological model pages

Ontology terms are navigable resources with typed incoming/outgoing edges. The
annotation panel uses the generic query API and offers exact-term or reflexive
is_a/part_of closure filtering, species selection, NOT inclusion and cursor paging.
Regulatory edges appear in the neighborhood but are not traversed for this closure.

The repository adapter reads GO-CAM source/review YAML, recursive module YAML and
PANTHER metadata/review YAML, member CSV and PAINT TSV. Original UTF-8 documents are
stored with paths and byte checksums in `knowledge_sources`; model detail endpoints
read that frozen snapshot. Searchable `KnowledgeRecord` projections travel through
the same DuckDB → Parquet → Solr pipeline as genes and annotations. Exact structured
identifiers connect these records to loaded genes, terms and other resources.

GO-CAM activity connections retain source predicates. Molecule inputs/outputs are
presented separately from enabled-by participants. Modules retain nested parts and
alternative selection semantics. Family inventories reflect the cached subset,
with full-catalog counts labeled separately. PAINT ancestral assertions retain
node IDs, negation, evidence codes and seeds; a flat table does not imply a tree or
assign functions to every family member. None of these adapters manufactures GAF
rows or applies review decisions to upstream annotations.

Models from all repository taxa can be browsed; local gene pages currently cover
human and pombe. Full phylogenetic topology and changeset execution remain separate
future work.

## Indexed assessments and progressive presentation

`annotation_review_entries` holds frozen entries with source paths/checksums;
`annotation_review_links` records the many-to-many context matches. The build uses
the gene page's matcher and projects assessment IDs, actions, replacement IDs,
support availability and multiplicity onto annotation records. `has_review` keeps
its original gene-level meaning; `has_annotation_review` records a row-level match.
Multiple assessments are retained independently. Proposals are never converted
into upstream annotations, and decisions remain unapplied.

The batch assessment endpoint retrieves rich documents for visible results without
putting long JSON records in keyword fields. Annotation text search includes the
matched summary and rationale. Supporting evidence availability means the review
has `supported_by` content; it is not a judgment of evidence quality.

Collection presentation metadata defines display presets, optional fields, facet
groups and visibility conditions. Compact is the default. Conditions reveal facets
without changing filters, and active filters override hidden-group conditions.
Explicitly added facets and display selections persist in the URL. This mechanism
can be reused by other collections without adding Solr syntax to the frontend.

## Collection dashboards

Every collection has List and Dashboard views of the same search and
filters. Dashboard settings (`view`, `group`, `split`, `chart`, `scale`, `top`)
persist in the URL. Bars, legends, and heatmap cells set ordinary filter chips;
a cell applies both dimensions together. Switching to List reveals the matching
records. Each collection's `dashboard` configuration in `search.yaml` declares
available fields, default grouping/split, and two summary cards. Choices are filtered
against the pinned build's fields. Chart settings are remembered per tab. Counts
always refer to records in the current collection (annotation counts are not distinct genes).

`POST /api/query` accepts up to three named `breakdowns`, each with `field`, an
optional `split_by`, and `limit` (1–20, default 12). Dimensions must be distinct
facetable fields. The grouping can be multivalued, such as GO subsets, product-type
ancestors, or project tags; `split_by` must be single-valued so segments remain
additive within a group. Unlike sidebar facets, breakdowns honor **all**
filters and have no exclusion option. Counts cover the full result set and are
independent of the results page.

Each dimension is ranked independently by count across the full filtered result
set, with ties resolved by value. The returned `buckets` contain row counts and,
when split, a `segments` entry for every returned column, including true zeros.
`columns` includes each column's count across all matching records. Top-level
`remainder` counts distinct records outside the union of displayed rows; each row's `remainder`
counts records outside the displayed columns. Remainders include missing values.
The UI keeps these records in percentage denominators and explicitly labels
omitted categories; it never interprets an unreturned category as a zero.

With `group_multivalued: true`, a record counts once in every group it belongs to;
repeated values within a record never increase the count. The UI labels overlapping
groups and notes that percentages across rows can exceed 100%.

DuckDB unnests multivalued groups and counts distinct record IDs. Solr first obtains both marginal
rankings, then uses bounded [JSON query facets](https://solr.apache.org/guide/solr/9_9/query-guide/json-facet-api.html)
for their intersections in one additional request (at most 400 cells per chart).
Builds are immutable, so both requests see the same snapshot. The dashboard uses
HTML/CSS charts with keyboard-accessible filter buttons and a scrollable heatmap
table, without adding a charting framework. Multivalued splits and Any/All
matching remain outside this version.

## External predictions and annotation comparisons

The [external predictions design](external-predictions.md) extends issue #8 with a
shared comparison service for configured prediction/annotation sets and temporary
GAF uploads. It covers the existing AIGR set/claim export and NCBI RefSeq pilot,
including identity mapping, comparison scope, provenance and review semantics.
[Configured GAF preloads](predictions.md) are implemented with separate DuckDB
tables, frozen artifacts, a comparison API and the `/predictions` page. Uploads
and AIGR export ingestion remain future adapters.

# Initial end-to-end run

Verified locally on 2026-10-10 against build `abcc0de20dbe8a01`, served by Solr
9.9.0. GO annotation source headers report 2026-07-28; this is the fetched
snapshot, not a claim that the data is from October.

| Collection | Records |
| --- | ---: |
| Genes and products | 76,562 |
| Annotations | 990,132 |
| GO terms | 48,340 |

The gene/product collection contains 57,726 gene records, 16,424 product records,
and 2,412 complexes. Annotation totals are 910,476 human and 79,656 pombe. Every
source annotation row survived integration exactly once; all GO IDs resolved.
Gene-level review summaries were attached to 2,960 records without applying their
decisions to upstream annotations.

Verification included:

- Eight passing tests, including a real Parquet-to-Solr publication and comparison
  against the DuckDB query implementation.
- Full-dataset count and facet parity for all annotations, each species, NOT
  annotations (1,723), and kinase term/descendant annotations (10,107).
- TP53 resolution from UniProt, HGNC and Ensembl; mrx11 resolution from UniProt
  and PomBase.
- Headless Chrome navigation through search, gene details, annotations, original
  source records, and term search; responsive layout at 390px width.
- Ruff lint/format checks and package build, including packaged LinkML and web assets.

The importer reported 148 strict entity-schema warnings and 197 ambiguous mapping
cases. Original inputs remain intact. Unresolved/ambiguous annotated products
remain separate records. Inspect `data/builds/<build-id>/report.json` for details.

Re-run `scripts/verify_live.py` and `scripts/check_browser.py` against the running
services. Their detailed outputs and browser screenshots are under git-ignored
`data/`; this page records the initial verification, not an automated freshness claim.

## Dedicated gene pages

Build `f697c5de55cc451c` adds frozen review documents; the collection counts above
are unchanged. Sixteen tests pass, including live Solr parity and review matching
checks for reference, evidence, subject, negation, isoform and proposed additions.
The tests also verify that later edits to review YAML do not alter a built page.

The Chrome check now follows gene links to real pages, reloads a deep link, returns
to the prior search, expands inline provenance, and checks a 390px mobile layout.
TP53 renders all 1,068 annotations (1,006 with matching review context), with no
25/100-record cap. Filtering to REMOVE assessments leaves the upstream records
visible, and resetting restores all annotations. Browser checks pass against the
running Solr-backed service.

## Term, GO-CAM, module and family browsing

Build `c5d4d93f9f60e7d8` retains the gene, annotation and term totals above and adds:

| Collection | Records |
| --- | ---: |
| GO-CAMs | 2,036 |
| Modules | 436 |
| PANTHER families | 3,768 |

The integration snapshots 13,231 repository documents. Eighteen tests pass with
live Solr enabled, including the three new collections, typed ontology neighbors,
module alternatives, molecule roles, PAINT negation, subfamily references and
byte-exact immutable source downloads. Full-dataset verification confirms all six
Solr collection totals and the annotation facet/identifier checks above.

Chrome checks pass for collection navigation, bookmarkable detail pages, reloads,
term annotation paging and inline provenance, model activity links, module variant
sets, family member search, exact subfamily filtering and PAINT rows. All four new
page types pass at 390px width without document overflow or JavaScript errors.
The existing gene-page browser regression also passes. Ruff, JavaScript syntax
checks and source/wheel builds pass; the wheel includes the new page assets.

Run `scripts/check_resource_browser.py` for these real-data browser checks. Family
member counts describe cached inventories; they are not full family censuses.

## Annotation assessment display and contextual facets

Build `8753cdac17b0fd64` indexes review-entry matches while retaining all 990,132
upstream annotation rows. Of those, 189,358 belong to reviewed genes, 167,036 have
matching assessments, and 15,257 have multiple matching entries. The ACCEPT facet
matches 72,291 annotation rows. Full-data checks confirm Solr/DuckDB parity for
review coverage, actions, multiple matches and supporting-evidence availability.

Twenty-one tests pass with live Solr, covering agreement with gene-page matching,
multiple assessments, excluded NEW/unmatched entries, isoforms, frozen detail
retrieval, empty review indexes and request bounds. Browser checks cover Compact
and Review views, progressive facets, active-filter persistence, custom fields,
URL reload/navigation, inline rationale and quotations, replacement terms and
mobile layout. Existing gene and resource page regressions also pass. Lint,
formatting, JavaScript syntax and package builds pass.

Run `scripts/check_review_browser.py` against the running service for the review
interaction checks; screenshots remain under git-ignored `data/browser-check/`.

## Rules and mappings

Build `2194f21a9fd82bf6` adds 4,205 records from 105 source files: 2,090 SSSOM
mappings, 2,029 conditional Proteostasis curations, 76 ARBA rules and 10 Pfam
annotation proposals. All nine collection counts match the published Solr core.
Full-data checks agree on hits and nine rule facets, including mapping-level Not,
project membership and review-action filters. Upstream annotation counts remain
990,132.

Twenty-eight tests pass with live Solr enabled. New checks cover preserved
mapping extensions and set defaults, set-versus-row authorship, anonymous row
identity and ordering, arbitrary predicates, omitted antecedents, OR/AND groups,
negated conditions, unmapped subjects, exclusions, counterexample references,
project links and immutable source bytes.

All six browser scripts pass. The new rule browser check exercises SSSOM source
interpretation, ARBA condition groups and reviews, missing conditions, project
filter navigation, source checksums, preserved search state and mobile layouts.
It passes against both the DuckDB preview and the published Solr service. Existing
gene, ontology/resource, review, report and project browser regressions pass.
Screenshots were inspected; lint, formatting, JavaScript syntax and package builds
also pass.

## Interactive GO-CAM pathway layout

The fixed source-order SVG grid is replaced by Cytoscape.js 3.34.3 with
cytoscape-dagre 4.0.1. Browser assets and licenses are vendored; RO relation labels
and positive/negative subproperty classifications are pinned to 2026-09-04.

The new `scripts/check_graph_browser.py` checks the reported C. elegans model
(`gomodel:568b0f9600000284`): all 10 activities and 9 causal edges are present,
the three negative-effect edges use inhibition bars, and both horizontal and
vertical layouts follow the directed acyclic topology. Selection highlights the
immediate neighborhood and exposes the exact relation with source evidence.
Zoom, fit, clear selection, PNG download, mobile layout and keyboard activity
selection work. Screenshots and the downloaded PNG were visually inspected.

The largest loaded model (132 activities) renders without the old 80-activity
cutoff. Synthetic cases exercise parallel edges, feedback, self-loops, unknown
predicates, disconnected nodes and missing targets. Self-loops use Cytoscape's
native routing outside the activity card; Dagre routes other edges. Disconnected
activities are packed below the connected graph. Missing targets are reported,
not fabricated. When a graph dependency fails, the source activity and causal
relationship tables remain readable. Browser-back navigation retains a usable
viewer. The existing resource-browser regression, JavaScript syntax, Ruff checks
and package build pass; the wheel includes libraries, licenses and RO labels.

Graph changes require no annotation rebuild or Solr reindex.

## Field customization across collections

All nine collections offer Customize fields. The eight non-annotation collections
also have collection-specific Details presets. Browser checks cover each field
picker, independent tab choices, URL reloads and return navigation, unchanged
result counts, and all selectable fields at 390px width. Additional cases check
invalid/private field names, zero and false values, empty lists, escaped markup
and inline expansion of long values. The annotation review regression still passes.

Run `scripts/check_display_browser.py` against the running service. Screenshots
are saved under `data/browser-check/`; the desktop field-picker layout was visually
inspected. All 29 Python tests pass with live Solr enabled, including validation
that display fields and presets resolve against the published schema. Ruff,
JavaScript syntax and package builds pass. No data rebuild or reindex is needed.


## Browser cache upgrade regression

An existing Orca tab exposed a gap in the fresh-session browser checks: new
`app.js` loaded beside cached, older `presentation.js`, causing
`normalizeDisplay is not defined`. The other open collection tab also held the
old presentation code. Browser pages now use content-hashed script/stylesheet
URLs and revalidate HTML. Pre-versioning HTML ETags cannot return a stale 304.

`tests/test_browser.py` deliberately caches an obsolete unversioned script, then
loads the updated app in the same browser context. It verifies that the cache
was actually used before the upgrade, exercises add/remove field controls in
all nine collections, and reloads custom URLs. Removing asset versioning makes
this test fail with the exact observed error. GitHub Actions now runs this test
in Chromium against the synthetic fixture, independently of the real-data scripts.

All 32 tests pass locally with Chrome and live Solr, including cache-header and
content-hash checks. The existing Orca tabs were reloaded: selecting Identifiers
on Genes renders the values, and Modules Details renders its extra fields. The
Genes screenshot was visually inspected. This addresses the cached-upgrade case
that the earlier headless runs missed.


## Fixture-based browser journeys in CI

Browser coverage now lives in `tests/browser/`: 23 cases covering cached upgrades,
custom field presets and tab state, gene discovery and complete annotation lists,
review rationale and provenance, annotation paging and progressive facets, term
scope and negation, module alternatives, family membership and PAINT, reports and
artifact downloads, project links, mapping polarity and rule condition groups,
and readable API failures. All nine collection views and their detail destinations
are checked at 390px width.

The GO-CAM cases exercise directed layout, inhibition arrows, evidence inspection,
zoom, PNG export and back navigation. Additional controlled graph payloads cover
132 activities, cycles, parallel edges, self-loops, isolated activities, unknown
relations, missing targets and escaped markup. A blocked graph dependency must
leave the source tables readable.

The main journeys use the real app and importer with a private extension of the
existing synthetic dataset: 34 annotations provide pagination, a review supplies
replacement terms and quotations, and a two-activity model supplies a causal edge.
No production dataset, external biological service or AIGR checkout is required.
The existing larger real-data browser scripts remain available locally.

All 54 tests pass locally with Chrome and live Solr (23 browser cases plus 31
Python/API/integration cases). Ruff and formatting pass. A failure during test
development verified that diagnostics are written before the browser closes:
the screenshot was inspected and the trace ZIP passed its integrity check.
GitHub Actions runs the browser directory in Chromium and uploads failure
screenshots, browser logs and traces as `browser-failures` for 14 days. Passing
tests discard their traces. This is functional and responsive coverage, not
pixel-based visual regression or a complete accessibility audit.

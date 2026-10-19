# GO-CAM networks and model neighborhoods

`/megamodels` is the graphical view of the **entire GO-CAM collection**. Every model
appears by default, including disconnected models. Species, status, review and
other collection filters select nodes; edges are the induced subgraph of that
selection. The GO-CAM list's **Network view** link and the network's **List view**
link preserve search and filters. There is no seed-model step or result-page cap.

Starting from one GO-CAM and extending to neighbors is a separate workflow on
`/gocams/{id}#neighbors`. Open **Explore neighboring models**, inspect candidate
neighbors, and add them to an activity workspace embedded on that model's page.

## Inspiration and linking strategies

This builds on [gocam-mega-editor](https://github.com/cmungall/gocam-mega-editor),
especially `service.py`'s model-link indexes and the neighboring-model workspace.
It brings the editor's six criteria into AmiGO's pinned corpus:

| Criterion | Required exact match | Interpretation |
| --- | --- | --- |
| Same enabler | `enabled_by` | Broad discovery; function and context can differ |
| Enabler + function | Enabler and molecular function | Shared activity type, possibly different contexts |
| Full activity context | Enabler, function, process, compartment; all present | More specific overlap; not proof of identity |
| Terminal → initial overlap | Full context, plus recorded causal-chain end roles | Candidate stitching of overlapping activities; not an extra causal arrow |
| Shared chemical | ChEBI participant, any molecular association role | Broad biochemical neighborhood |
| Output → input | `RO:0002234` to `RO:0002233`, same ChEBI term | Candidate reaction flow |

All criteria require the same nonempty model taxon. Identifier normalization is
used, but no gene-symbol, orthology, ontology-ancestor or chemical-similarity
matching is performed. Molecule instances resolve through the source model's
`molecules` array. Matches keep every distinct activity pair, not just the first
representative activity in each model. Multiple criteria between endpoints are
bundled visually but remain separately inspectable.

Chemical matches require the same known compartment by default: explicit molecule
location when present, otherwise the activity's location. Alternatives permit
unknown compartments, or ignore compartments entirely. An explicit list of 16
common ChEBI metabolites is excluded by default and exposed in the UI/API.
This is an exact-ID exclusion, not a comprehensive currency-metabolite ontology.
Compartment agreement alone does not establish transport, availability, reaction
direction under physiological conditions, or compatible cell state.

## Collection network and local activity workspace

The collection network uses compact Cytoscape nodes, colored by the source
model's taxon and sized by activity count. It offers a force-directed layout,
connectivity rings and a circle, plus drag, pan, zoom, optional labels, inline
node/edge inspection and PNG/JSON export. One undirected edge aggregates all
selected candidate criteria for a model pair. Selecting it fetches the exact
activity-level matches and their directions. Aggregation does not imply causality.

The neighbor workspace on a GO-CAM page retains two detailed views:

- **Model overview:** one node per selected model, with labeled candidate links.
  Aggregation is undirected; inspect a link for individual match directions.
- **Activities:** compound model boundaries, individual activity cards, solid
  recorded causal relationships and dashed cross-model candidates. Each model's
  causal topology is laid out independently with Dagre; isolated activities use a
  compact grid. Source boundaries are packed without letting candidate density
  determine the source layout.

AmiGO reuses its existing Cytoscape/Dagre viewer components rather than adding
the editor's React Flow stack. [Cytoscape compound nodes and subgraph layouts](https://js.cytoscape.org/)
support this view. The editor's persistent editing, undo and change model remain
separate work; this explorer does not edit, merge or assert new biology.

Inspection is inline, with keyboard-accessible model selectors and connection
buttons. Original activity records, causal evidence, match participants and source
YAML checksums remain accessible. Network URLs preserve collection filters,
search, linking criteria, compartment policy, layout and build. Model-page URLs
add `neighbor` parameters for the expanded workspace. Coordinates are not persisted.

Repeated activity IDs occur within 115 models in build `4770856b1c54ab25`.
Each source row is preserved with a model-scoped identity and source row number.
Causal links with ambiguous or external targets are listed as unresolved rather
than guessed; ambiguous activity rows and sources with unresolved outgoing links
are excluded from terminal/initial stitching. The same activity ID appearing in
different models is also kept distinct.

## API, limits and operation

`GET /api/megamodels` returns the catalog, criteria, defaults and exclusions.
`POST /api/megamodels/network` accepts a normal GO-CAM collection `query` plus
linking options. It consumes every page from the server's active query backend
(Solr or DuckDB), preserving list-search semantics and facet exclusions. It
returns all matching models and all aggregate edges between them, without the
local workspace's model, activity or edge limits. Disconnected and source-less
models remain visible; missing sources are reported explicitly. Aggregates are
cached for up to three linking configurations per pinned model index.

`POST /api/megamodels/connection` accepts two `model_ids` plus linking options and
returns exact criterion counts, source provenance and up to 100 example anchors
(the UI shows 30). This keeps whole-corpus responses small without truncating
model-level topology. Network JSON exports contain aggregate counts; use this
endpoint to inspect activity-level evidence.

`POST /api/megamodels` serves the **local neighbor workspace**, for example:

```json
{
  "model_ids": ["gomodel:684b6c8100001669", "gomodel:684b6c8100001511"],
  "criteria": ["gene_mf", "full_activity_signature", "chemical_flow"],
  "chemical_location": "same",
  "include_currency": false
}
```

Optional `view` must match the server's pinned build. The index is lazily cached
from immutable DuckDB `knowledge_sources`; no source checkout reads, rebuild or
Solr republish are required. The first request includes corpus parsing time.

A local activity workspace accepts at most 12 models and 500 activity rows. Candidate output is
capped at 2,000 matches, with total count and truncation reported. Discovery shows
40 neighbors by default (API maximum 100), ordered by distinct link types, then
matching activity-pair count. Counts are discovery aids, not calibrated confidence.
The inspector shows up to 30 anchors per displayed edge; the accessible connection
list shows up to 200 edges. Downloads preserve the complete returned set, subject
to the API's reported cap. These limits do not apply to the collection network.

## Validation and next experiments

Unit/API tests cover all six criteria, direction, taxon separation, missing
context, molecule instances, currency exclusions, compartment policies, repeated
IDs, provenance and limits. Network tests check collection-query parity, facet
counts, pagination beyond 100 models, isolate retention and aggregate/detail
count agreement. Playwright exercises list/network round trips, filters, layout
switching, linking controls, inspection, export, history, empty results and API
failures, plus expansion on actual GO-CAM pages. These run in the existing GitHub
Actions jobs. Live verification on build `4770856b1c54ab25` includes all 2,036 models,
25,061 default aggregate connections, and species-filtered list/network parity.

Useful next comparisons: explicit metabolite nodes versus direct activity links;
selections from projects or enrichment; community detection and group collapse
across the corpus; persisted positions for comparing linking configurations. More
ambitious linking should separately expose identifier equivalence, chemical
hierarchy, transport and orthology assumptions. Adjudicated links could then
become reviewable first-class assertions with changeset provenance.

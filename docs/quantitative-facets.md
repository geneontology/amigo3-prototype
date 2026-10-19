# Quantitative facets

GO-CAMs and modules carry integer structural measures, computed at build time
(`src/amigo3/metrics.py`) from the cached source documents only. The browser shows them
as **range sliders** in a "Size & structure" sidebar group, and the **Dashboard** adds a
histogram per measure plus a joint-distribution heatmap of any two measures (chosen
with the Rows/Columns selectors; shared in the URL as `jx`/`jy`). Clicking a column or
cell applies the corresponding range filter.

## GO-CAMs

These follow the query index of [gocam-py](https://github.com/geneontology/gocam-py),
which backs the official GO-CAM browser. The causal graph has one node per activity
with an `enabled_by`. Its edges are explicit causal associations plus molecule-mediated
chains, where one activity's output is another's input or small-molecule regulator.

| Field | Meaning |
|---|---|
| `activity_count` | All activities in the model |
| `enabled_by_count` | Distinct enabling genes / complexes |
| `causal_edge_count` | Edges in the causal graph |
| `longest_path_length` | Longest *shortest* directed path, in edges (gocam-py's definition; finite on cycles) |
| `component_count` | Weakly connected subgraphs, including isolated activities |

## Modules

Parts are conjunctive (AND); variant sets are disjunctive branch points (OR).

| Field | Meaning |
|---|---|
| `part_count` | AND parts across all levels |
| `variant_set_count` | OR branch points across all levels |
| `variant_count` | Alternatives across all variant sets |
| `realization_count` | Distinct combinations the module admits (variability). Per node: product over parts (an optional part doubles), times per variant set: sum of alternatives (`EXACTLY_ONE`, also used when absent), product of (1 + alternative) minus 1 (`ONE_OR_MORE`), or that product (`ZERO_OR_MORE`). Saturates at 10^12 |
| `max_depth` | Deepest nesting below the root |
| `annoton_count`, `connection_count` | Leaf activities and curated connections across all levels |
| `longest_path_length` | As for GO-CAMs, over connections (except `PART_OF`) plus implicit succession between consecutively ordered sibling parts |

## Query contract

`Query.histograms` requests binned distributions of single-valued integer fields:
`{field, y_field?, bins, y_bins, scale: linear|log, y_scale, exclude_filters}`. Both
engines fetch the exact value distribution and share the binning
(`breakdowns.histogram_result`), so Solr and DuckDB agree exactly. A slider's
histogram excludes its own filter, so the full domain stays visible. Range filters use
`{"op": "between", "values": [min, max]}` (inclusive integers).

Which fields appear, and with which bin scale, is configured per collection in
`search.yaml` under `range_facets`, `dashboard.distributions` and `dashboard.joint`.

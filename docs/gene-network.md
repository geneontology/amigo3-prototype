# GO-CAM gene network

AmiGO can project the loaded GO-CAM corpus into a species-specific functional
interaction network. Each explicit causal relationship between two activities
becomes a connection between their gene-product enablers. Matching identifiers
connect models through shared genes. The enrichment page can use this graph to
test whether a submitted gene set has unusually many or few connections to
topology-derived neighborhoods (the primary network mode) or GO-annotated sets,
while accounting for the input genes' degrees.

## In-app help

The reader-facing guide is served at `/help/network-enrichment`, with contextual
links from the analysis form, overview filters and gene network. It opens in a
separate tab so unsaved inputs and the current analysis remain available.
Sections explain construction, repeated motifs, the null model and BY FDR,
analysis bounds versus display filters, and the two graph levels.

The guide lives in `src/amigo3/web/network-enrichment-help.html`. Keep its
algorithm settings and interpretation aligned with this specification when the
projection, neighborhood policy, null model or display defaults change. The
experiment history remains in [network neighborhood experiments](network-neighborhood-experiments.md).

## Projection

The graph has one node per resolved gene/product and exact model taxon, and one
undirected edge per distinct pair. Multiple assertions, directions, relation
types and models supporting the same pair do not increase its degree or weight.
All supporting assertions remain available with their original direction,
evidence, activity identifiers and model source paths/checksums. Activity records
retain their molecular function, process, location and other recorded context.
Isolated projectable genes are retained.

This is **functional connectivity**, not a physical PPI network. For example,
`provides input for` can link enzymes through a metabolite. The union also spans
recorded contexts; it does not establish that all connections coexist in one
cell. See the [GO-CAM overview](https://geneontology.org/docs/gocam-overview/).

The first policy, `gocam-gene-projection-v1`, uses:

- All model statuses in the frozen source corpus; their counts are reported.
- Explicit causal associations with an allowlist of causal predicates, exposed
  in every report. Unknown and noncausal predicates are reported and omitted.
  The allowlist follows the [GO-CAM causal vocabulary](https://geneontology.github.io/gocam-py/CausalPredicateEnum/)
  and includes legacy direct/indirect regulation relations and
  [constitutively upstream relations](https://github.com/geneontology/noctua/issues/1132).
- Unique identifier mappings into the pinned gene catalog, requiring exact taxon
  agreement. No symbol joins, orthology inference or automatic species/strain
  merging. Isoforms collapse only when the catalog explicitly maps their IDs.
- For species/products absent from the catalog, explicitly typed gene-product
  enablers in the documented provider namespaces retain their source ID.
  These nodes have `resolution: source_identifier`; they do not imply local GO
  annotation coverage. Unknown identifiers or ambiguous catalog mappings are
  omitted. This allows a network for species without loaded GAFs while making
  the limitations for future GO enrichment visible.
- No complex expansion, same-model cliques, inferred cross-model stitching,
  transitive closure, or self-loops. Complex enablers, repeated activity IDs,
  missing taxa/enablers, unresolved targets and other omissions are inspectable.

The exported `policy` gives the exact predicate and source-namespace allowlists.
Changes to these rules should advance the policy version so reports can be
compared alongside the build ID.

## Run and inspect

No rebuild or external interaction download is required when the snapshot was
built with `--repository`. Commands read the frozen GO-CAM sources and identifiers
from the integration database, never the mutable review repository.

```sh
# Coverage across the entire current corpus.
uv run amigo3 --data data gene-network --summary

# Full human network, including assertions, activity records and omissions.
uv run amigo3 --data data gene-network --taxon NCBITaxon:9606 \
  --output data/exports/human-gene-network.json

# Reproduce the report against a specific available build.
uv run amigo3 --data data gene-network --build-id BUILD_ID --summary \
  --output data/exports/gene-network-coverage.json
```

The API uses the server's pinned build:

```text
GET /api/gene-network?summary=true
GET /api/gene-network?taxon=NCBITaxon:9606
GET /api/gene-network?taxon=NCBITaxon:9606&summary=true&view=BUILD_ID
```

An unknown taxon or mismatched `view` returns 422. Omitting `taxon` includes the
entire corpus, with missing-taxon models in the coverage row whose taxon is the
empty string. An empty corpus returns empty coverage and zero totals. Species
filters use exact model taxa, not display names. Full exports are untruncated;
use `summary=true` for the compact coverage report.

Coverage reports include:

- Model and activity counts; the fraction of activities with usable enablers.
  This fraction does **not** mean the activities have a retained causal edge.
- Distinct gene/product and edge counts, degree-zero isolates, connected
  component sizes (including singletons), and largest component size.
- Catalog-mapped versus source-only nodes and nodes with local GO annotations.
  Annotation presence is an inventory count, not a guarantee of membership in a
  particular evidence/aspect-filtered enrichment universe.
- Counts of source versus retained causal assertions, source predicate usage,
  and mutually exclusive omission reasons. The first applicable reason is
  counted; an assertion may involve activities with additional reported issues.

Full exports add `models`, `activities`, `nodes`, `edges` and
`omitted_assertions`. Node `id` is taxon-qualified; `gene_id` is the biological
identifier. Edge `source`/`target` refer to undirected node IDs. Each supporting
assertion's `source`/`target` refer to the original activity keys, while
`source_node`/`target_node` preserve the projected causal direction. Activity
`model_id` links to source metadata; `record` contains the original activity.

## Neighborhood enrichment

**Experimental status:** overlapping local PageRank neighborhoods replace the
initial disjoint greedy-modularity partition. The
[experiment record](network-neighborhood-experiments.md) preserves the rejected
approach, the G2M comparison and parameter sensitivity. These neighborhoods remain
an operational definition, not validated canonical biological modules.

Choose **Network neighborhoods (GO-CAM)**. These enrichment targets are
overlapping sets derived from graph topology, not GO terms or source GO-CAM models.
The existing **GO-term connectivity (GO-CAM)** method remains available.

### Fixed reference neighborhoods

Policy `gocam-local-pagerank-v1` constructs overlapping neighborhoods on the full,
nonisolated, catalog-mapped graph for the exact species taxon. Every reference
gene serves as a seed, independently of the submitted study genes, GO annotations,
custom background and analysis size filters. The construction uses a bounded
variant of [personalized PageRank and conductance sweeps](https://snap.stanford.edu/class/cs224w-readings/andersen06localgraph.pdf),
not the paper's full PageRank-Nibble procedure or its theoretical guarantees:

1. Compute approximate personalized PageRank by deterministic residual pushes.
   Restart at the seed with probability **0.15**; otherwise use a lazy walk
   (half stay, half move to a uniformly chosen neighbor). Stop when every
   residual divided by degree is at most **1e-5**.
2. Rank genes by PageRank divided by degree, with identifier order breaking ties.
   Examine prefixes of **2–50 genes** that contain the seed and induce a connected
   subgraph. Select the prefix with minimum conductance; ties within 1e-12 favor
   the smaller prefix. Conductance is boundary edges divided by the smaller of
   the prefix and complement degree volumes, within the seed's connected component.
3. Do not let a whole component's trivial zero cut override a useful local cut.
   A whole component is eligible only when no proper eligible prefix has
   conductance below **0.5**, and it must still fit the 50-gene cap. If approximate
   support yields no eligible prefix, use the seed and its first neighbor and
   report the fallback count. No fallbacks occurred in the human benchmark.
4. Deduplicate identical reference memberships, retaining all generating seeds.
   Distinct overlapping sets remain separate tests. Every reference gene belongs
   to at least the neighborhood generated from that seed.

All settings and the reference graph contribute to `collection_id`. The legacy
`partition_id` field is an alias, not a claim that sets are disjoint. A GN:
identifier hashes the taxon and sorted membership; the same membership retains
its identity across construction policies. Neighborhood numbers follow reference
size order. Results report seed genes, reference size and reference conductance;
conductance describes the boundary, not statistical enrichment.

The 50-gene construction cap is fixed for this policy. The form's Min/Max genes per neighborhood
controls filter already constructed sets; they do not rerun neighborhood discovery.
For example, `max=10` excludes a 44-gene neighborhood. To display only ten
neighborhoods while retaining larger sets, use `overview_top=10` and leave the
maximum gene count at its default.
Custom backgrounds induce the analysis graph and remove isolates, then intersect
fixed reference neighborhoods with the resulting universe. This can disconnect
a neighborhood without changing its reference identity. Identical sets arising
only after background restriction remain separate reference hypotheses.

The graph has incomplete, context-dependent curation. Overlap permits a gene to
participate in multiple mechanisms, but neither the local algorithm nor its size
cap establishes biological boundaries. In particular, hub-seeded neighborhoods
can be sensitive to locality settings; see the experiment record.

### Connectivity test and descriptive labels

The statistic and null are the same as the GO-term connectivity test below:
count distinct pairs joining study genes to a neighborhood, and compare with
uniformly sampled gene lists with the same exact degree histogram. Study genes
may themselves belong to the neighborhood; a pair satisfying both orientations
still counts once. A shared endpoint alone does not count a connection.
This tests **connections to a fixed neighborhood**, not its internal density or
a separate gene-occupancy hypothesis. Direct study membership is reported as a
descriptive count. All eligible neighborhoods, including zero-connection ones,
enter the BY/BH/Bonferroni correction family before result truncation.

After memberships are fixed, reference-neighborhood members are summarized using
propagated positive GO BP annotations with all evidence. Up to three terms with
at least two annotated members are ranked by Jaccard overlap; roots are omitted.
These labels have no GO enrichment p-value and never define membership,
affect connectivity scores, or rank neighborhood results. The GO aspect,
evidence and exclusion controls are normalized to BP/all/no exclusions for this
mode; they are hidden in the UI. Annotation-free neighborhoods remain testable.

### API and network display

POST to /api/enrichment with method="neighborhood", species and genes.
The target is normalized to "neighborhood" (target="gocam" is rejected).
Network taxon, random-set count, seed, alternative, background and size limits
work as for GO-term connectivity. Results retain the generic terms array for
compatibility, but its identifiers are GN: neighborhoods. Each result adds
reference_size, representatives, seeds, conductance and descriptive go_context. The network object
has kind="neighborhood" and a neighborhoods object containing construction metadata,
all background neighborhoods and shared-gene links (edge_kind="shared_genes").
An edge's shared_genes counts membership overlap in the analysis background, not
functional interactions. Internal edge counts include an edge in every neighborhood
containing both endpoints.
Overview scores cover the complete tested family even when the terms array is
truncated; returned marks which neighborhoods have a returned result row.

The **Network** tab opens first. The overview defaults to the **top 20 returned
neighborhoods with at least two study members**, ranked by lowest p-value, then
study count. Controls allow top 10/20/40/60, a different minimum study count,
and ranking by p-value, study count, study fraction or fold enrichment. Circle
area is proportional to study membership (zero-member circles use a small floor
when explicitly included), and nodes are arranged by rank to leave room for labels. Labels show study/total
membership and the top
descriptive BP label, falling back to seed names when unavailable.

The default is explicitly exploratory: the overview BY FDR filter is off.
Enable it to restrict nodes to the current threshold; editing the main BY FDR
threshold also enables it. Status, green outlines and the summary counts use
the current threshold, not a hardcoded 0.05. An empty filtered overview stays
empty and explains how to explore. The table/chart "Show all" control is separate.
These display filters never rerun enrichment or alter the correction family.

Overview lines count shared genes, not interactions or enrichment scores. The
API retains all background neighborhoods; overview ranking uses tested, returned
results and reports API truncation when applicable. The detail selector retains
all returned neighborhoods. Selecting a hidden neighborhood does not bypass the
overview filters or replace a top-N node; a note explains that its gene detail
remains available below. Shared links retain the overview controls via
`overview_top`, `overview_min_study`, `overview_rank` and `overview_fdr`.

Select a neighborhood node or use **Expand neighborhood** to inspect its gene
network. The default shows counted study-to-neighborhood pairs and their endpoints.
**Show full neighborhood context** adds all neighborhood members in the analysis
background and their internal edges. Solid edges count toward the connectivity
statistic; dashed edges provide context only. Both views retain the score for the
full tested neighborhood. Selecting a pair exposes the original directed activity
assertions and source models. The edge endpoint /api/enrichment/network/edges
accepts include_context=true for the expanded view; the default returns counted
pairs only. Its member_ids, study_ids, counted_connections and per-edge counted
flag make the distinction explicit.

The result table and optional count chart retain the display filter; the network
remains inspectable even when nothing passes. Shared links preserve method,
snapshot, selected neighborhood, tab and network_context=full when expanded. Both
network levels support PNG export; TSV includes construction metadata and
descriptive annotation context. The legacy neighborhood_partition metadata header
now describes an overlapping collection.

For example:
<http://127.0.0.1:8041/enrichment?geneset=HALLMARK_INTERFERON_GAMMA_RESPONSE&species=human&method=neighborhood>

On build `48879e043488a9a3`, the human reference has 1,631 nonisolated genes,
2,293 pairs and **741 distinct overlapping neighborhoods**, each with 2–50 genes.
For `HALLMARK_G2M_CHECKPOINT`, the neighborhood generated by CDC25A, CHEK1 and WEE1
contains those three genes; its counted-pair display also includes the study gene
CDK1 at the boundary. It has 3 connections versus 0.461 expected (p=0.014, BY=1).
CDK1's own 46-gene neighborhood includes all five genes of the source G2M model,
including LMNA, but is broader than that mechanism (26 connections versus 9.706
expected, p=0.004, BY≈0.887). Neither passes BY 0.05 with 1,999 random sets and seed 0.

The interferon-response example also has no BY-significant neighborhoods at those
settings. These examples assess interpretability and reproducibility, not confirmed
biological discovery. Full comparisons and sensitivity results are recorded in
[network neighborhood experiments](network-neighborhood-experiments.md).

## GO-term connectivity enrichment

On `/enrichment`, choose **GO-CAM network connectivity (degree-matched sets)**,
paste genes, and choose species, exact network taxon and GO filters. Both
enriched and depleted connectivity are supported. The coverage panel explains
which genes were usable, omissions, comparison space, p-value resolution and
any input genes fixed by the degree constraints. Expand a result's connecting
pairs to inspect the original activities, predicates, evidence and model source
checksums. TSV downloads include settings, counts, p-values, corrections,
neighbor/overlap IDs, and the null and network policies. Shared links preserve
the build, taxon, background, filters, seed and permutation count.

### Universe and statistic

Start with the exact-taxon projected graph, restrict to catalog-mapped genes of
the selected species, and optionally induce a subgraph on the custom background.
Remove degree-zero nodes. Missing inputs, absent network nodes, isolates and
genes outside the custom background are reported separately. An input gene
does not need its own GO annotation to contribute a connection to an annotated
neighbor. Source-only identifiers remain in the exported graph but cannot enter
this catalog-mapped analysis.

GO memberships use the existing evidence/aspect/review filters and ancestor
propagation. These filters change memberships, not network edges. Size bounds
apply after intersecting memberships with the connected background. For study
set A and GO set B, count distinct undirected pairs with one endpoint in A and
the other in B. An edge internal to A∩B counts once. An edge with only one
endpoint in A∩B and its other endpoint outside A∪B does not count. Direct gene
overlap is reported separately, and a term can score without overlapping A.

### Null and inference

The competitive null samples uniformly from all gene subsets with the same
number of members in each **exact degree stratum** as A, keeping the graph and
GO memberships fixed. It asks whether A connects unusually strongly/weakly to
B compared with equally connected sets. This is a fixed-graph, degree-matched
gene-set randomization, not the [NEAT hypergeometric test](https://pmc.ncbi.nlm.nih.gov/articles/PMC5011912/)
and not a test of differential interactions.

If the product of the within-stratum binomial coefficients is no greater than
the requested permutation count, enumerate all possible sets. Otherwise draw
independent uniform sets with replacement using `random.Random(seed)` and
canonical sorted pools. Upper/lower tails are inclusive. Exact p-values are
`extreme / number_of_sets`; Monte Carlo p-values use `(extreme + 1)/(draws + 1)`.
See [Phipson & Smyth](https://pubmed.ncbi.nlm.nih.gov/21044043/) for the nonzero
Monte Carlo correction. The default is 1,999 random draws (minimum p=0.0005),
configurable from 99 to 19,999. Small p-values have Monte Carlo uncertainty;
more draws improve resolution but take longer. Many tested terms can make the
default resolution insufficient for adjusted significance.

Expected counts are analytical under that same null. An eligible gene of degree
d is selected with probability k_d/n_d. For two different genes in the same
stratum, joint selection has probability k_d(k_d−1)/(n_d(n_d−1)); across strata,
the probabilities multiply. Sum the selection probability of the opposite
endpoint of every edge incident to B, subtracting the joint-selection
probability once for edges internal to B. Fold enrichment is observed/expected;
when expectation is zero it is reported as null. If every study gene is fixed
by the degree constraints, the comparison space is one and all p-values are 1.

All eligible GO terms enter the correction, including zero-connection terms,
before sorting or truncation. The displayed correction is Benjamini–Yekutieli
(BY), which accommodates dependent tests ([Benjamini & Yekutieli, 2001](https://www.math.tau.ac.il/~ybenja/depApr27.pdf)). The API/TSV also expose BH FDR and
Bonferroni. `fdr` retains its existing BH meaning; `by_fdr` is the network display
score. BY can be conservative for nested GO sets. Choosing enriched versus
depleted connectivity defines separate families; exploratory use of both needs
to account for that additional choice.

The network and GO memberships may come from the same curation. Results describe
connectivity within existing GO knowledge, not independent validation of a
process association. Exact degree conditioning can leave hubs fixed and reduce
power. It does not adjust for all study-design or curation biases. Complex
expansion, directed tests and differential interactions remain separate extensions.

### API

`GET /api/enrichment/network` lists available species/taxa and coverage. Submit
the usual `POST /api/enrichment` request with `method: network`:

```json
{
  "genes": ["TP53", "MDM2", "ATM", "CHEK2", "BRCA1"],
  "species": "human",
  "target": "go",
  "method": "network",
  "network_taxon": "NCBITaxon:9606",
  "network_alternative": "greater",
  "network_permutations": 1999,
  "network_seed": 0,
  "aspect": "P",
  "evidence": "all"
}
```

An omitted taxon is allowed only when there is exactly one available taxon for
the species. Add `view` to pin the request to a known build. An empty custom
background is honored as empty; omitting it selects the default graph.

Each result includes `connections`, exact `expected`, `fold_enrichment`,
`study_count` (direct overlap), `genes` (connected study members), `neighbors`
(connected term members), `overlap_genes`, and the three corrections. `network`
contains coverage, the projection policy and null metadata. `tests` is the total
number of tested terms, independent of `max_results`. No usable study genes or
terms returns empty results with coverage rather than invented p-values.

For all contributing pairs and evidence, send the identical request plus
`term_id` and the result's `view` to `POST /api/enrichment/network/edges`. This
reconstructs the same background and memberships without rerunning permutations.

### Graphical inspection

Network results have **Table** and **Graphs** tabs. Both use the same display
filter. When no terms pass BY FDR, select **Show all returned terms** to explore
the returned results; doing so does not change their significance.

The count chart compares observed and exactly expected connections for the
first 15 visible terms, in table order. Its bars share a count scale. The term
selector includes every visible returned term. Select a term to view all its
contributing pairs, with study-only, term-only, and overlapping genes
distinguished by both color and shape. Only incident genes are drawn: this is
the graph of counted pairs, not the full study-induced graph or all term genes.
Lines are undirected functional connections as used by the test. Select a line,
or use the keyboard-accessible pair selector, to inspect the original directed
activity assertions, models, evidence and context. Select a node for its role
and displayed degree. **Fit graph** and **Save PNG** operate on the current
graph; the TSV remains the numerical result export.

The graph fetch uses the exact submitted filters, background and pinned build.
It does not rerun permutations. Graph and table inspectors share an edge cache
that resets after each successful analysis. Changing terms or rerunning an
analysis invalidates any outstanding graph rendering. Shared links preserve
the selected tab, term, and show-all state. If the canvas library cannot load,
the pair selector and its source evidence remain available.

### Suggested demonstration sets

Start with the bundled **HALLMARK_INTERFERON_GAMMA_RESPONSE** example, available
in the enrichment page's **Example gene sets** selector. It represents genes
upregulated in response to interferon gamma, derived from expression data rather
than selected from a GO term ([MSigDB definition](https://www.gsea-msigdb.org/gsea/msigdb/cards/HALLMARK_INTERFERON_GAMMA_RESPONSE)).
This is a useful biological smoke test, not an independent validation of GO-CAM
curation or of the statistical method.

On build 48879e043488a9a3, the bundled MSigDB 2026.1 copy has 211 members;
209 map to inputs and 80 are usable in the human network. With biological
process, default set-size/evidence filters, 1,999 random sets and seed 0:

| Term | Observed connections | Expected | Raw p | BY FDR |
| --- | ---: | ---: | ---: | ---: |
| regulation of cytokine production | 106 | 74.88 | 0.0005 | 0.443 |
| positive regulation of cytokine production | 82 | 51.74 | 0.0005 | 0.443 |
| T cell mediated cytotoxicity | 5 | 0.66 | 0.0005 | 0.443 |
| adaptive immune response | 48 | 20.13 | 0.0005 | 0.443 |

None pass BY FDR 0.05 at this sampling resolution. The first two graphs show
broader connectivity; T cell mediated cytotoxicity is small enough to trace
pair by pair. Do not select a test set by whether it produces a significant
result. More random sets improve resolution, without guaranteeing significance.

For a second expression-derived check, **SANA_RESPONSE_TO_IFNG_UP** is also
bundled: 17 of 80 input genes are usable here, with stimulus-response terms near
the top and no BY-significant results at the same settings. The smaller
TP53, MDM2, ATM, CHEK2, BRCA1 set remains convenient for manually inspecting
eight connections to response to UV, but is a handpicked example.

Statistical regression checks should use fixed synthetic graphs with known
exact answers and degree-matched random sets, as described below; these
biological examples do not replace them.

### Verification

Tests compare counts, expectations and both exact tails with an independent
brute-force implementation, including overlapping sets. Exhaustive null-space
tests check type-I error and BY family-wide rejection under a global null.
Monte Carlo tests check seeded sampling, the nonzero correction and degree
constraints. API tests cover mappings, custom backgrounds, filters, truncation,
evidence and build validation; browser tests exercise the form, source links,
TSV export, shared links, depletion, mobile layout, escaped source text, graph
counts and membership roles, PNG export, and source inspection without the canvas.

## Snapshot audit

Running policy `gocam-gene-projection-v1` on local build `48879e043488a9a3`
produced 5,134 taxon-qualified gene/product nodes and 4,666 undirected edges from
2,036 models. Of 12,585 activity rows, 11,325 (90.0%) had projectable enablers;
7,721 of 9,146 source causal-association rows contributed to retained edges.
There are 1,351 isolated nodes. Repeated assertions are retained as provenance,
not counted as additional edges.

| Species / exact taxon | Models | Nodes | Edges | Components | Largest component | Isolates | Catalog-mapped nodes |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Human / 9606 | 1,214 | 1,918 | 2,420 | 243 | 1,548 | 189 | 1,819 |
| Fission yeast / 4896 | 132 | 1,128 | 801 | 498 | 182 | 425 | 1,128 |
| Mouse / 10090 | 321 | 922 | 887 | 229 | 157 | 151 | 0 |
| Fruit fly / 7227 | 116 | 600 | 395 | 259 | 137 | 215 | 600 |
| Budding yeast S288C / 559292 | 199 | 353 | 42 | 315 | 16 | 301 | 0 |
| Zebrafish / 7955 | 14 | 49 | 32 | 17 | 5 | 5 | 0 |
| Rat / 10116 | 14 | 39 | 15 | 25 | 8 | 22 | 0 |
| C. elegans / 6239 | 4 | 25 | 25 | 4 | 17 | 1 | 25 |

These counts suggest human as the first enrichment prototype: its largest
component contains 80.7% of its projected nodes. Even there, 99 nodes retain
source IDs without catalog mappings, and 181 activity rows are complex-enabled.
Those gaps need explicit handling when defining the annotation universe.

Budding yeast has 597 activity rows omitted for duplicate activity IDs; its
coverage should not be interpreted as an estimate of biological connectivity.
Mouse has a source-derived graph but no mapped nodes in this snapshot's gene
catalog, so local GO enrichment would need additional annotation coverage.
These are snapshot-specific observations, not permanent limitations of GO-CAM.

An end-to-end enrichment smoke run on this build used TP53, MDM2, ATM, CHEK2
and BRCA1 with default biological-process filters and seed 0. All five mapped
into a background of 1,631 connected catalog genes and 2,293 edges. The test
evaluated 6,355 GO sets with 1,999 random draws. The smallest observed p-value
was 0.001, and no term passed BY FDR ≤ 0.05. This run verifies computation and
inspection on real data; it is not evidence of a validated biological discovery.

## Choosing worked examples

The [50-Hallmark illustration screen](network-enrichment-illustrations.md) compares
all human Hallmark sets under fixed settings and reruns the ten strongest at
higher precision with three seeds. It includes frozen inputs and links to the
strongest examples, and distinguishes within-set BY correction from exploratory
selection across gene sets. In this snapshot, none passed at the default 1,999
draws; the recommended significant illustrations use 19,999 draws.

The [study-derived screen beyond Hallmarks](study-derived-network-illustrations.md)
samples 402 signatures from 222 publications across CGP, C7 and C6. Ten leading
signatures were rerun at 19,999 draws with three seeds; none of their selected
neighborhoods passed BY 0.05 across all three. EZH2 knockdown was borderline
(BY 0.0380–0.0532). The report retains negative results, source provenance and
coverage limitations; these examples are exploratory, not validated benchmarks.

For a study-derived walkthrough, [explore the GSE3039 NKT versus CD8αα network](/enrichment?genes=HGNC%3A30205%2CHGNC%3A41%2CHGNC%3A27288%2CHGNC%3A17256%2CHGNC%3A175%2CHGNC%3A195%2CHGNC%3A285%2CHGNC%3A20910%2CHGNC%3A483%2CHGNC%3A548%2CHGNC%3A17352%2CHGNC%3A48348%2CHGNC%3A23724%2CHGNC%3A29351%2CHGNC%3A847%2CHGNC%3A13527%2CHGNC%3A948%2CHGNC%3A25163%2CHGNC%3A28915%2CHGNC%3A1050%2CHGNC%3A1063%2CHGNC%3A31455%2CHGNC%3A23493%2CHGNC%3A10632%2CHGNC%3A6726%2CHGNC%3A1639%2CHGNC%3A24665%2CHGNC%3A1677%2CHGNC%3A11922%2CHGNC%3A1653%2CHGNC%3A16848%2CHGNC%3A1673%2CHGNC%3A1674%2CHGNC%3A1675%2CHGNC%3A1685%2CHGNC%3A1691%2CHGNC%3A1695%2CHGNC%3A1699%2CHGNC%3A1707%2CHGNC%3A33710%2CHGNC%3A27578%2CHGNC%3A1322%2CHGNC%3A18856%2CHGNC%3A2479%2CHGNC%3A2505%2CHGNC%3A2546%2CHGNC%3A16647%2CHGNC%3A20580%2CHGNC%3A25373%2CHGNC%3A2959%2CHGNC%3A2976%2CHGNC%3A2988%2CHGNC%3A3009%2CHGNC%3A3015%2CHGNC%3A3068%2CHGNC%3A28670%2CHGNC%3A13944%2CHGNC%3A3368%2CHGNC%3A1233%2CHGNC%3A16465%2CHGNC%3A3424%2CHGNC%3A3574%2CHGNC%3A30459%2CHGNC%3A11936%2CHGNC%3A3823%2CHGNC%3A4037%2CHGNC%3A19873%2CHGNC%3A4183%2CHGNC%3A17493%2CHGNC%3A4234%2CHGNC%3A4248%2CHGNC%3A23237%2CHGNC%3A21872%2CHGNC%3A21918%2CHGNC%3A22404%2CHGNC%3A21792%2CHGNC%3A16693%2CHGNC%3A29353%2CHGNC%3A13698%2CHGNC%3A4708%2CHGNC%3A4709%2CHGNC%3A4711%2CHGNC%3A18298%2CHGNC%3A20507%2CHGNC%3A29130%2CHGNC%3A4840%2CHGNC%3A4932%2CHGNC%3A4933%2CHGNC%3A4962%2CHGNC%3A4976%2CHGNC%3A5183%2CHGNC%3A5345%2CHGNC%3A5438%2CHGNC%3A18626%2CHGNC%3A5541%2CHGNC%3A13178%2CHGNC%3A5971%2CHGNC%3A5987%2CHGNC%3A6009%2CHGNC%3A6010%2CHGNC%3A6012%2CHGNC%3A6116%2CHGNC%3A6140%2CHGNC%3A6171%2CHGNC%3A6176%2CHGNC%3A23177%2CHGNC%3A16631%2CHGNC%3A6374%2CHGNC%3A6378%2CHGNC%3A26789%2CHGNC%3A6476%2CHGNC%3A30621%2CHGNC%3A18874%2CHGNC%3A6524%2CHGNC%3A24241%2CHGNC%3A6677%2CHGNC%3A6528%2CHGNC%3A6727%2CHGNC%3A6730%2CHGNC%3A15715%2CHGNC%3A26988%2CHGNC%3A25458%2CHGNC%3A29636%2CHGNC%3A14048%2CHGNC%3A7434%2CHGNC%3A6731%2CHGNC%3A12768%2CHGNC%3A7830%2CHGNC%3A29933%2CHGNC%3A7966%2CHGNC%3A28238%2CHGNC%3A23117%2CHGNC%3A15768%2CHGNC%3A18341%2CHGNC%3A1186%2CHGNC%3A273%2CHGNC%3A8760%2CHGNC%3A8816%2CHGNC%3A8809%2CHGNC%3A30013%2CHGNC%3A8923%2CHGNC%3A20884%2CHGNC%3A9112%2CHGNC%3A9113%2CHGNC%3A9327%2CHGNC%3A7892%2CHGNC%3A18583%2CHGNC%3A15850%2CHGNC%3A9322%2CHGNC%3A18737%2CHGNC%3A9360%2CHGNC%3A9403%2CHGNC%3A9410%2CHGNC%3A9635%2CHGNC%3A9666%2CHGNC%3A9667%2CHGNC%3A9812%2CHGNC%3A9891%2CHGNC%3A26120%2CHGNC%3A21863%2CHGNC%3A9997%2CHGNC%3A19441%2CHGNC%3A21176%2CHGNC%3A13436%2CHGNC%3A16820%2CHGNC%3A10732%2CHGNC%3A15848%2CHGNC%3A10757%2CHGNC%3A20263%2CHGNC%3A10808%2CHGNC%3A10820%2CHGNC%3A10821%2CHGNC%3A15605%2CHGNC%3A21394%2CHGNC%3A10908%2CHGNC%3A14430%2CHGNC%3A14409%2CHGNC%3A26918%2CHGNC%3A9298%2CHGNC%3A14677%2CHGNC%3A11249%2CHGNC%3A19744%2CHGNC%3A19751%2CHGNC%3A11362%2CHGNC%3A11363%2CHGNC%3A11365%2CHGNC%3A15585%2CHGNC%3A25749%2CHGNC%3A10699%2CHGNC%3A43%2CHGNC%3A18655%2CHGNC%3A44%2CHGNC%3A29203%2CHGNC%3A1989%2CHGNC%3A11599%2CHGNC%3A21569%2CHGNC%3A11801%2CHGNC%3A24239%2CHGNC%3A26722%2CHGNC%3A26572%2CHGNC%3A11914%2CHGNC%3A18988%2CHGNC%3A11562%2CHGNC%3A12032%2CHGNC%3A12035%2CHGNC%3A13410%2CHGNC%3A12434%2CHGNC%3A29877%2CHGNC%3A29884%2CHGNC%3A13481%2CHGNC%3A30906%2CHGNC%3A28866%2CHGNC%3A16176%2CHGNC%3A16763%2CHGNC%3A28046&species=human&method=neighborhood&view=48879e043488a9a3&network_permutations=19999&network_seed=0&graph_term=GN%3Ab1d1743375a2d5e1&overview_top=10&overview_min_study=2&overview_fdr=0&fdr=0.05&result_view=graphs&min=2&max=1000). This is the human-mapped version of a mouse cell-type contrast; see the
[provenance and three-seed results](study-derived-network-illustrations.md#gse3039_nkt_cell_vs_alphaalpha_cd8_tcell_dn).

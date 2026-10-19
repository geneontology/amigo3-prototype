# Network neighborhood experiments

## 2026-10-17: greedy modularity did not give suitable enrichment targets

**Decision:** reject the initial disjoint greedy-modularity partition as the
preferred definition of biological neighborhoods. The G2M inspection below
showed that it combines distinct mechanisms while also splitting a curated
mechanism. The implementation still uses this approach at the time of this
record; the proposed replacement has not yet been implemented or validated.

This is a failure of the neighborhood definition for our intended use, not a
finding that the connectivity statistic was computed incorrectly, nor a claim
that all modularity methods fail on all biological networks.

### What we tried

We projected GO-CAM causal assertions onto gene enablers and merged shared genes
across models into a species-specific, undirected, unweighted functional graph.
This graph does not imply physical protein interactions or simultaneous activity
in one biological context. See [the projection and enrichment documentation](gene-network.md).

Policy `gocam-greedy-modularity-v1` used NetworkX 3.7's
Clauset–Newman–Moore greedy modularity algorithm at resolution 1. Each gene was
assigned to exactly one community, independently of the submitted study genes
and GO annotations. Enrichment counted connections from the study set to each
fixed community against degree-matched random gene sets.

On build `48879e043488a9a3`, the human catalog-mapped analysis graph contained
1,631 nonisolated genes and 2,293 pairs. It yielded 86 communities with modularity
approximately 0.7989. These were **not connected components**: the same graph had
56 connected components, the largest containing 1,453 genes. A high modularity
score did not establish that the communities were useful biological targets.

### Failure case: HALLMARK_G2M_CHECKPOINT

Inspecting `HALLMARK_G2M_CHECKPOINT` exposed the problem in Neighborhood 001,
`GN:44d0f16d625b805a`:

| Observation | Value |
| --- | --- |
| Reference neighborhood | 189 genes, 258 internal pairs |
| Expanded display | 190 genes, 259 pairs, 232 source models |
| Counted study-to-neighborhood connections | 7 |
| Displayed context-only pairs | 252 |
| Expected connections | 20.06 |
| Fold enrichment | 0.35 |
| Upper-tail p-value / BY FDR | 0.984 / 1.00 |

The display was dominated by GPCR signaling, with the study genes CDK1, CDC25A
and CHEK1 at its edge. These genes participate in
[`gomodel:653b0ce600000623`](https://identifiers.org/gomodel:653b0ce600000623),
"Negative regulation of the G2/M transition of the cell cycle via inhibition of
CDK1 (Human)." The merged graph contains this undirected path into the GPCR region:

```text
CHEK1 — CDC25A — CDK1 — ULK3 — SMO — GNAS
```

The path spans source assertions; it is not a claim that the original G2M model
contains all those genes or describes that entire mechanism. Conversely, LMNA,
which is in the original G2M model and connected to CDK1, was assigned to another
community. Thus the partition both absorbed the cell-cycle segment into a broad
signaling neighborhood and split the source mechanism.

The viewer amplified the problem by showing every internal edge of the selected
community. Most of the visible graph was context, not evidence contributing to
the seven counted connections. The GPCR GO BP labels were descriptive summaries
of the community, not the enrichment targets. Lack of statistical significance
was not the reason for rejecting the approach.

### Interpretation and proposed next experiment

Global modularity has a known
[resolution limit](https://arxiv.org/abs/physics/0607100): small modules can be
merged at a scale that depends on the whole graph. This is a plausible contributor
to the observed result, alongside greedy optimization and the requirement for
disjoint membership; we did not isolate their individual effects. Connected
components would be even less useful as neighborhoods as the corpus grows and
more processes become linked.

The proposed next experiment is **overlapping local neighborhoods**, using
[personalized PageRank with a conductance sweep](https://snap.stanford.edu/class/cs224w-readings/andersen06localgraph.pdf):
explore from a seed gene with repeated returns to that seed, then choose a local
boundary with relatively few crossing connections. Precompute neighborhoods
around reference genes independently of the study set and deduplicate identical
memberships. A gene could then belong to several mechanistic neighborhoods.
Locality parameters, boundary selection and robustness still need evaluation;
this algorithm is not guaranteed to recover a curated GO-CAM exactly.

Evaluate the replacement on G2M and the earlier interferon-response examples,
checking local coherence, retention of source mechanisms, overlap and sensitivity
to parameters. Do not choose settings simply because they produce significant
enrichment. Preserve source assertions and model provenance throughout.

Separately, make the initial display focus on study-associated connections with
optional expansion of context. Display trimming alone must retain and identify
the score for the full tested neighborhood. Changing target memberships requires
recomputing enrichment and the multiple-testing family. If neighborhoods are
instead selected using the submitted study genes, the selection procedure must
also be repeated within the null permutations; the fixed-target p-values cannot
be reused for those adaptively selected subnetworks.

## 2026-10-17 follow-up: overlapping local PageRank neighborhoods

**Status:** implemented as the new experimental neighborhood method. The earlier
modularity implementation is replaced; its failure record above is retained.
The [current policy](gene-network.md#fixed-reference-neighborhoods) specifies the
walk, sweep, size cap, whole-component fallback and deduplication rules. These
rules were fixed before inspecting enrichment p-values.

Every reference gene is a seed. The residual-push PageRank calculation uses a
lazy walk with restart probability 0.15 and residual/degree tolerance 1e-5.
Connected seed-containing sweep prefixes are capped at 50 genes. Whole connected
components cannot automatically win via a trivial zero boundary: they are used
only if no eligible proper prefix has conductance below 0.5, still subject to the
cap. This preserves small cohesive components while allowing weakly connected
substructures to separate. Identical memberships are deduplicated, retaining all
seeds; nonidentical overlapping memberships remain distinct hypotheses.

On the same human build `48879e043488a9a3`, this gives **741 neighborhoods** from
1,631 seeds, with sizes 2–50 and no seed-pair fallbacks. Of these, 95 reach the
50-gene cap. The larger, overlapping hypothesis family is included in BY/BH/
Bonferroni correction before result truncation. The unchanged degree-matched
connectivity test evaluates every eligible target, including zero-hit targets.

### G2M comparison

With the bundled `HALLMARK_G2M_CHECKPOINT` set (209 inputs, 53 network-eligible
genes), default filters, 1,999 random sets and seed 0:

| Target | Reference size | Connections | Expected | Raw p | BY FDR |
| --- | ---: | ---: | ---: | ---: | ---: |
| Previous modularity Neighborhood 001 | 189 | 7 | 20.06 | 0.984 | 1.00 |
| Local neighborhood seeded by CDC25A, CHEK1, WEE1 | 3 | 3 | 0.461 | 0.014 | 1.00 |
| Local neighborhood seeded by CDK1 | 46 | 26 | 9.706 | 0.004 | 0.887 |

These are different targets, not before/after estimates of the same hypothesis.
The compact neighborhood `GN:b4c60798da274983` contains CDC25A, CHEK1 and WEE1.
CDK1 is a study gene outside that neighborhood and is included through its counted
connection to CDC25A. Its default display therefore has **four genes and three
pairs**, without the distant GPCR cluster. CDK1's own neighborhood,
`GN:e6b8d402393b7be1`, includes all five genes of the original G2M GO-CAM, including
LMNA, but also additional mechanisms. The algorithm does not reconstruct GO-CAM
boundaries exactly.

No G2M neighborhood passes BY 0.05 at these settings. This experiment improves
local interpretability; it is not evidence of a significant G2M discovery.
Inspect the compact result on a local server with this build:

<http://127.0.0.1:8041/enrichment?geneset=HALLMARK_G2M_CHECKPOINT&species=human&method=neighborhood&graph_term=GN%3Ab4c60798da274983&view=48879e043488a9a3>

### Interferon comparison and sensitivity

The bundled `HALLMARK_INTERFERON_GAMMA_RESPONSE` example has 209 inputs and 80
network-eligible genes. It also tests 741 neighborhoods, with none passing BY
0.05. For example, the IFNA2-seeded 48-gene neighborhood has 36 connections versus
11.345 expected (p=0.0005, BY≈0.178). The ERAP1/TAP1/TAP2-seeded neighborhood has
three members and four connections versus 0.426 expected (p=0.001, BY≈0.254).
The earlier eight-gene MHC community is now represented by smaller local sets;
this is a change in granularity, not proof that the smaller sets are more correct.

A topology-only sensitivity check varied restart probability over 0.1, 0.15 and
0.3 and the size cap over 25, 50 and 100, without choosing settings by significance:

- CDC25A and CHEK1 retained the same three-gene membership across all nine settings.
- LMNA retained its five-gene membership and TAP1 its three-gene membership.
- CDK1's neighborhood size ranged from 12 to 100. The 46-gene default boundary
  is therefore not robust across this parameter range.
- STAT1 had seven members with caps of 25 and 50; with cap 100 and lower restart
  probabilities it expanded to 62 or 71 genes.

The size cap is substantive: a minimum-conductance sweep can prefer a larger
region when given more room. Local PageRank solves the illustrated compact G2M
case better, but hub neighborhoods still need careful interpretation. Overlapping
near-duplicate targets also enlarge the correction family. Further evaluation
should assess boundary stability and biological coherence, not optimize p-values.

### Display and verification

The default gene display now shows counted connections, with an explicit **Show
full neighborhood context** control. Both views retain the full target's score.
Overview links count shared genes between neighborhoods, rather than assigning
each gene to one community. The overview shows up to 60 neighborhoods for
readability; the selector and table retain returned results and statistical
correction still covers the full tested family. Shared links preserve expansion.

Checks include PageRank against independent power iteration; overlapping,
connected, seed-preserving and size-bounded memberships; deterministic ordering;
independence from unrelated components, study inputs and annotation controls;
exact degree-null enumeration for overlapping targets; overlap-aware overview
counts; and browser checks for expansion, source inspection, exports, shared links
and overview truncation. Real-data G2M and interferon runs complement these checks;
they do not establish calibrated inference for every possible biological study.

## 2026-10-17 display follow-up: filter the overlapping overview

Repeated CHEK1–CDC25A–CDK1 segments across neighborhoods are expected: different
seeds can select overlapping memberships. Their appearance in several views is
not independent evidence. Identical memberships are deduplicated, and enrichment
correction still uses the complete tested family.

The earlier fixed 60-node overview ignored the table/chart threshold and made
study-rich neighborhoods hard to identify. The overview now defaults to the top
20 returned neighborhoods with at least two study members. Users can change top N,
minimum study membership and ranking (p-value, study count, study fraction or fold).
The optional BY FDR filter uses the current threshold; editing that threshold
also enables it in the overview. Empty filtered results remain empty. A selected
single-hit neighborhood remains inspectable below without being injected into
the filtered overview.

Circle area now reflects study membership instead of total neighborhood size.
Ranked placement leaves room for descriptive BP labels; positions do not imply
biological distance. Overview edges still count shared genes. Display filters and
rankings do not alter scores or the correction family, and shared links preserve
the settings. Thus N178 (7/44) is visible in the default G2M overview, while N340
(1/23) is omitted unless the minimum study count is lowered.

## 2026-10-17 illustration screen: all 50 human Hallmark sets

We screened the complete human Hallmark collection with fixed analysis settings,
then reran the ten strongest sets with 19,999 random draws and three seeds.
The [illustration report](network-enrichment-illustrations.md) records the full
comparison, frozen inputs, policies, reproduction commands and links into the UI.

No set passed BY 0.05 at the default 1,999 draws. IL6/JAK/STAT3 and TGF-beta signaling
provide strong, interpretable examples at higher precision. This is exploratory
example selection: BY adjusts neighborhoods within each submitted set, not the
search across gene sets. Seed reruns assess Monte Carlo stability rather than
providing independent biological validation.

## 2026-10-17 broader study-derived signature screen

To move beyond constructed Hallmarks, we selected 402 signatures from 222
publications in a frozen 8,041-record MyGeneset catalog covering CGP, C7 and C6.
Of 396 signatures with network coverage, none passed BY 0.05 at 1,999 draws.
Ten leading candidates were rerun at 19,999 draws with three seeds. No selected
neighborhood passed across all three; the strongest experimental candidate,
EZH2 knockdown, ranged from BY 0.0380 to 0.0532.

The [study-derived illustration report](study-derived-network-illustrations.md)
records selection, all results, source checks and reproducible analysis links.
It distinguishes original perturbations from cell-type contrasts, human-mapped
mouse studies and meta-analysis signatures. Sparse GO-CAM coverage and the
absence of reconstructed assay backgrounds limit interpretation.

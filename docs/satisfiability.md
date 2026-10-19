# Module satisfiability and gap filling

Every module page has a **Species satisfiability** tab (`/modules/{id}?species=PSEPK#satisfiability`).
Pick a loaded species and an evidence preset; the browser asks whether that
species' GO annotations realize each step of the module, and proposes candidate
genes for the steps that are not realized. This is in the spirit of
[GapMind](https://papers.genomics.lbl.gov/gaps), but uses curated GO annotations
and the module's own and/or structure instead of sequence searches.

API: `GET /api/satisfiability?module=MODULE:x` (species options and a default) and
`POST /api/satisfiability` with `{"module": ..., "species": ..., "evidence": "all"|"no_iea"|"experimental"|"iba"}`.

## Logic

The module is read as an and/or tree:

- A node needs **all** of its annotons and all **non-optional** parts.
- A variant set needs **one** realized variant (`EXACTLY_ONE`, `ONE_OR_MORE`);
  `ZERO_OR_MORE` sets are optional.
- An annoton is **satisfied** when a gene of the species has a positive annotation
  to its required molecular function (`participant.required_function`, else
  `function`) or an is_a/part_of descendant, or when a concrete participant or
  representative member named by the module resolves to a gene of that species.
  The best evidence tier is shown: experimental, curated participant, curated
  computational (e.g. IBA, ISS), or IEA only.
- A NOT annotation to the required function, or to one of its is_a ancestors,
  removes a gene from that step, both as a satisfier and as a gap-filling
  candidate. Negation runs opposite to positive propagation: NOT acyltransferase
  rules out every acetyltransferase, but NOT one acetyltransferase leaves the
  broader acyltransferase step open. part_of is not followed for negation, since
  lacking a whole does not rule out having one of its parts.
- Annotons with neither a function term nor a named participant are **not
  scorable** and do not count against their parent.

Status rolls up as satisfied > gap (candidates exist) > not scorable > missing:
the minimum over required children, the maximum over variants.

## Gap filling

For each unsatisfied step, candidates accumulate support from:

| Basis | Weight | Meaning |
| --- | --- | --- |
| broader term | 3 | Annotated to an is_a ancestor (≤3 levels) of the required function. Ancestors that annotate more than 40 genes are skipped as uninformative. |
| subfamily member | 3 | Cached PANTHER inventory member of the annoton's subfamily in the species' taxon. |
| family member | 2 | Member of the annoton's PANTHER family. |
| pathway context | 2 | Annotated to a module process concept, and not already filling any step. |
| related activity | 1 | Annotated to a sibling function under the same parent (often a paralog with another substrate). |

A broader-term match scores 3, 2 or 1 for an ancestor 1, 2 or 3 levels up, and is
dropped for a gene whose broad annotation sits alongside a different specific
activity. A candidate already filling another step loses one point. Lone related-activity
hints are shown only when nothing stronger exists. The tab lists the proposals as
`enables` annotations and offers them as TSV, first those whose gap blocks the
module; a gap in an optional part, or in an alternative to a realized variant, is
marked as not blocking. They are hypotheses for curators:
nothing is written back to annotations, reviews, or the repository.

## Caveats

- Annotons on one node are treated conjunctively (complex subunits, co-acting
  partners). Redundant paralogs modelled as separate annotons on the same node
  will look stricter than they are; variant sets are the right way to model them.
- PANTHER inventories in the repository are cached subsets, so family support is
  only present when the species' protein is in the cached list.
- Module `context.taxa` restrictions are not applied; variants intended for other
  lineages are simply not realized.

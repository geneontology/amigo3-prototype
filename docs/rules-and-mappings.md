# Rules and mappings as first-class records

AmiGO now has one **Rules & mappings** collection with distinct record kinds. It preserves source assertions and assessments and provides searchable, bookmarkable pages. It does not execute the rules or synthesize annotations.

A mapping used to propagate annotations can be understood as a simple rule: membership in a source category implies an annotation to a target term. That interpretation needs an explicit application context. A Rhea reaction correspondence, a heterogeneous InterPro domain and a curated conditional taxonomy mapping should not silently acquire identical semantics.

## Existing AIGR models

| Source | Imported unit | Existing assessment |
| --- | --- | --- |
| `projects/**/*.sssom.yaml` | Individual mapping, with mapping-set context | Justification and comments; some projects encode verdict conventions in predicates |
| `rules/*/*/*-review.yaml` | Native rule and its embedded condition sets | `RuleReview` in `gene_review.yaml`: action, rationale, confidence, specificity, taxonomic scope, overlap and supporting evidence |
| `projects/PROTEOSTASIS/mappings/*.yaml` | Source-subject curation, including unmapped subjects | `ext_mapping.yaml`: status, propagation scope, rationale, conditions, exclusions and ontology-gap assessment |
| `interpro/pfam/*/*-review.yaml` | Individual annotation proposal | `pfam_entry_review.yaml`: status, confidence, rationale, supporting examples and counterexamples; parent InterPro assessment retained |

These paths refer to the AIGR repository supplied to the build. Generated `.terms.yaml` validation derivatives are excluded. Curated, generated, “safe,” and propagation SSSOM sets remain separate source products, even when they share endpoints. The first loaded snapshot contains 2,090 SSSOM rows, 2,029 subject curations, 76 ARBA rules and 10 Pfam proposals. The importer also accepts the same RuleReview shape for UniRule; no UniRule reviews were present in this source snapshot.

Family/PAINT assessments remain in the family browser. Gene-annotation `propagation_review` assesses a particular propagated assertion and its evidence chain; it is not automatically a global review of the originating rule.

## SSSOM preservation and interpretation

The detail record retains every mapping field, extension, prefix definition and mapping-set field. It also exposes an effective mapping with the standard inheritable set defaults. Set authorship is kept distinct from row authorship. Arbitrary predicates, the predicate modifier and `sssom:NoTermFound` survive import. See the [SSSOM 1.0 model](https://mapping-commons.github.io/sssom/1.0/spec-model/) and [predicate modifier definition](https://mapping-commons.github.io/sssom/1.0/predicate_modifier/).

The source conventions matter:

- **InterPro:** `exactMatch` endorses propagation; `broadMatch` flags heterogeneous or overly broad entry membership; `exactMatch` with `Not` proposes removing a mapping.
- **TCDB propagation:** predicates encode categories such as justified, unjustified at this level, class-level or uncertain. The comment and set description explain the evidence and convention.
- **Rhea:** mappings distinguish specific activity matches, broader available terms and missing-term proposals. A reaction correspondence alone is not a protein-matching antecedent.
- **Proteostasis:** `ok_for_propagation_to_go` is explicitly distinct from `exact` and `too_broad_to_propagate`. `no_mapping` is a meaningful reviewed result, not an absent record.

The browser shows these original statements together. It does not translate all `broadMatch` values into one review action, infer a structured review from a prose comment, or turn a mapping-level `Not` into a negated gene annotation. This is source-preserving ingestion, not a general SSSOM validator or a claim that every local predicate convention has standard SKOS semantics.

## Conditions, provenance and links

ARBA condition sets are alternatives (OR); the conditions within a set are conjunctive (AND). Negation stays on its condition. An absent condition set is marked **Not captured**, including reviews that omitted a large antecedent. An empty captured group is also explicitly flagged in the page. Neither is interpreted as an unconditional rule.

Flat premise, GO, family and gene identifiers support search and navigation. They do not encode logical structure or a list of proteins matched by a rule. A referenced protein may be a counterexample. Full nested source objects remain in the DuckDB detail store; Parquet/Solr holds the compact search projection. Source bytes and checksums are frozen with the build.

Projects link to rules through explicitly referenced source files and files under the project's own directory. Project filters work for large mapping sets. GO term, family and gene pages expose structured references back to rules where identifiers resolve to loaded records.

## Identity and future reviews

Native ARBA/UniRule identifiers receive an AmiGO `RULE:` prefix. For anonymous mappings and proposals, the current ID hashes the source path and complete source row. Row reordering does not change it; changes to the assertion, provenance or rationale do. Identical duplicate rows receive occurrence suffixes. Relocating a source changes an anonymous ID. This deliberately avoids silently attaching an assessment to a different assertion, but is not yet a permanent curator-assigned identity system.

Before adding review editing, introduce durable proposal/rule identifiers with explicit revision lineage. A review should identify its target and revision, reviewer, date, action, rationale, supporting evidence and scope. Multiple reviews can coexist. A whole-rule review, a condition-specific review, a conclusion-specific review and an assessment of an applied annotation are different targets. Changesets can then refer to these records directly.

A future executable model could separate `MappingAssertion`, `AnnotationRule` and `RuleReview`, with a single-premise propagation mapping represented as a rule specialization. Keep source SSSOM alongside that interpretation, with an explicit derivation and chosen application semantics. The existing AIGR schemas already provide much of the review content; a new universal schema should reconcile those formats rather than replace them prematurely.

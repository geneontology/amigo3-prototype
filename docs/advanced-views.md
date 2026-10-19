# Advanced views: ideas to revisit

These notes describe longer-term views. The [GO-CAM megamodel network](megamodels.md) now shows the full collection with filters and six cross-model linking strategies. Individual GO-CAM pages support expansion into neighboring activities. Both preserve source activities rather than merging them. Embeddings and the broader integration ideas below remain exploratory.

## A common activity layer

Make GO-CAM activities, module nodes and gene core functions individually addressable, while retaining their different meanings and sources. A selection should carry its filters and shareable URL between a list, an embedding explorer and a network. Later, the same views could compare upstream annotations with a changeset-applied projection.

## Embedding explorers

Keep biological-function embeddings separate from embeddings of review rationales: biological similarity and similar curation problems answer different questions. Activity-level exploration would complement gene-level browsing. Show exactly which text produced a vector, and distinguish neighbors measured in the full vector space from proximity in a two-dimensional projection.

The [DisMech explorer methods](https://github.com/monarch-initiative/dismech-ont-scores/blob/main/METHODS.md) are a useful precedent: separate text spaces, source-grounded mechanism nodes, and versioned vectors and projections. In AmiGO, filters could expose species, families, projects and review actions. Similarity should suggest records to inspect; it is not causal evidence or support for an annotation. Comparing changes requires a fixed embedding model and projection, with changed input text visible.

## GO-CAM mega-models

Start from a project, gene set or selected models, then expand into connected activities. Preserve original activity identifiers, model boundaries and evidence. Sharing an enabler or molecular function is not sufficient to merge activities: organism, location and biological context matter.

[PomBase’s GO-CAM documentation](https://www.pombase.org/documentation/gocams) provides a useful reference for pathway connections, model boundaries and selective display. A first AmiGO version could offer separate-model and connected-network views, with every displayed assertion linking back to its source model.

## Dynamic reaction connections

Propose links where a recorded output of one activity matches a recorded input of another, respecting organism, compartment and chemical identity. Rhea and ChEBI could help reconcile identifiers where the underlying data supports it. Handle currency compounds such as ATP and water explicitly to avoid connecting nearly everything.

Keep inferred candidate connections visually distinct from curated causal associations. Every candidate needs an inspectable reason and source participants. Missing inputs or outputs are incomplete data, not evidence that a connection is impossible. More ambitious matching should follow a useful, conservative exact-match implementation.

## Possible sequence

1. Addressable activities and explicit participant roles, with provenance.
2. A small embedding explorer with inspectable input text and full-vector neighbors.
3. Combined GO-CAM views that retain model boundaries.
4. Candidate reaction links, followed by changeset comparisons.

Revisit this ordering after the basic browsers, rules and mappings are usable. These views should share selection state rather than become disconnected applications.

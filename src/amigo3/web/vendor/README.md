# Pathway viewer dependencies

These browser files are served locally and included in the Python wheel. There
is no runtime CDN dependency or JavaScript build step.

- Cytoscape.js 3.34.3: interactive graph rendering, selection, pan/zoom and PNG export.
- cytoscape-dagre 4.0.1: directed layered layout; its distribution bundles Dagre.
- The included Dagre and Graphlib licenses cover that bundled layout code.

`manifest.json` records package versions, source tarballs and npm SHA-512
integrity values. Regenerate using `uv run python scripts/vendor_graph.py`.
That script also generates `../assets/causal-relations.json` from the pinned RO
2026-09-04 release, using subproperty ancestry for positive/negative effects.
Unrecognized predicates keep their original identifiers and neutral styling.

References: [Cytoscape.js](https://js.cytoscape.org/),
[cytoscape-dagre](https://github.com/cytoscape/cytoscape.js-dagre),
[Relation Ontology](https://github.com/oborel/obo-relations/tree/v2026-09-04).

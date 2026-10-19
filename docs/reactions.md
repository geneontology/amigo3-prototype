# GO-owned reactions

GO terms own structured reaction descriptions. They do not inherit from reaction
classes. [The biological LinkML model](../schema/biology.yaml) composes `GOTerm`
with `Reaction`, `DifferentiationDescription`, `TransportCapability`, and
`BiosynthesisDescription` objects. Reactions and differentiation descriptions are
populated; transport and biosynthesis remain extension points. Modules can
eventually reference these descriptions and supply activity
context without copying chemistry or changing the GO term's identity.

## Storage and identity

* `reactions`: one description per GO–Rhea pair, with local ID
  `GO:0047034/reaction/RHEA:23260`, owning `go_term_id`, explicit `rhea_id`, direction,
  source equation, and checksum provenance. Two GO terms can own separate descriptions
  of the same Rhea reaction. A term can own several distinct reactions.
* `chemicals`: shared ID, label, formula, charge, and source provenance. ChEBI labels
  live here, supplied by the pinned Rhea snapshot. These are Rhea's participant names,
  not an independently refreshed ChEBI release.
* `reaction_participants`: reaction reference, side, position, chemical reference,
  stoichiometry, and source-relative compartment. There is no authoritative chemical
  label on a participant. Details resolve labels by joining `chemicals`.
* `reaction_mappings`: GO–Rhea assertions with predicate and provenance. The Rhea ID
  agrees with the owning reaction. Rhea's GO cross-references use `rdfs:seeAlso` in
  this adapter; they are not promoted to exact equivalence. Repository
  `skos:exactMatch` assertions are preserved separately by rule ID. Negative and
  broad/close/related mappings do not establish a reaction capability.

The three public collections (`reactions`, `chemicals`, `reaction_participants`)
also carry derived search fields. Reaction chemical-ID arrays and search text are
projections, not the source of participant structure. Search fields may duplicate
labels; chemical detail labels always resolve from the chemical table. Chemical records
also cover identifiers explicitly referenced in GO-CAMs and modules, even when no
imported reaction uses them. Rhea labels take precedence; otherwise a label and its
provenance come from the first source record in stable import order. An ID is
displayed when the source supplies no label. Unused model vocabulary entries do
not establish chemical participation.

Small molecules retain ChEBI identities. Rhea `GENERIC` and `POLYMER` accessions
retain their own identities, with reactive-part references, underlying ChEBI
classes, and polymerization indexes where available. An underlying class does not
replace a polymer's identity. Numeric coefficients are positive integers as supplied
by Rhea; symbolic coefficients have a null numeric value and an explicit expression
such as `n` or `n+1`. No missing coefficient is defaulted to 1.

## Import and reproducibility

`amigo3 fetch` now caches the [Rhea RDF export](https://ftp.expasy.org/databases/rhea/rdf/rhea.rdf.gz)
and [GO cross-references](https://ftp.expasy.org/databases/rhea/tsv/rhea2go.tsv), using
the existing checksum-addressed source mechanism. Run `fetch --refresh` only to
advance cached inputs. Historical release URLs can be pinned in `config/sources.yaml`.
`amigo3 build` uses local bytes only and includes those checksums and importer code
in its build identity. Rebuild and republish/restart the server to use the new snapshot.

The adapter merges repeated RDF descriptions, reads coefficients from the
`containsN` predicates, and uses side `curatedOrder` and structured direction
relationships. It never parses equations or GO definitions for chemistry.
Source `In`/`Out` locations are retained without inventing GO compartments.
Unspecified direction uses `=`, not a bidirectional arrow. See
[Rhea's direction semantics](https://www.rhea-db.org/help/reaction-side-direction).

Absent/obsolete GO terms and missing/unapproved Rhea links are listed under
`reactions.issues` in the build report. Incomplete structured chemistry in an
approved reaction fails the build. A missing source pair fails with an actionable
error; omitting both sources produces empty reaction collections. Existing older
snapshots remain readable. Nothing is fetched during page requests.

## API and queries

`GET /api/terms/GO:0047034` adds a `reactions` array containing left/right participant
objects with resolved chemicals, and `reaction_sources` containing source metadata.
`GET /api/reactions/GO:0047034/reaction/RHEA:23260` returns that single description.
The API is a resolved read model; the LinkML domain model uses chemical references.
Overview cards link to Rhea, ChEBI, mapping assertions, the structured API, and
filtered reaction browsing. Reaction search hits link back to their owning term's
equation. Chemical links and search hits open `/chemicals/{id}`. Its
`GET /api/chemicals/{id}` read model includes metadata, reaction sides/directions,
distinct Rhea and GO-owned reaction counts, and reverse links to GO-CAMs/modules.
Related lists show at most 12 records and link to the complete, paginated search.
`chemical_ids` on model/module search records supports exact chemical filtering.
Links derive from structured references, never from labels, prose, or inference
through a model's molecular-function term. No ChEBI hierarchy expansion is implied.
External ChEBI links remain available on chemical pages.

Search for reactions containing a chemical on the left:

```json
{
  "collection": "reactions",
  "filters": [{"id": "chemical", "field": "left_chemical_ids", "values": ["CHEBI:57540"]}]
}
```

For correlated constraints, query participant rows so all filters apply to the
same chemical participation:

```json
{
  "collection": "reaction_participants",
  "filters": [
    {"id": "chemical", "field": "chemical_id", "values": ["CHEBI:15377"]},
    {"id": "side", "field": "side", "values": ["left"]},
    {"id": "coefficient", "field": "stoichiometry", "values": [2]}
  ]
}
```

The existing agent SQL interface also exposes these tables. For example:

```sql
SELECT DISTINCT r.go_term_id, r.rhea_id
FROM reactions r
JOIN reaction_participants p ON p.reaction_id = r.id
WHERE p.chemical_id = 'CHEBI:15377'
  AND p.side = 'left' AND p.stoichiometry = 2;
```

## Validation

Tests cover Rhea's four directions, RDF descriptions split across records, shared
chemicals with multiple GO owners, coefficients and symbolic expressions, mapping
consistency, missing sources, old snapshots, correlated participant queries, and
Solr/DuckDB filter parity. Browser tests exercise desktop/mobile overview cards,
chemical links, provenance, filtered search, history, and equation deep links.
`tests/fixtures/rhea.rdf` is a small extract of Rhea's published RDF (CC BY 4.0),
including reaction 23260 and its directional variants. Synthetic ownership mappings
used in tests are not biological assertions.

## Chemical structures

The `rhea_structures` input caches Rhea's [SDF export](https://ftp.expasy.org/databases/rhea/ctfiles/rhea.sdf.gz),
including curated 2D coordinates. During a build, RDKit produces one SVG per loaded
chemical with a usable structure. `chemical_structures` stores the source molfile,
its checksum, depiction checksum/path, renderer version, and SDF source checksum.
The chemical label and formula remain in `chemicals`; participants still reference
that shared chemical. `chemicals.has_structure` is queryable.

Both chemical pages and reaction cards use these local depictions. Clicking a
figure opens an enlarged SVG; missing or unrenderable structures have a text
fallback. Generic entities are never replaced with an arbitrary representative
molecule. Depiction failures are reported under `chemical_structures.issues` in
the build report. The renderer version participates in the build identity.
`GET /api/chemical-structures/{id}?sha256={svg_sha256}` serves an immutable depiction;
requests for a checksum from another build return 404 rather than different bytes.
Neither drawing nor upstream fetching happens during page requests.

## Cell differentiation

GO terms also own `DifferentiationDescription` objects, independently of reactions.
The normalized `differentiations` table references a shared `cell_types` table;
Cell Ontology labels, synonyms, definitions, and is-a ancestry live there once.
The optional precursor slot is reserved in the domain schema, but this importer
never infers precursor identity from CL `develops_from` edges.

Two checksum-pinned inputs supply these descriptions:

- `go_plus` uses GO's [fully axiomatized release](https://www.geneontology.org/docs/download-ontology/).
  Only `RO:0002315` (results in acquisition of features of) restrictions targeting
  CL on active descendants of GO:0030154 are imported. Logical-definition and
  edge representations are deduplicated, retaining their representation kinds.
- `cl` uses [CL-basic OBOGraphs JSON](https://purl.obolibrary.org/obo/cl/cl-basic.json)
  for cell identities and the separate is-a hierarchy.

GO-basic remains the sole source of the GO graph and annotation closures. GO-plus
is an enrichment source only. Unsupported/missing or obsolete CL targets are
reported; source inputs must be configured together. Older snapshots without
these collections still render term pages normally.

`/terms/GO:0030217` displays its T-cell target and links to
`/cell_types/CL:0000084`. `GET /api/cell_types/{id}` exposes cell metadata, parents,
children, and bounded reverse-link examples. Browse links use the ordinary query
API for complete, paginated results. The `differentiations` collection distinguishes
`target_cell_type_id` (exact target) from `target_cell_type_closure` (target or a
subtype). Neither query asserts expression in that cell type or precursor lineage.

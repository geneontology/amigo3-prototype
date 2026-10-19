"""GO-owned reaction descriptions from checksum-pinned Rhea RDF and mappings.

RDF descriptions of the same subject are merged. Stoichiometry is carried by
Rhea's containsN predicates, not by participant names or equation text.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import xml.etree.ElementTree as ET
from collections import defaultdict

import pyarrow as pa

from amigo3.knowledge import grounding, parse_document, walk
from amigo3.structures import structure_details

RH = "http://rdf.rhea-db.org/"
RDF = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}"
RDFS = "{http://www.w3.org/2000/01/rdf-schema#}"
DIRECTIONS = {"UN": "unspecified", "LR": "left_to_right", "RL": "right_to_left", "BI": "bidirectional"}
SYMBOLS = {"N": "n", "Nplus1": "n+1", "Nminus1": "n-1", "2n": "2n"}


def read_rdf(path):
    """Index only the structured predicates used by this adapter, in bounded XML memory."""
    graph = defaultdict(lambda: defaultdict(list))
    fields = {
        "accession",
        "name",
        "formula",
        "charge",
        "chebi",
        "underlyingChebi",
        "polymerizationIndex",
        "reactivePart",
        "equation",
        "status",
        "side",
        "curatedOrder",
        "directionalReaction",
        "bidirectionalReaction",
        "substrates",
        "products",
        "substratesOrProducts",
        "compound",
        "location",
        "coefficient",
        "isTransport",
    }
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rb") as stream:
        events = ET.iterparse(stream, events=("start", "end"))
        _, root = next(events)
        for event, element in events:
            if event != "end" or element.tag != RDF + "Description":
                continue
            subject = element.get(RDF + "about", "")
            for child in element:
                key = child.tag.removeprefix("{" + RH + "}")
                if key not in fields and not key.startswith("contains"):
                    continue
                value = child.get(RDF + "resource") or child.text or ""
                if value not in graph[subject][key]:
                    graph[subject][key].append(value)
            root.clear()
    return graph


def one(graph, subject, key, default=""):
    values = graph.get(subject, {}).get(key, [])
    if len(values) > 1:
        raise ValueError(f"Ambiguous Rhea {key} on {subject}")
    return values[0] if values else default


def reaction_structure(graph, rhea_id):
    uri = RH + rhea_id.split(":")[1]
    node = graph.get(uri, {})
    if not node or one(graph, uri, "status") != RH + "Approved":
        raise ValueError(f"Missing or unapproved reaction {rhea_id}")
    sides = (
        node.get("side")
        or node.get("substratesOrProducts")
        or (node.get("substrates", []) + node.get("products", []))
    )
    ordered = sorted(sides, key=lambda side: int(one(graph, side, "curatedOrder", "0")))
    if len(set(ordered)) != 2 or [one(graph, s, "curatedOrder") for s in ordered] != ["1", "2"]:
        raise ValueError(f"Expected two ordered sides for {rhea_id}")
    direction = "unspecified" if node.get("side") else "bidirectional"
    if node.get("substrates"):
        direction = "left_to_right" if node["substrates"] == ordered[:1] else "right_to_left"
    participants = []
    for side, side_uri in zip(("left", "right"), ordered, strict=True):
        coefficients = {}
        for predicate, targets in graph[side_uri].items():
            if not predicate.startswith("contains") or predicate == "contains":
                continue
            coefficient = one(graph, RH + predicate, "coefficient")
            if not coefficient:
                raise ValueError(f"Missing coefficient for {predicate}")
            for target in targets:
                if target in coefficients and coefficients[target] != coefficient:
                    raise ValueError(f"Conflicting coefficients on {side_uri}: {target}")
                coefficients[target] = coefficient
        if not coefficients or set(coefficients) != set(graph[side_uri].get("contains", [])):
            raise ValueError(f"Incomplete participants on {side_uri}")
        for position, participant in enumerate(sorted(coefficients), 1):
            coefficient = coefficients[participant]
            numeric = int(coefficient) if coefficient.isdigit() else None
            if numeric is not None and numeric <= 0:
                raise ValueError(f"Nonpositive stoichiometry: {coefficient}")
            compound = one(graph, participant, "compound")
            chemical_id = one(graph, compound, "accession")
            if not chemical_id or not one(graph, compound, "name"):
                raise ValueError(f"Unresolved chemical on {participant}")
            participants.append(
                {
                    "side": side,
                    "position": position,
                    "chemical_id": chemical_id,
                    "stoichiometry": numeric,
                    "stoichiometry_expression": ""
                    if numeric is not None
                    else SYMBOLS.get(coefficient, coefficient),
                    "compartment": one(graph, participant, "location"),
                    "source_participant": participant,
                    "compound": compound,
                }
            )
    return direction, participants


REACTION_SCHEMA = pa.schema(
    [
        (k, pa.string())
        for k in (
            "id",
            "go_term_id",
            "rhea_id",
            "label",
            "direction",
            "source_id",
            "source_sha256",
            "search_text",
        )
    ]
    + [("is_transport", pa.bool_())]
    + [(k, pa.list_(pa.string())) for k in ("chemical_ids", "left_chemical_ids", "right_chemical_ids")]
)
CHEMICAL_SCHEMA = pa.schema(
    [
        (k, pa.string())
        for k in (
            "id",
            "label",
            "formula",
            "charge",
            "underlying_chebi_id",
            "polymerization_index",
            "source_uri",
            "source_id",
            "source_sha256",
            "search_text",
        )
    ]
    + [("reactive_part_ids", pa.list_(pa.string()))]
)
PARTICIPANT_SCHEMA = pa.schema(
    [
        (k, pa.string())
        for k in (
            "id",
            "reaction_id",
            "go_term_id",
            "rhea_id",
            "side",
            "chemical_id",
            "stoichiometry_expression",
            "compartment",
            "source_participant",
            "search_text",
        )
    ]
    + [(k, pa.int64()) for k in ("position", "stoichiometry")]
)
MAPPING_SCHEMA = pa.schema(
    [
        (k, pa.string())
        for k in (
            "id",
            "reaction_id",
            "go_term_id",
            "rhea_id",
            "predicate",
            "source_id",
            "source_sha256",
            "rule_id",
        )
    ]
)


def load_reactions(db, data, manifest):
    sources = {kind: [s for s in manifest.values() if s["kind"] == kind] for kind in ("rhea_rdf", "rhea_go")}
    if any(len(entries) > 1 for entries in sources.values()):
        raise ValueError("Use one Rhea RDF snapshot and one GO mapping snapshot")
    if bool(sources["rhea_rdf"]) != bool(sources["rhea_go"]):
        raise ValueError("Rhea import requires both rhea_rdf and rhea_go sources; fetch again")
    reactions, chemicals, participants, mappings, issues = {}, {}, [], [], []
    if sources["rhea_rdf"]:
        source, mapping_source = sources["rhea_rdf"][0], sources["rhea_go"][0]
        graph = read_rdf(data / source["path"])
        terms = dict(db.execute("SELECT id, label FROM terms WHERE NOT obsolete").fetchall())
        assertions = []
        with (data / mapping_source["path"]).open() as stream:
            reader = csv.DictReader(stream, delimiter="\t")
            if not {"RHEA_ID", "DIRECTION", "MASTER_ID", "ID"} <= set(reader.fieldnames or []):
                raise ValueError("Invalid Rhea GO mapping header")
            for row in reader:
                assertions.append(
                    (
                        row["ID"],
                        "RHEA:" + row["RHEA_ID"],
                        "rdfs:seeAlso",
                        mapping_source["id"],
                        mapping_source["sha256"],
                        "",
                        row["DIRECTION"],
                    )
                )
        # Retain repository exact-match assertions as additional provenance. A
        # broad/negative mapping is not sufficient to assert that a term owns chemistry.
        tables = {row[0] for row in db.execute("SHOW TABLES").fetchall()}
        if "rules" in tables:
            for (
                identifier,
                subject,
                obj,
                predicate,
            ) in db.execute("""SELECT id, subject_id, object_id, predicate_id
                FROM rules WHERE predicate_id='skos:exactMatch' AND coalesce(predicate_modifier, '')=''""").fetchall():
                go, rhea = (obj, subject) if subject.startswith("RHEA:") else (subject, obj)
                if go.startswith("GO:") and rhea.startswith("RHEA:"):
                    assertions.append((go, rhea, predicate, "repository_rules", "", identifier, ""))
        for go, rhea, predicate, mapping_id, checksum, rule, declared_direction in sorted(set(assertions)):
            if go not in terms:
                issues.append({"go_term_id": go, "rhea_id": rhea, "reason": "GO term absent or obsolete"})
                continue
            identifier = f"{go}/reaction/{rhea}"
            if identifier not in reactions:
                uri = RH + rhea.split(":")[1]
                if not graph.get(uri) or one(graph, uri, "status") != RH + "Approved":
                    issues.append(
                        {
                            "go_term_id": go,
                            "rhea_id": rhea,
                            "reason": f"Missing or unapproved reaction {rhea}",
                            "source_id": mapping_id,
                            "rule_id": rule,
                        }
                    )
                    continue
                # An approved but malformed record is an import failure, not missing data.
                direction, members = reaction_structure(graph, rhea)
                reaction = {
                    "id": identifier,
                    "go_term_id": go,
                    "rhea_id": rhea,
                    "label": one(graph, uri, "equation"),
                    "direction": direction,
                    "source_id": source["id"],
                    "source_sha256": source["sha256"],
                    "is_transport": one(graph, uri, "isTransport") == "true",
                    "chemical_ids": sorted({p["chemical_id"] for p in members}),
                    **{
                        side + "_chemical_ids": sorted(
                            {p["chemical_id"] for p in members if p["side"] == side}
                        )
                        for side in ("left", "right")
                    },
                }
                reaction["search_text"] = " ".join(
                    [go, terms[go], rhea, reaction["label"], *reaction["chemical_ids"]]
                )
                reactions[identifier] = reaction
                for member in members:
                    compound = member.pop("compound")
                    chemical = {
                        "id": member["chemical_id"],
                        "label": one(graph, compound, "name"),
                        "formula": one(graph, compound, "formula"),
                        "charge": one(graph, compound, "charge"),
                        "underlying_chebi_id": one(graph, compound, "underlyingChebi").replace(
                            "http://purl.obolibrary.org/obo/CHEBI_", "CHEBI:"
                        ),
                        "polymerization_index": one(graph, compound, "polymerizationIndex"),
                        "reactive_part_ids": [
                            one(graph, part, "accession") or part
                            for part in graph[compound].get("reactivePart", [])
                        ],
                        "source_uri": compound,
                        "source_id": source["id"],
                        "source_sha256": source["sha256"],
                    }
                    chemical["search_text"] = " ".join(
                        [chemical["id"], chemical["label"], chemical["underlying_chebi_id"]]
                    )
                    if chemical["id"] in chemicals and chemicals[chemical["id"]] != chemical:
                        raise ValueError(f"Conflicting chemical identity: {chemical['id']}")
                    chemicals[chemical["id"]] = chemical
                    participants.append(
                        {
                            **member,
                            "id": f"{identifier}/{member['side']}/{member['position']}",
                            "reaction_id": identifier,
                            "go_term_id": go,
                            "rhea_id": rhea,
                            "search_text": " ".join([go, rhea, chemical["id"], chemical["label"]]),
                        }
                    )
            if (
                declared_direction
                and DIRECTIONS.get(declared_direction) != reactions[identifier]["direction"]
            ):
                raise ValueError(f"Rhea direction mismatch for {rhea}")
            mapping_key = "|".join([identifier, predicate, mapping_id, rule])
            mappings.append(
                {
                    "id": "REACTION_MAPPING:" + hashlib.sha256(mapping_key.encode()).hexdigest()[:24],
                    "reaction_id": identifier,
                    "go_term_id": go,
                    "rhea_id": rhea,
                    "predicate": predicate,
                    "source_id": mapping_id,
                    "source_sha256": checksum,
                    "rule_id": rule,
                }
            )
    # Chemicals explicitly used by models/modules are entities even when no
    # imported GO-owned Rhea reaction mentions them. Labels retain source provenance.
    if "knowledge_sources" in {row[0] for row in db.execute("SHOW TABLES").fetchall()}:
        for collection, path, checksum, raw in db.execute("""SELECT collection, path, sha256, raw_text
            FROM knowledge_sources WHERE role IN ('model', 'module') ORDER BY collection, path""").fetchall():
            document = parse_document(path, raw)
            refs = sorted(identifier for identifier in grounding(document) if identifier.startswith("CHEBI:"))
            labels = {
                node["id"]: node["label"]
                for node in walk(document)
                if isinstance(node.get("id"), str) and node.get("label")
            }
            for identifier in refs:
                if identifier in chemicals:
                    continue
                chemical = {
                    field.name: [] if pa.types.is_list(field.type) else "" for field in CHEMICAL_SCHEMA
                }
                chemical.update(
                    id=identifier,
                    label=labels.get(identifier, identifier),
                    source_id="knowledge:" + collection,
                    source_uri=path,
                    source_sha256=checksum,
                )
                chemical["search_text"] = identifier + " " + chemical["label"]
                chemicals[identifier] = chemical
    for name, rows, schema in (
        ("reactions", list(reactions.values()), REACTION_SCHEMA),
        ("chemicals", list(chemicals.values()), CHEMICAL_SCHEMA),
        ("reaction_participants", participants, PARTICIPANT_SCHEMA),
        ("reaction_mappings", mappings, MAPPING_SCHEMA),
    ):
        db.register("reaction_batch", pa.Table.from_pylist(rows, schema=schema))
        db.execute(f'CREATE TABLE "{name}" AS SELECT * FROM reaction_batch')
        db.execute(f'ALTER TABLE "{name}" ADD PRIMARY KEY (id)')
        db.unregister("reaction_batch")
    db.execute("CREATE INDEX reactions_owner ON reactions(go_term_id)")
    db.execute("CREATE INDEX participants_reaction ON reaction_participants(reaction_id)")
    db.execute("CREATE INDEX participants_chemical ON reaction_participants(chemical_id)")
    return {
        "reactions": len(reactions),
        "chemicals": len(chemicals),
        "participants": len(participants),
        "issues": issues,
    }


def reaction_details(db, *, go_term_id=None, reaction_id=None):
    """Resolve shared chemical labels at read time; older snapshots remain usable."""
    if "reactions" not in {r[0] for r in db.execute("SHOW TABLES").fetchall()}:
        return []
    field, value = ("go_term_id", go_term_id) if go_term_id is not None else ("id", reaction_id)
    rows = (
        db.execute(f'SELECT * EXCLUDE(search_text) FROM reactions WHERE "{field}"=? ORDER BY id', [value])
        .to_arrow_table()
        .to_pylist()
    )
    structures = structure_details(db, {c for row in rows for c in row["chemical_ids"]})
    for row in rows:
        row["left"], row["right"] = [], []
        members = (
            db.execute(
                """SELECT p.* EXCLUDE(search_text), c.label, c.formula, c.charge
            FROM reaction_participants p JOIN chemicals c ON c.id=p.chemical_id
            WHERE p.reaction_id=? ORDER BY side, position""",
                [row["id"]],
            )
            .to_arrow_table()
            .to_pylist()
        )
        for member in members:
            chemical = {
                "id": member.pop("chemical_id"),
                **{key: member.pop(key) for key in ("label", "formula", "charge")},
            }
            chemical["structure"] = structures.get(chemical["id"])
            side = member.pop("side")
            row[side].append({**member, "chemical": chemical})
        row["mappings"] = (
            db.execute("SELECT * FROM reaction_mappings WHERE reaction_id=? ORDER BY id", [row["id"]])
            .to_arrow_table()
            .to_pylist()
        )
    return rows

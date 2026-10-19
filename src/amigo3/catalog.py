"""Discover published field types; configure presentation independently of engines."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import yaml

from amigo3.breakdowns import configure_dashboard, configure_ranges
from amigo3.sorting import configure_sorts, resolve_sort

PRESENTATION = yaml.safe_load((Path(__file__).parent / "search.yaml").read_text())


class Catalog:
    def __init__(self, data: Path, build_id: str | None = None):
        self.data = data
        if build_id is None:
            build_id = json.loads((data / "current.json").read_text())["build_id"]
        if len(build_id) != 16 or any(c not in "0123456789abcdef" for c in build_id):
            raise ValueError("Invalid build ID")
        self.build_id = build_id
        self.path = data / "builds" / build_id
        self.manifest = json.loads((self.path / "manifest.json").read_text())
        schema_path = Path(__file__).parent / "schema.yaml"
        if not schema_path.exists():
            schema_path = Path(__file__).resolve().parents[2] / "schema" / "amigo3.yaml"
        domain_schema = yaml.safe_load(schema_path.read_text())
        self.collections = {}
        for name, presentation in PRESENTATION.items():
            if name not in self.manifest["counts"]:
                continue
            class_name = {
                "genes": "GeneRecord",
                "annotations": "AnnotationRecord",
                "terms": "TermRecord",
                "reports": "ReportRecord",
                "projects": "ProjectRecord",
                "rules": "RuleRecord",
                "reactions": "ReactionRecord",
                "chemicals": "ChemicalRecord",
                "cell_types": "CellTypeRecord",
                "differentiations": "DifferentiationRecord",
                "reaction_participants": "ReactionParticipantRecord",
                "references": "ReferenceRecord",
            }.get(name, "KnowledgeRecord")
            declared = domain_schema["classes"][class_name]["attributes"]
            fields = {}
            for field in pq.read_schema(self.path / f"{name}.parquet"):
                is_list = pa.types.is_list(field.type)
                scalar = field.type.value_type if is_list else field.type
                kind = (
                    "boolean"
                    if pa.types.is_boolean(scalar)
                    else "integer"
                    if pa.types.is_integer(scalar)
                    else "string"
                )
                if field.name not in declared:
                    raise ValueError(f"Undeclared projection field: {name}.{field.name}")
                definition = declared[field.name]
                if definition["range"] != kind or bool(definition.get("multivalued")) != is_list:
                    raise ValueError(f"Projection type disagrees with LinkML schema: {name}.{field.name}")
                fields[field.name] = {
                    "type": kind,
                    "multivalued": is_list,
                    "label": presentation.get("field_labels", {}).get(
                        field.name, field.name.replace("_", " ").capitalize()
                    ),
                    "facetable": bool(
                        definition.get("annotations", {}).get(
                            "facet", field.name in presentation.get("facets", [])
                        )
                    ),
                }
            self.collections[name] = {
                **deepcopy(presentation),
                "fields": fields,
                "count": self.manifest["counts"][name],
            }
            config = self.collections[name]
            configure_sorts(config)
            # Older immutable snapshots predate the product-type projection.
            if name == "genes" and "gene_product_types" not in fields:
                config["facets"] = [
                    "record_kind" if f == "gene_product_types" else f for f in config["facets"]
                ]
                for group in config.get("facet_groups", []):
                    group["fields"] = [
                        "record_kind" if f == "gene_product_types" else f for f in group["fields"]
                    ]
                config["display_fields"].pop("gene_product_type_ids", None)
            config["hierarchies"] = {}
            for hierarchy in self.manifest.get("hierarchies", {}).get(name, []):
                field = hierarchy["field"]
                if field not in fields or not hierarchy["nodes"]:
                    continue
                config["hierarchies"][field] = hierarchy
                fields[field]["label"] = hierarchy["label"]
                replaced = hierarchy.get("replaces")
                if replaced == "gene_product_types":
                    fields[replaced]["label"] = "Supplied product type (exact)"

                def insert(values, field=field, replaced=replaced):
                    return [field if value == replaced else value for value in values]

                config["facets"] = insert(config["facets"])
                for group in config.get("facet_groups", []):
                    group["fields"] = insert(group["fields"])
                if field not in config["facets"]:
                    config["facets"].insert(1, field)
                    if config.get("facet_groups"):
                        config["facet_groups"][0]["fields"].insert(1, field)

            configure_dashboard(config)
            configure_ranges(config)

    def validate(self, query):
        if query.view and query.view != self.build_id:
            raise ValueError("Requested view is not this server's pinned build")
        if query.collection not in self.collections:
            raise ValueError("Collection is not available in this build; rebuild with --repository")
        fields = self.collections[query.collection]["fields"]
        for clause in resolve_sort(self.collections[query.collection], query):
            field = fields.get(clause.field)
            if not field or clause.field == "search_text":
                raise ValueError(f"Unknown or non-public sort field: {clause.field}")
            if clause.value is None and field["multivalued"]:
                raise ValueError("Sorting a multivalued field requires an exact value to rank")
            if clause.value is not None:
                expected = {"string": str, "boolean": bool, "integer": int}[field["type"]]
                if type(clause.value) is not expected:
                    raise ValueError(f"Expected {field['type']} sort value for {clause.field}")
        requested = (
            [f.field for f in query.filters] + [f.field for f in query.facets.values()] + (query.select or [])
        )
        for field in requested:
            if field not in fields or field == "search_text":
                raise ValueError(f"Unknown or non-public field: {field}")
        for breakdown in query.breakdowns.values():
            for field in [breakdown.field, breakdown.split_by]:
                if field is None:
                    continue
                if field not in fields or not fields[field]["facetable"]:
                    raise ValueError("Breakdowns require facetable fields")
                if field == breakdown.split_by and fields[field]["multivalued"]:
                    raise ValueError("Breakdown splits require single-valued facetable fields")
        for histogram in query.histograms.values():
            for field in [histogram.field, histogram.y_field]:
                if field is None:
                    continue
                if field not in fields or fields[field]["type"] != "integer" or fields[field]["multivalued"]:
                    raise ValueError("Histograms require single-valued integer fields")
        for f in [*query.filters, *(f for f in query.facets.values() if f.values is not None)]:
            kind = fields[f.field]["type"]
            if getattr(f, "op", None) in ("gte", "lte", "between") and kind != "integer":
                raise ValueError("Range filters require an integer field")
            for value in f.values:
                valid = (
                    isinstance(value, str)
                    if kind == "string"
                    else type(value) is bool
                    if kind == "boolean"
                    else type(value) is int
                )
                if not valid:
                    raise ValueError(f"Expected {kind} values for {f.field}")

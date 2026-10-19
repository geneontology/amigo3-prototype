"""Parquet publisher and compilation of generic queries into Solr JSON facets."""

from __future__ import annotations

import json
import re
import time

import httpx
import pyarrow.parquet as pq

from amigo3.breakdowns import breakdown_result, histogram_result
from amigo3.catalog import Catalog
from amigo3.query import Filter, Query, decode_cursor, encode_cursor
from amigo3.sorting import resolve_sort
from amigo3.sources import digest, write_json


def escape(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return re.sub(r'([+\-!(){}\[\]^"~*?:\\/|&])', r"\\\1", str(value))


def field_name(name: str) -> str:
    return "record_id" if name == "id" else name


def filter_expression(f) -> str:
    field = field_name(f.field)
    if f.op == "exists":
        return f"{field}:[* TO *]"
    if f.op == "gte":
        return f"{field}:[{f.values[0]} TO *]"
    if f.op == "lte":
        return f"{field}:[* TO {f.values[0]}]"
    if f.op == "between":
        return f"{field}:[{f.values[0]} TO {f.values[1]}]"
    clause = f"{field}:(" + " OR ".join(f'"{escape(v)}"' for v in f.values) + ")"
    return f"(*:* AND -{clause})" if f.op == "not_in" else clause


def publish(catalog: Catalog, url: str, batch_size: int = 1500) -> dict:
    core = "amigo3_" + catalog.build_id
    url = url.rstrip("/")
    with httpx.Client(timeout=120) as client:
        status = client.get(f"{url}/admin/cores", params={"action": "STATUS", "wt": "json"})
        status.raise_for_status()
        exists = core in status.json()["status"]
        receipt_path = catalog.path / "solr.json"
        if exists and receipt_path.exists():
            receipt = json.loads(receipt_path.read_text())
            if receipt["url"] == url:
                print(f"Already published {core}")
                return receipt
        if not exists:
            response = client.get(
                f"{url}/admin/cores",
                params={
                    "action": "CREATE",
                    "name": core,
                    "instanceDir": core,
                    "configSet": "_default",
                    "wt": "json",
                },
            )
            response.raise_for_status()
        base = f"{url}/{core}"
        existing = {f["name"] for f in client.get(f"{base}/schema/fields").json()["fields"]}
        types = client.get(f"{base}/schema/fieldtypes").json()["fieldTypes"]
        if "amigo_text" not in {t["name"] for t in types}:
            response = client.post(
                f"{base}/schema",
                json={
                    "add-field-type": {
                        "name": "amigo_text",
                        "class": "solr.TextField",
                        "analyzer": {
                            "tokenizer": {"class": "solr.StandardTokenizerFactory"},
                            "filters": [{"class": "solr.LowerCaseFilterFactory"}],
                        },
                    }
                },
            )
            response.raise_for_status()
        fields = {
            "collection": {"type": "string", "multivalued": False},
            "record_id": {"type": "string", "multivalued": False},
            "payload": {"type": "string", "multivalued": False},
        }
        for config in catalog.collections.values():
            for name, spec in config["fields"].items():
                if name != "id":
                    if name in fields and (spec["type"], spec["multivalued"]) != (
                        fields[name]["type"],
                        fields[name]["multivalued"],
                    ):
                        raise ValueError(f"Incompatible shared field {name}")
                    fields[name] = spec
        additions = []
        for name, spec in fields.items():
            if name in existing:
                continue
            field = {
                "name": name,
                "type": {"integer": "plong", "boolean": "boolean"}.get(spec["type"], "string"),
                "multiValued": spec["multivalued"],
                "stored": False,
                "indexed": True,
                "docValues": True,
            }
            if name == "payload":
                field.update(stored=True, indexed=False, docValues=False)
            if name == "search_text":
                field.update(type="amigo_text", docValues=False)
            additions.append(field)
        if additions:
            response = client.post(f"{base}/schema", json={"add-field": additions})
            response.raise_for_status()
            if response.json().get("errors"):
                raise ValueError(response.text)
        # Only this immutable build's dedicated core is modified. Failed publication
        # can be restarted; no live core or unrelated Solr data is touched.
        client.post(f"{base}/update", json={"delete": {"query": "*:*"}}).raise_for_status()
        counts = {}
        for collection in catalog.collections:
            path = catalog.path / f"{collection}.parquet"
            if digest(path) != catalog.manifest["files"][path.name]:
                raise ValueError(f"Parquet checksum mismatch: {path.name}")
            count = 0
            for batch in pq.ParquetFile(path).iter_batches(batch_size=batch_size):
                documents = []
                for row in batch.to_pylist():
                    document = {k: v for k, v in row.items() if v is not None and k != "id"}
                    document.update(
                        id=f"{collection}|{row['id']}",
                        record_id=row["id"],
                        collection=collection,
                        payload=json.dumps({k: v for k, v in row.items() if k != "search_text"}),
                    )
                    documents.append(document)
                for attempt in range(4):
                    try:
                        response = client.post(f"{base}/update", json=documents)
                        response.raise_for_status()
                        break
                    except (httpx.TransportError, httpx.HTTPStatusError):
                        if attempt == 3:
                            raise
                        time.sleep(2**attempt)
                count += len(documents)
            counts[collection] = count
            print(f"Indexed {collection}: {count:,}", flush=True)
        client.post(f"{base}/update", json={"commit": {}}).raise_for_status()
        for collection, expected in counts.items():
            response = client.get(f"{base}/select", params={"q": f"collection:{collection}", "rows": 0})
            response.raise_for_status()
            if response.json()["response"]["numFound"] != expected:
                raise ValueError(f"Solr count mismatch: {collection}")
    receipt = {"url": url, "core": core, "build_id": catalog.build_id, "counts": counts}
    write_json(receipt_path, receipt)
    write_json(catalog.data / "published.json", receipt)
    return receipt


class SolrBackend:
    name = "solr"

    def __init__(self, catalog: Catalog, url: str):
        self.catalog = catalog
        self.base = url.rstrip("/") + "/amigo3_" + catalog.build_id

    def query(self, query: Query) -> dict:
        self.catalog.validate(query)
        cursor = decode_cursor(self.catalog.build_id, query) or "*"
        filters = [f'collection:"{query.collection}"']
        for index, f in enumerate(query.filters):
            filters.append(f"{{!tag=f{index}}}" + filter_expression(f))
        search = (
            " AND ".join(escape(token) for token in query.search.split()) if query.search.strip() else "*:*"
        )
        facets = {}
        for index, (_, facet) in enumerate(query.facets.items()):
            exclusions = [f"f{i}" for i, f in enumerate(query.filters) if f.id in facet.exclude_filters]
            if facet.values is not None:
                facets[f"facet{index}"] = {
                    "type": "query",
                    "q": "*:*",
                    "domain": {"excludeTags": exclusions},
                    "facet": {
                        f"value{i}": {"type": "query", "q": f'{field_name(facet.field)}:"{escape(v)}"'}
                        for i, v in enumerate(dict.fromkeys(facet.values))
                    },
                }
                continue
            facets[f"facet{index}"] = {
                "type": "terms",
                "field": field_name(facet.field),
                "limit": facet.limit,
                "sort": "count desc",
                "numBuckets": True,
                "mincount": 1,
                "domain": {"excludeTags": exclusions},
            }
        for index, spec in enumerate(query.breakdowns.values()):
            for dimension, field in [("rows", spec.field), ("columns", spec.split_by)]:
                if field:
                    facets[f"breakdown{index}_{dimension}"] = {
                        "type": "terms",
                        "field": field_name(field),
                        "limit": spec.limit,
                        "sort": "count desc",
                        "mincount": 1,
                    }
        for index, spec in enumerate(query.histograms.values()):
            # Exact value distributions; binning is shared with the DuckDB engine.
            values = {"type": "terms", "field": spec.field, "limit": -1, "mincount": 1}
            if spec.y_field:
                values["facet"] = {"y": {"type": "terms", "field": spec.y_field, "limit": -1, "mincount": 1}}
            facets[f"histogram{index}"] = {
                "type": "query",
                "q": "*:*",
                "domain": {
                    "excludeTags": [
                        f"f{i}" for i, f in enumerate(query.filters) if f.id in spec.exclude_filters
                    ]
                },
                "facet": {"values": values},
            }
        ordering, sort_params = [], {}
        for index, clause in enumerate(resolve_sort(self.catalog.collections[query.collection], query)):
            field = field_name(clause.field)
            if clause.value is not None:
                key = f"sort_match_{index}"
                sort_params[key] = f'{field}:"{escape(clause.value)}"'
                field = f"if(query(${key}),1,0)"
            ordering.append(f"{field} {clause.direction}")
        # Solr cursors require the actual uniqueKey, distinct from record_id.
        ordering.append("id asc")
        body = {
            "query": search,
            "filter": filters,
            "limit": query.page.size,
            "sort": ", ".join(ordering),
            "fields": ["payload"],
            "facet": facets,
            "params": {"defType": "edismax", "qf": "search_text", "cursorMark": cursor, **sort_params},
        }
        response = httpx.post(f"{self.base}/query", json=body, timeout=60)
        response.raise_for_status()
        result = response.json()
        # First rank both dimensions globally; then count only their bounded
        # intersections. Per-row top terms would yield misleading heatmap zeros.
        dimensions, intersections = {}, {}
        for index, (key, spec) in enumerate(query.breakdowns.items()):
            rows, columns = (
                [
                    (b["val"], b["count"])
                    for b in result.get("facets", {})
                    .get(f"breakdown{index}_{dimension}", {})
                    .get("buckets", [])
                ]
                for dimension in ("rows", "columns")
            )
            dimensions[key] = (rows, columns)
            if rows and self.catalog.collections[query.collection]["fields"][spec.field]["multivalued"]:
                intersections[f"covered{index}"] = {
                    "type": "query",
                    "q": filter_expression(
                        Filter(id=spec.field, field=spec.field, values=[v for v, _ in rows])
                    ),
                }
            for ri, (row, _) in enumerate(rows):
                for ci, (column, _) in enumerate(columns):
                    clauses = [
                        filter_expression(Filter(id=field, field=field, values=[value]))
                        for field, value in [(spec.field, row), (spec.split_by, column)]
                    ]
                    intersections[f"cell{index}_{ri}_{ci}"] = {
                        "type": "query",
                        "q": " AND ".join(clauses),
                    }
        cell_counts = {}
        if intersections:
            response = httpx.post(
                f"{self.base}/query",
                json={
                    "query": search,
                    "filter": filters,
                    "limit": 0,
                    "facet": intersections,
                    "params": {"defType": "edismax", "qf": "search_text"},
                },
                timeout=60,
            )
            response.raise_for_status()
            cell_counts = response.json().get("facets", {})
        breakdowns = {}
        for index, (key, spec) in enumerate(query.breakdowns.items()):
            rows, columns = dimensions[key]
            cells = {
                (row, column): cell_counts[f"cell{index}_{ri}_{ci}"]["count"]
                for ri, (row, _) in enumerate(rows)
                for ci, (column, _) in enumerate(columns)
            }
            breakdowns[key] = breakdown_result(
                spec,
                result["response"]["numFound"],
                rows,
                columns,
                cells,
                covered=cell_counts.get(f"covered{index}", {}).get("count"),
                multivalued=self.catalog.collections[query.collection]["fields"][spec.field]["multivalued"],
            )
        hits = [json.loads(doc["payload"]) for doc in result["response"]["docs"]]
        if query.select:
            hits = [{k: row.get(k) for k in query.select} for row in hits]
        returned_facets = {}
        for index, (key, facet) in enumerate(query.facets.items()):
            values = result.get("facets", {}).get(f"facet{index}", {})
            buckets = [{"value": b["val"], "count": b["count"]} for b in values.get("buckets", [])]
            if facet.values is not None:
                buckets = [
                    {"value": v, "count": values.get(f"value{i}", {}).get("count", 0)}
                    for i, v in enumerate(dict.fromkeys(facet.values))
                ]
            returned_facets[key] = {
                "field": facet.field,
                "buckets": buckets,
                "count_unit": "records",
                "exact": True,
                "truncated": values.get("numBuckets", 0) > len(buckets),
            }
        histograms = {}
        for index, (key, spec) in enumerate(query.histograms.items()):
            facet = result.get("facets", {}).get(f"histogram{index}", {})
            buckets = facet.get("values", {}).get("buckets", [])
            if spec.y_field:
                counts = {
                    (b["val"], y["val"]): y["count"]
                    for b in buckets
                    for y in b.get("y", {}).get("buckets", [])
                }
            else:
                counts = {b["val"]: b["count"] for b in buckets}
            histograms[key] = histogram_result(spec, facet.get("count", 0), counts)
        next_cursor = result.get("nextCursorMark")
        return {
            "build_id": self.catalog.build_id,
            "collection": query.collection,
            "total": result["response"]["numFound"],
            "total_exact": True,
            "hits": hits,
            "facets": returned_facets,
            "breakdowns": breakdowns,
            "histograms": histograms,
            "next_cursor": encode_cursor(self.catalog.build_id, query, next_cursor)
            if hits and next_cursor and next_cursor != cursor
            else None,
        }

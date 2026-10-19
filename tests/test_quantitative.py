import pytest
from pydantic import ValidationError

from amigo3.breakdowns import bin_edges, histogram_result
from amigo3.catalog import Catalog
from amigo3.duck import DuckBackend
from amigo3.metrics import gocam_metrics, module_metrics
from amigo3.query import Filter, Histogram, Query


def activity(identifier, downstream=(), molecules=()):
    return {
        "id": identifier,
        "enabled_by": {"term": "UniProtKB:" + identifier},
        "causal_associations": [{"downstream_activity": d} for d in downstream],
        "molecular_associations": [{"predicate": p, "molecule": m} for p, m in molecules],
    }


def test_gocam_metrics_follow_gocam_py_graph():
    model = {
        "activities": [
            activity("a", ["b"]),
            activity("b", ["c"]),
            # c's output is d's input: an implicit molecule-mediated edge.
            activity("c", molecules=[("RO:0002234", "m")]),
            activity("d", ["a"], molecules=[("RO:0002233", "m")]),
            activity("lonely"),
            {"id": "unenabled", "causal_associations": [{"downstream_activity": "a"}]},
        ]
    }
    metrics = gocam_metrics(model)
    assert metrics == {
        "activity_count": 6,
        "enabled_by_count": 5,
        "causal_edge_count": 4,
        # A cycle a→b→c→d→a: the longest shortest path is 3 edges, not unbounded.
        "longest_path_length": 3,
        "component_count": 2,
    }


def test_module_metrics_count_and_or_structure():
    module = {
        "id": "root",
        "parts": [
            {"order": 1, "node": {"id": "s1", "annotons": [{"id": "x"}]}},
            {
                "order": 2,
                "node": {
                    "id": "s2",
                    "variant_sets": [
                        {
                            "selection": "EXACTLY_ONE",
                            "variants": [{"id": "v1"}, {"id": "v2"}, {"id": "v3"}],
                        }
                    ],
                },
            },
            {"order": 3, "optional": True, "node": {"id": "s3"}},
        ],
        "variant_sets": [{"selection": "ONE_OR_MORE", "variants": [{"id": "r1"}, {"id": "r2"}]}],
        "connections": [
            {"source": "s3", "target": "after", "connection_type": "PRECEDES"},
            {"source": "s1", "target": "root", "connection_type": "PART_OF"},
        ],
    }
    metrics = module_metrics(module)
    assert metrics["part_count"] == 3
    assert metrics["variant_set_count"] == 2
    assert metrics["variant_count"] == 5
    # 1 × 3 variants × (optional: 2) × (one or more of 2: 3)
    assert metrics["realization_count"] == 18
    assert metrics["max_depth"] == 2
    assert metrics["annoton_count"] == 1
    assert metrics["connection_count"] == 2
    # s1 → s2 → s3 (part order) → after (connection); PART_OF is not a step.
    assert metrics["longest_path_length"] == 3


def test_bins_cover_range_without_gaps():
    for low, high, bins, scale in [(0, 132, 12, "log"), (1, 54684, 12, "log"), (0, 28, 12, "linear")]:
        edges = bin_edges(low, high, bins, scale)
        assert len(edges) <= bins
        assert edges[0][0] == low and edges[-1][1] == high
        assert all(a[1] + 1 == b[0] for a, b in zip(edges, edges[1:], strict=False))
    assert bin_edges(2, 4, 12) == [(2, 2), (3, 3), (4, 4)]
    # Reserving the non-positive log bucket must not leave zero bins for the rest.
    for scale in ("linear", "log"):
        assert bin_edges(0, 10, 1, scale) == [(0, 10)]
        assert bin_edges(-3, 10, 1, scale) == [(-3, 10)]
    assert bin_edges(0, 10, 2, "log") == [(0, 0), (1, 10)]


@pytest.mark.parametrize("scale", ["linear", "log"])
def test_one_bin_histograms_with_zero_in_domain(scale):
    plain = histogram_result(Histogram(field="x", bins=1, scale=scale), 2, {0: 1, 10: 1})
    assert plain["buckets"] == [{"start": 0, "end": 10, "count": 2}]
    joint = histogram_result(
        Histogram(field="x", y_field="y", bins=1, y_bins=1, scale=scale, y_scale=scale),
        2,
        {(0, 0): 1, (10, 10): 1},
    )
    assert joint["cells"] == [[2]]
    assert joint["y_buckets"] == [{"start": 0, "end": 10, "count": 2}]


def test_realization_count_saturates_exactly_at_cap():
    from amigo3.metrics import REALIZATION_CAP

    def subsets(n, selection):
        return {"variant_sets": [{"selection": selection, "variants": [{"id": str(i)} for i in range(n)]}]}

    # 2**40 - 1 and 2**40 both exceed the cap; 2**20 - 1 does not.
    assert module_metrics(subsets(40, "ONE_OR_MORE"))["realization_count"] == REALIZATION_CAP
    assert module_metrics(subsets(40, "ZERO_OR_MORE"))["realization_count"] == REALIZATION_CAP
    assert module_metrics(subsets(20, "ONE_OR_MORE"))["realization_count"] == 2**20 - 1


def test_histogram_result_bins_exact_distribution():
    spec = Histogram(field="x", y_field="y", bins=2, y_bins=2)
    result = histogram_result(spec, 10, {(0, 0): 2, (1, 5): 3, (9, 5): 4})
    assert result["missing"] == 1
    assert [b["count"] for b in result["buckets"]] == [5, 4]
    assert result["cells"] == [[2, 3], [0, 4]]


def test_between_requires_ordered_integer_pair():
    with pytest.raises(ValidationError):
        Filter(id="x", field="x", op="between", values=[5, 1])
    with pytest.raises(ValidationError):
        Filter(id="x", field="x", op="between", values=["1", "5"])


def test_knowledge_records_carry_metrics(built_data):
    catalog = Catalog(built_data)
    backend = DuckBackend(catalog)
    modules = catalog.collections["modules"]
    assert "realization_count" in modules["range_facets"]
    assert modules["dashboard"]["joint"] == ["part_count", "variant_set_count"]
    module = backend.query(Query(collection="modules"))["hits"][0]
    assert (module["variant_set_count"], module["variant_count"], module["realization_count"]) == (1, 2, 2)
    model = backend.query(Query(collection="gocams"))["hits"][0]
    assert (model["activity_count"], model["longest_path_length"], model["component_count"]) == (1, 0, 1)


def test_range_filters_and_histograms(built_data):
    backend = DuckBackend(Catalog(built_data))
    query = Query(
        collection="modules",
        filters=[Filter(id="variants", field="variant_count", op="between", values=[3, 10])],
        histograms={
            "own": Histogram(field="variant_count", exclude_filters=["variants"]),
            "all": Histogram(field="realization_count", scale="log"),
        },
    )
    result = backend.query(query)
    assert result["total"] == 0
    # The slider's own histogram ignores its filter so the full domain stays visible.
    assert result["histograms"]["own"]["min"] == 2
    assert result["histograms"]["own"]["total"] == 1
    assert result["histograms"]["all"]["buckets"] == []
    query.filters[0].values = [2, 2]
    assert backend.query(query)["total"] == 1


def test_histograms_reject_non_integer_fields(built_data):
    backend = DuckBackend(Catalog(built_data))
    with pytest.raises(ValueError, match="integer"):
        backend.query(Query(collection="modules", histograms={"bad": Histogram(field="kind")}))
    with pytest.raises(ValueError, match="integer"):
        backend.query(
            Query(collection="modules", filters=[Filter(id="k", field="kind", op="between", values=[1, 2])])
        )

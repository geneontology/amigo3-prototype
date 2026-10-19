import httpx
import pytest
from fastapi.testclient import TestClient

from amigo3.api import create_app
from amigo3.genesets import GenesetClient, examples, go_derived, member_candidates, source_url

MSIG_H = {"category": {"code": "H"}, "subcategory": {"code": ""}}
MSIG_GO = {"category": {"code": "C5"}, "subcategory": {"code": "GO:BP"}}
LIVE = {
    "_id": "WP_LIVE",
    "name": "Live pathway",
    "source": "wikipathways",
    "taxid": 9606,
    "count": 5,
    "wikipathways": {"url": "https://www.wikipathways.org/instance/WP_LIVE"},
    "genes": [
        # The stable identifier wins over a stale symbol.
        {"symbol": "STALE", "ncbigene": "1"},
        {"symbol": "GENE2"},
        {"symbol": "gene2"},
        {"uniprot": ["P00003", "P00002"], "symbol": "OTHER"},
        {"symbol": "MISSING", "ncbigene": "999"},
    ],
}
HITS = [
    {"_id": "WP_LIVE", "name": "Live pathway", "source": "wikipathways", "taxid": 9606, "count": 5},
    {"_id": "HALLMARK_X", "name": "hallmark x", "source": "msigdb", "count": 9, "msigdb": MSIG_H},
    # Defence in depth: a GO set slipping past the query filter is dropped.
    {"_id": "GOBP_X", "source": "msigdb", "count": 3, "msigdb": MSIG_GO},
]
DOCS = {
    "WP_LIVE": LIVE,
    "GO_0006281_9606": {"_id": "GO_0006281_9606", "source": "go", "genes": []},
    "GOBP_X": {"_id": "GOBP_X", "source": "msigdb", "msigdb": MSIG_GO, "genes": []},
}


def mock_client(calls=None, status=200):
    def handler(request: httpx.Request):
        if calls is not None:
            calls.append(request)
        if status != 200:
            return httpx.Response(status)
        if request.url.path == "/v1/query":
            return httpx.Response(200, json={"total": 3, "hits": HITS})
        identifier = request.url.path.removeprefix("/v1/geneset/")
        if identifier in DOCS:
            return httpx.Response(200, json=DOCS[identifier])
        return httpx.Response(404, json={"success": False})

    return GenesetClient("https://mygeneset.test/v1", transport=httpx.MockTransport(handler))


def test_member_candidates_prefer_stable_identifiers():
    assert member_candidates(
        {
            "ncbigene": "5426",
            "uniprot": ["Q1", "Q2"],
            "ensemblgene": "ENSG1",
            "symbol": "POLE",
            "source_id": "5426",
        }
    ) == ["NCBIGene:5426", "UniProtKB:Q1", "UniProtKB:Q2", "ENSEMBL:ENSG1", "POLE"]
    assert member_candidates({"ensemblgene": "WBGene00004789", "symbol": "sgk-1"}) == [
        "WB:WBGene00004789",
        "sgk-1",
    ]
    assert member_candidates({"ensemblgene": "SPCC622.17", "source_id": "apn1"}) == [
        "PomBase:SPCC622.17",
        "apn1",
    ]


def test_source_urls_and_go_detection():
    assert (
        source_url({"_id": "R-HSA-1", "source": "reactome"}) == "https://reactome.org/content/detail/R-HSA-1"
    )
    assert source_url({"_id": "WP1", "source": "wikipathways"}).endswith("/WP1")
    assert source_url({"_id": "HALLMARK_X", "source": "msigdb"}).endswith("/human/geneset/HALLMARK_X.html")
    assert go_derived({"source": "go"}) and go_derived({"source": "msigdb", "msigdb": MSIG_GO})
    assert not go_derived({"source": "msigdb", "msigdb": MSIG_H}) and not go_derived({"source": "ctd"})


def test_bundled_examples_are_independent_signatures_with_expectations():
    data = examples()
    provenance = data["provenance"]
    assert provenance["service"] == "https://mygeneset.info/v1" and provenance["retrieved"]
    assert "CC BY 4.0" in provenance["license"] and "genesets" in provenance["expected_go"]
    assert data["genesets"]
    for doc in data["genesets"]:
        assert doc["source"] == "msigdb" and not go_derived(doc)
        msigdb = doc["msigdb"]
        assert msigdb["category"]["code"] in ("H", "C8") or msigdb["subcategory"]["code"] == "CGP"
        assert doc["count"] == len(doc["genes"]) > 0
        assert all(member_candidates(member) for member in doc["genes"])
        assert doc["expected_go"] and all(
            t["id"].startswith("GO:") and t["label"] for t in doc["expected_go"]
        )


def test_examples_live_search_and_member_mapping(built_data):
    calls = []
    with TestClient(create_app(built_data, "duckdb", genesets=mock_client(calls))) as client:
        listing = client.get("/api/genesets", params={"species": "human"}).json()
        assert listing["taxa"] == ["9606"] and listing["search"]["enabled"]
        assert set(listing["collections"]) >= {"signatures", "pathways", "all"}
        first = listing["examples"][0]
        assert all(e["taxid"] == "9606" and e["expected_go"] for e in listing["examples"])
        assert first["collection"].startswith("MSigDB ") and first["name"] == first["id"]
        pombe = client.get("/api/genesets", params={"species": "pombe"}).json()
        assert pombe["taxa"] == ["284812", "4896"] and pombe["examples"] == []

        found = client.get("/api/genesets/search", params={"species": "human", "q": "pathway"}).json()
        assert [h["id"] for h in found["hits"]] == ["WP_LIVE", "HALLMARK_X"]
        assert found["hits"][1]["collection"] == "MSigDB H"
        assert calls[-1].url.params["species"] == "9606"
        query = calls[-1].url.params["q"]
        assert query.startswith("(pathway) AND (msigdb.category.code:(h OR c4 OR c6 OR c7 OR c8)")
        assert query.endswith("AND NOT source:go AND NOT msigdb.subcategory.code:go* AND count:>0")
        client.get("/api/genesets/search", params={"species": "human", "q": "pathway", "collection": "all"})
        assert calls[-1].url.params["q"].startswith("(pathway) AND * AND NOT source:go")
        bad = client.get(
            "/api/genesets/search", params={"species": "human", "q": "pathway", "collection": "go"}
        )
        assert bad.status_code == 422
        for go_set in ("GO_0006281_9606", "GOBP_X"):
            refused = client.get(f"/api/genesets/{go_set}", params={"species": "human"})
            assert refused.status_code == 422 and "circular" in refused.json()["detail"]

        live = client.get("/api/genesets/WP_LIVE", params={"species": "human"}).json()
        assert live["origin"] == "mygeneset" and live["species_match"] and live["expected_go"] == []
        vias = [(m["label"], m["gene"] and m["gene"]["id"], m["via"]) for m in live["members"]]
        assert vias == [
            ("STALE", "HGNC:1", "NCBIGene:1"),
            ("GENE2", "HGNC:2", "GENE2"),
            ("gene2", "HGNC:2", "gene2"),
            ("OTHER", "HGNC:2", "UniProtKB:P00002"),
            ("MISSING", None, ""),
        ]
        assert live["genes"] == ["GENE1", "GENE2", "MISSING"]  # Readable, deduplicated, unmapped kept.
        assert (live["mapped"], live["unmapped"], live["member_count"]) == (2, 1, 5)

        bundled = client.get(f"/api/genesets/{first['id']}", params={"species": "human"}).json()
        assert bundled["origin"] == "bundled" and bundled["provenance"]["retrieved"]
        assert bundled["member_count"] == first["count"] and bundled["expected_go"] == first["expected_go"]
        assert not any(c.url.path.endswith(first["id"]) for c in calls)  # Served offline.

        assert client.get("/api/genesets/NOPE", params={"species": "human"}).status_code == 404
        assert client.get("/api/genesets/search", params={"species": "human", "q": "x"}).status_code == 422


@pytest.mark.parametrize("status", [500, 503])
def test_upstream_failure_is_reported(built_data, status):
    with TestClient(create_app(built_data, "duckdb", genesets=mock_client(status=status))) as client:
        response = client.get("/api/genesets/search", params={"species": "human", "q": "pathway"})
        assert response.status_code == 502 and "MyGeneset" in response.json()["detail"]


def test_disabled_search_keeps_bundled_examples(built_data):
    with TestClient(create_app(built_data, "duckdb", genesets=GenesetClient(""))) as client:
        listing = client.get("/api/genesets", params={"species": "human"}).json()
        assert listing["search"] == {"enabled": False, "service": None}
        assert client.get("/api/genesets/search", params={"species": "human", "q": "dna"}).status_code == 503
        assert client.get("/api/genesets/WP_LIVE", params={"species": "human"}).status_code == 503
        example = listing["examples"][0]["id"]
        assert client.get(f"/api/genesets/{example}", params={"species": "human"}).status_code == 200

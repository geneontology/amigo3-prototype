"""Optional real Solr backup/restore roundtrip against an isolated local server."""

import json
import os
import uuid
from pathlib import Path

import pytest
from test_release import release

from amigo3.catalog import Catalog
from amigo3.solr import publish


@pytest.mark.skipif(not os.getenv("AMIGO3_RELEASE_TEST_SOLR_HOME"), reason="Needs an isolated Solr home")
def test_committed_index_and_configuration_restore(built_data, tmp_path):
    url = os.environ.get("AMIGO3_RELEASE_TEST_SOLR_URL", "http://127.0.0.1:18987/solr")
    home = Path(os.environ["AMIGO3_RELEASE_TEST_SOLR_HOME"])
    catalog = Catalog(built_data)
    receipt = publish(catalog, url)
    archive = tmp_path / "roundtrip.tar.gz"
    release.package(
        built_data, home / receipt["core"], url, "example/repo@sha256:" + "a" * 64, archive, "b" * 40
    )
    # Restore into the isolated Solr home, but register under a different core name
    # so we compare two independently opened indexes rather than reusing the source.
    destination = home / ("release-test-" + uuid.uuid4().hex)
    release.extract_release(archive, destination)
    restored = "restored_" + uuid.uuid4().hex
    core_dir = destination / "solr" / receipt["core"]
    # EC2 discovers core.properties at startup. This already-running test server
    # instead needs CREATE, which insists on writing core.properties itself.
    (core_dir / "core.properties").unlink()
    release.request_json(
        url + "/admin/cores", {"action": "CREATE", "name": restored, "instanceDir": str(core_dir)}
    )
    try:
        for collection, expected in receipt["counts"].items():
            params = {"q": f"collection:{collection}", "rows": 100, "sort": "id asc"}
            source = release.request_json(f"{url}/{receipt['core']}/select", params)["response"]
            target = release.request_json(f"{url}/{restored}/select", params)["response"]
            assert target["numFound"] == expected
            assert [json.loads(row["payload"]) for row in source["docs"]] == [
                json.loads(row["payload"]) for row in target["docs"]
            ]
    finally:
        release.request_json(
            url + "/admin/cores", {"action": "UNLOAD", "core": restored, "deleteInstanceDir": "true"}
        )

"""Presentation choices must refer to fields in the published projection."""

from amigo3.catalog import Catalog


def test_display_fields_and_presets_resolve(built_data):
    for name, config in Catalog(built_data).collections.items():
        available = set(config["display_fields"])
        virtual = {"assessments"} if name == "annotations" else set()
        assert available
        assert available <= set(config["fields"]) | virtual, name
        assert "search_text" not in available
        assert config["display_presets"]["compact"]["fields"] == []
        for preset in config["display_presets"].values():
            assert set(preset["fields"]) <= available, name


def test_progressive_facets_are_available_and_declared(built_data):
    for config in Catalog(built_data).collections.values():
        for group in config.get("facet_groups", []):
            for field in group["fields"] + group.get("more_fields", []):
                assert config["fields"][field]["facetable"], field
            rule = group.get("visible_when", {})
            assert set(rule.get("display_fields", [])) <= set(config["display_fields"])
            for field in rule.get("any_true_filters", []):
                assert config["fields"][field]["type"] == "boolean"

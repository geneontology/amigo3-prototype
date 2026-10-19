"""The module structure tab opens with a compact and/or diagram that links into the tree."""

from playwright.sync_api import expect


def leaf(id, label, module_type="MOLECULAR_FUNCTION"):
    return {"id": id, "label": label, "module_type": module_type}


MODULE = {
    "id": "root",
    "label": "Signaling",
    "module_type": "SIGNALING_PATHWAY",
    "parts": [
        # Source order differs from step order; the diagram follows the steps.
        {"order": 2, "role": "cleavage", "node": leaf("cleave", "Receptor cleavage", "REACTION")},
        {"order": 1, "role": "binding", "node": leaf("bind", "Ligand binding")},
        {
            "order": 3,
            "node": {
                "id": "signal",
                "label": "Nuclear signal",
                "module_type": "BIOLOGICAL_PROCESS",
                "parts": [
                    {"order": 1, "node": leaf("deep", "Deep step")},
                    {"order": 2, "optional": True, "node": leaf("extra", "Optional boost")},
                ],
            },
        },
    ],
    "variant_sets": [
        {
            "id": "ligands",
            "label": "Ligand",
            "selection": "ONE",
            "variants": [leaf("dll", "Delta-like"), leaf("jag", "Jagged")],
        }
    ],
}


def mock_module(page):
    def fulfill(route):
        response = route.fetch()
        data = response.json()
        data["documents"]["module"]["module"] = MODULE
        route.fulfill(response=response, json=data)

    page.route("**/api/resources/modules/*", fulfill)


def test_module_diagram_shows_steps_alternatives_and_opens_tree(page):
    mock_module(page)
    page.goto("/modules/MODULE%3Atest")
    diagram = page.locator(".module-diagram")
    expect(diagram).to_be_visible()
    root_flow = diagram.locator(".md-scroll > .md-cluster > .md-flow")
    labels = root_flow.locator(":scope > .md-node .md-label, :scope > .md-cluster > .md-node .md-label")
    expect(labels).to_have_text(["Ligand binding", "Receptor cleavage", "Nuclear signal"])
    choice = diagram.get_by_role("group", name="Ligand: one of")
    expect(choice.locator(".md-choice-label")).to_have_text("one of · Ligand")
    expect(choice.locator(".md-label")).to_have_text(["Delta-like", "Jagged"])
    optional = diagram.locator(".md-node", has_text="Optional boost")
    expect(optional).to_contain_text("optional")
    assert optional.evaluate("e => getComputedStyle(e).borderTopStyle") == "dashed"
    assert (
        diagram.locator(".md-node", has_text="Deep step").evaluate("e => getComputedStyle(e).borderTopStyle")
        == "solid"
    )

    # The tree starts with depth-2 nodes closed; the diagram opens and reveals them.
    entry = page.locator("#module-node-m-p2-p1")
    expect(entry).not_to_have_attribute("open", "")
    optional.click()
    expect(entry).to_have_attribute("open", "")
    expect(entry.locator(":scope > summary")).to_be_in_viewport()
    expect(entry.locator(":scope > summary")).to_be_focused()
    expect(entry.locator(":scope > summary")).to_contain_text("Optional boost")
    diagram.locator(".md-node", has_text="Jagged").click()
    expect(page.locator("#module-node-m-v0-1 > summary")).to_contain_text("Jagged")
    expect(page.locator("#module-node-m-v0-1 > summary")).to_be_in_viewport()


def test_module_diagram_scrolls_inside_itself_on_mobile(page):
    mock_module(page)
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto("/modules/MODULE%3Atest")
    scroll = page.locator(".module-diagram .md-scroll")
    expect(scroll).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    assert scroll.evaluate("e => e.scrollWidth > e.clientWidth")
    node = page.locator(".module-diagram .md-node", has_text="Jagged")
    node.scroll_into_view_if_needed()
    node.click()
    expect(page.locator("#module-node-m-v0-1 > summary")).to_be_in_viewport()


def test_module_without_parts_has_no_diagram(page):
    def fulfill(route):
        response = route.fetch()
        data = response.json()
        data["documents"]["module"]["module"] = leaf("root", "Single step", "REACTION")
        route.fulfill(response=response, json=data)

    page.route("**/api/resources/modules/*", fulfill)
    page.goto("/modules/MODULE%3Atest")
    expect(page.locator("#module-node-m > summary")).to_contain_text("Single step")
    expect(page.locator(".module-diagram")).to_have_count(0)

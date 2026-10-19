"""Record captioned feature tours of a running AmiGO 3 server.

Each tour drives the real UI with Playwright: README screenshots go to
docs/images/<name>.png (overlays hidden), and each tour's recording becomes an
MP4 and a looping GIF under showcase/ (not committed; the README links the
YouTube uploads).

    just serve                       # in another terminal
    just showcase                    # every tour
    just showcase --tour enrichment  # one tour
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Annotated

import typer
from playwright.sync_api import Locator, Page, expect, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
VIEWPORT = {"width": 1440, "height": 900}

# A visible cursor and a caption bar; headless recordings show neither by default.
OVERLAY = """
window.addEventListener('DOMContentLoaded', () => {
  const style = document.createElement('style');
  style.textContent = `
    #showcase-cursor { position: fixed; z-index: 2147483647; width: 22px; height: 22px; margin: -11px 0 0 -11px;
      border-radius: 50%; background: rgba(214, 69, 65, .35); border: 2px solid rgba(214, 69, 65, .9);
      pointer-events: none; transition: transform .15s; left: -40px; top: -40px; }
    #showcase-cursor.down { transform: scale(.7); }
    #showcase-caption { position: fixed; z-index: 2147483646; left: 50%; bottom: 36px; transform: translateX(-50%);
      max-width: 1100px; padding: 14px 26px; border-radius: 10px; background: rgba(6, 22, 66, .92); color: #fff;
      font: 600 24px/1.35 system-ui, sans-serif; text-align: center; box-shadow: 0 6px 24px rgba(0,0,0,.25);
      pointer-events: none; opacity: 0; transition: opacity .3s; }
    #showcase-caption.on { opacity: 1; }
    .showcase-hidden #showcase-cursor, .showcase-hidden #showcase-caption { display: none; }`;
  document.head.append(style);
  const cursor = Object.assign(document.createElement('div'), {id: 'showcase-cursor'});
  const caption = Object.assign(document.createElement('div'), {id: 'showcase-caption'});
  document.body.append(cursor, caption);
  const saved = sessionStorage.getItem('showcase-cursor');
  if (saved) { const [x, y] = JSON.parse(saved); cursor.style.left = x + 'px'; cursor.style.top = y + 'px'; }
  addEventListener('mousemove', e => {
    cursor.style.left = e.clientX + 'px'; cursor.style.top = e.clientY + 'px';
    sessionStorage.setItem('showcase-cursor', JSON.stringify([e.clientX, e.clientY]));
  }, true);
  addEventListener('mousedown', () => cursor.classList.add('down'), true);
  addEventListener('mouseup', () => cursor.classList.remove('down'), true);
});
"""


class Director:
    """Paced interactions with a visible cursor, captions and clean screenshots."""

    def __init__(self, page: Page, base_url: str, images: Path):
        self.page, self.base_url, self.images = page, base_url, images
        self.mouse = (VIEWPORT["width"] / 2, VIEWPORT["height"] / 2)

    def goto(self, path: str, ready: str) -> None:
        self.page.goto(self.base_url + path)
        expect(self.page.locator(ready).first).to_be_visible(timeout=60_000)
        self.page.wait_for_timeout(800)

    def caption(self, text: str, hold: int = 2200) -> None:
        self.page.evaluate(
            "t => { const c = document.getElementById('showcase-caption'); c.textContent = t; c.classList.add('on'); }",
            text,
        )
        self.page.wait_for_timeout(hold)

    def uncaption(self) -> None:
        self.page.evaluate("document.getElementById('showcase-caption').classList.remove('on')")

    def point(self, target: Locator) -> None:
        target.scroll_into_view_if_needed()
        box = target.bounding_box()
        x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        self.page.mouse.move(x, y, steps=25)
        self.mouse = (x, y)
        self.page.wait_for_timeout(300)

    def click(self, target: Locator, settle: int = 1200) -> None:
        self.point(target)
        target.click()
        self.page.wait_for_timeout(settle)

    def type(self, target: Locator, text: str) -> None:
        self.click(target, settle=200)
        target.press_sequentially(text, delay=70)
        self.page.wait_for_timeout(400)

    def select(self, target: Locator, value: str | None = None, index: int | None = None) -> None:
        self.point(target)
        target.select_option(value=value, index=index)
        self.page.wait_for_timeout(1200)

    def scroll(self, pixels: int) -> None:
        self.page.mouse.wheel(0, pixels)
        self.page.wait_for_timeout(900)

    def shot(self, name: str) -> None:
        """A README screenshot of the current viewport, without the tour overlays."""
        self.page.evaluate("document.documentElement.classList.add('showcase-hidden')")
        self.page.screenshot(path=self.images / f"{name}.png")
        self.page.evaluate("document.documentElement.classList.remove('showcase-hidden')")


def search_and_facets(d: Director) -> None:
    d.goto("/?collection=genes", "#rows .row")
    d.caption("Nine species, 221,000 genes and products, one search")
    d.shot("search")
    d.type(d.page.locator("#search"), "kinase")
    d.page.keyboard.press("Enter")
    expect(d.page.locator("#status")).to_contain_text("kinase")
    d.caption("Facets refine results; species follow the taxonomy")
    d.click(d.page.get_by_role("button", name="Expand Viridiplantae"))
    d.click(d.page.get_by_role("checkbox", name="Species: Viridiplantae"))
    expect(d.page.locator("#chips")).to_contain_text("Viridiplantae")
    d.caption("Plant kinases from Arabidopsis and poplar")
    d.shot("facets")
    d.click(d.page.locator('[data-result-view="dashboard"]'), settle=2500)
    d.scroll(380)
    d.caption("The dashboard summarizes the same selection")
    d.shot("dashboard")
    d.uncaption()


def gene_page(d: Director) -> None:
    d.goto("/?collection=genes", "#rows .row")
    d.type(d.page.locator("#search"), "TP53")
    d.page.keyboard.press("Enter")
    expect(d.page.locator("#status")).to_contain_text("TP53")
    d.click(d.page.locator("#rows .row-title").first, settle=2500)
    expect(d.page.locator("#gene-symbol")).to_have_text("TP53")
    d.caption("Gene pages lead with core functions from an AI gene review")
    d.shot("gene")
    d.click(d.page.get_by_role("tab", name="Annotations ("), settle=2000)
    d.caption("Every GO annotation, with review verdicts alongside")
    d.click(d.page.locator('[data-annotation-view="summary"]'), settle=2000)
    d.scroll(520)
    d.caption("Summary boxes roll annotations up by term")
    d.shot("gene-annotations")
    d.click(d.page.get_by_role("tab", name="Pathways ("), settle=2000)
    d.caption("GO-CAM pathways and modules that involve the gene")
    d.uncaption()


def ontology(d: Director) -> None:
    d.goto("/terms/GO:0006281", "#resource-content")
    d.click(d.page.get_by_role("button", name="Fit graph"), settle=1500)
    d.caption("GO term pages show every path to the root")
    d.shot("term")
    d.click(d.page.get_by_role("tab", name="Annotations ("), settle=2500)
    d.caption("Annotations to the term and its descendants, across species")
    d.select(d.page.locator("#term-species"), value="fly")
    d.caption("Filter to one species, evidence type or NOT annotations")
    d.shot("term-annotations")
    d.uncaption()


def enrichment(d: Director) -> None:
    d.goto("/enrichment", "#enrichment-form")
    d.caption("Gene set enrichment against GO terms or GO-CAMs")
    d.select(d.page.locator("#example-set"), index=1)
    d.caption("Start from a bundled example, a MyGeneset search or your own list")
    d.click(d.page.get_by_role("button", name="Run enrichment"), settle=1000)
    expect(d.page.locator("#enrichment-results")).to_be_visible(timeout=120_000)
    d.page.wait_for_timeout(1500)
    d.point(d.page.locator("#terms-heading"))
    d.caption("Enriched terms with set sizes, scores and the genes behind them")
    d.shot("enrichment")
    d.caption("Or test the same genes against GO-CAM network neighborhoods")
    d.select(d.page.locator("#analysis-method"), value="neighborhood")
    d.click(d.page.get_by_role("button", name="Run enrichment"), settle=1000)
    expect(d.page.locator("#graphs-tab")).to_be_visible(timeout=300_000)
    d.click(d.page.locator("#graphs-tab"), settle=4000)
    d.point(d.page.locator("#neighborhood-overview-canvas"))
    d.scroll(250)
    d.caption("Graphs show which neighborhoods are enriched and how they connect")
    d.shot("enrichment-graphs")
    d.uncaption()


def gocams(d: Director) -> None:
    d.goto("/gocams/gomodel:5745387b00001516", "#resource-content")
    d.click(d.page.get_by_role("button", name="Fit", exact=True), settle=1500)
    d.scroll(330)
    d.caption("GO-CAM models as interactive pathways")
    d.shot("gocam")
    d.click(d.page.get_by_role("tab", name="Activities ("), settle=1500)
    d.caption("Each activity with its gene, function, location and evidence")
    d.goto("/megamodels", "canvas")
    d.page.wait_for_timeout(4000)
    d.caption("The megamodel links all 2,036 GO-CAMs through shared activities")
    d.shot("megamodels")
    d.uncaption()


def modules(d: Director) -> None:
    d.goto("/modules/MODULE:abscisic_acid_signaling", "#resource-content")
    d.caption("Modules describe pathways as steps, alternatives and options")
    d.shot("module")
    d.click(d.page.get_by_role("tab", name="Species satisfiability"), settle=2500)
    d.caption("Gap filling: which steps does each species' genome satisfy?")
    d.shot("satisfiability")
    d.uncaption()


TOURS: dict[str, Callable[[Director], None]] = {
    "search-and-facets": search_and_facets,
    "gene-page": gene_page,
    "ontology": ontology,
    "enrichment": enrichment,
    "gocams": gocams,
    "modules": modules,
}


TOUR_NAMES = list(TOURS)


def encode(video: Path, name: str, videos: Path) -> None:
    """MP4 for sharing; a smaller looping GIF for slides and posts."""
    mp4 = videos / f"{name}.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-i",
            video,
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-crf",
            "23",
            "-movflags",
            "+faststart",
            mp4,
        ],
        check=True,
    )
    palette = "fps=8,scale=960:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128[p];[b][p]paletteuse=dither=bayer"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", mp4, "-filter_complex", palette, videos / f"{name}.gif"],
        check=True,
    )


def main(
    tour: Annotated[list[str], typer.Option(help="Tours to record; repeat for several.")] = TOUR_NAMES,
    base_url: Annotated[str, typer.Option(help="A running `amigo3 serve`.")] = "http://127.0.0.1:8030",
    videos: Annotated[Path, typer.Option(help="Where MP4s and GIFs go (not committed).")] = ROOT / "showcase",
    images: Annotated[Path, typer.Option(help="Where README screenshots go.")] = ROOT / "docs/images",
) -> None:
    unknown = set(tour) - TOURS.keys()
    if unknown:
        raise typer.BadParameter(f"Unknown tours {sorted(unknown)}; choose from {sorted(TOURS)}")
    if not shutil.which("ffmpeg"):
        raise typer.BadParameter("ffmpeg is required to encode recordings")
    videos.mkdir(parents=True, exist_ok=True)
    images.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for name in tour:
            with tempfile.TemporaryDirectory() as raw:
                context = browser.new_context(
                    viewport=VIEWPORT, device_scale_factor=1, record_video_dir=raw, record_video_size=VIEWPORT
                )
                context.add_init_script(OVERLAY)
                page = context.new_page()
                TOURS[name](Director(page, base_url, images))
                page.wait_for_timeout(1000)
                context.close()
                encode(Path(page.video.path()), name, videos)
            typer.echo(f"Recorded {name}")
        browser.close()


if __name__ == "__main__":
    typer.run(main)

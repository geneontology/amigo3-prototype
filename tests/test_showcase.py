"""The showcase script's command line; recording itself needs a live server and ffmpeg."""

import importlib.util
import re
from pathlib import Path

import typer
from typer.testing import CliRunner

spec = importlib.util.spec_from_file_location(
    "showcase", Path(__file__).parent.parent / "scripts/showcase.py"
)
showcase = importlib.util.module_from_spec(spec)
spec.loader.exec_module(showcase)
app = typer.Typer()
app.command()(showcase.main)


def plain(output: str) -> str:
    """CI forces colour, which splits option names with ANSI codes."""
    return re.sub(r"\x1b\[[0-9;]*m", "", output)


def test_help_lists_tour_option():
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "--tour" in plain(result.output)


def test_unknown_tour_is_rejected(tmp_path):
    result = CliRunner().invoke(app, ["--tour", "nope", "--videos", str(tmp_path), "--images", str(tmp_path)])
    assert result.exit_code == 2
    assert "Unknown tours" in plain(result.output)

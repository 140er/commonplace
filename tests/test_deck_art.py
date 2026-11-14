"""Dithered Capy art: the harness checks the spots, this file checks the generator and the own-art note."""

import importlib.util
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GENERATOR = ROOT / "scripts" / "capy_art.py"
OUTPUT = ROOT / "commonplace" / "web" / "static" / "deck-art-data.js"


def _generator():
    spec = importlib.util.spec_from_file_location("capy_art", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_art_harness():
    node = shutil.which("node")
    assert node, "Node is required for the art checks"
    result = subprocess.run(
        [node, "--test", str(Path(__file__).with_name("deck_art.mjs"))],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout[-10000:] + result.stderr[-2000:]


def test_generator_reproduces_the_committed_art_byte_for_byte():
    capy_art = _generator()
    first = capy_art.render()
    assert first == capy_art.render()
    assert OUTPUT.read_bytes() == first.encode("utf-8")
    assert "\r" not in first


def test_generator_check_mode_passes_on_the_committed_art():
    result = subprocess.run(
        ["python3", str(GENERATOR), "--check"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_sources_are_our_own_small_images_and_nothing_else_feeds_the_art():
    capy_art = _generator()
    sources = sorted(path.name for path in capy_art.SOURCES.glob("*.png"))
    assert sources == sorted(f"{name}.png" for name in capy_art.NAMES)
    for path in capy_art.SOURCES.glob("*.png"):
        assert path.stat().st_size < 2048
    for name in capy_art.NAMES:
        assert len(capy_art.dither_svg(name)) < 4096


def test_own_art_policy_is_written_down():
    note = (ROOT / "docs" / "system" / "own-dither-art-only.md").read_text(encoding="utf-8")
    assert "reference only" in note
    assert "scripts/capy_art.py" in note

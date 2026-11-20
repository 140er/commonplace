"""Every place a piece of copy sends the person to is a place the app really renders, and the part of the app it is in.

A pointer to "Workspace settings, under Runner machines" was a dead end for a long time (Runner machines is an Admin
section). This reads the app's real section names and routes and checks the copy that points at them, so that class of
pointer cannot come back.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "commonplace"
STATIC = ROOT / "web" / "static"

# place name -> (file, literal that proves the app renders it under that name)
RENDERED = {
    "Cloud workspaces": ("compute.js", "<h1>Cloud workspaces</h1>"),
    "Add a machine": ("compute-pairing.js", ">Add a machine</h2>"),
    "Needs me": ("rail.js", "Needs me"),
    "Admin": ("rail.js", ">Admin<"),
    "Runner machines": ("adminrunners.js", ">Runner machines</h2>"),
    "Advanced": ("compute.js", 'label: "Advanced"'),
    "Workspace settings": ("palette.js", "'Workspace settings'"),
    "Memory": ("rail.js", "Memory"),
}
# A place that lives inside another: it must be named with its parent.
PARENT = {"Runner machines": "Admin"}
# Where the copy that points people somewhere lives.
COPY = [
    STATIC / "cardruns.js",
    STATIC / "compute-pairing.js",
    STATIC / "compute.js",
    STATIC / "compute-overview.js",
    ROOT / "compute" / "overview.py",
    ROOT / "compute" / "pairing.py",
    ROOT / "runner" / "machine_setup.py",
]
# Pointers that were dead ends: never again.
DEAD = [
    r"Workspace settings,? under Runner machines",
    r"Runner machines (?:in|of) Workspace settings",
    r"Advanced,? (?:under|in) Runner machines",
]
# A route a link points at -> a literal in app.js that proves the router serves it.
ROUTES = {"#/compute": '"#/compute', "#/admin": '"#/admin"', "#/needs": '"#/needs"'}


def _text(path):
    return path.read_text(encoding="utf-8")


def test_every_place_name_is_a_heading_or_label_the_app_renders():
    for name, (file, literal) in RENDERED.items():
        assert literal in _text(STATIC / file), f"{name!r} is not rendered by {file} as {literal!r}"


def test_the_copy_that_points_somewhere_names_only_real_places_with_their_parent():
    for path in COPY:
        text = _text(path)
        for pattern in DEAD:
            assert not re.search(pattern, text), f"{path.name}: dead pointer {pattern}"
        for child, parent in PARENT.items():
            for found in re.finditer(re.escape(child), text):
                around = text[max(0, found.start() - 40) : found.end() + 30]
                if parent in around:
                    continue
                # A heading of the Admin screen itself, a comment or an identifier is not a pointer.
                line = text[text.rfind("\n", 0, found.start()) + 1 : text.find("\n", found.end())]
                assert (
                    line.lstrip().startswith(("#", "//", "-")) or "Runner machines</h2>" in line
                ), f"{path.name}: {child!r} named without {parent!r}: {line.strip()[:120]}"


def test_every_link_in_the_start_refusals_goes_to_a_route_the_app_serves():
    block = _text(STATIC / "cardruns.js")
    hrefs = set(re.findall(r'"(#/[a-z-]+)"', block[block.index("const ADD_LINK") :][:900]))
    assert hrefs >= {"#/compute", "#/admin"}, hrefs
    app = _text(STATIC / "app.js")
    for href in hrefs:
        assert href in ROUTES, f"{href} is not a known route"
        assert ROUTES[href] in app, f"the router does not serve {href}"


def test_the_start_refusals_name_admin_for_runner_machines_and_cloud_workspaces_for_adding_one():
    block = _text(STATIC / "cardruns.js")
    refusals = block[block.index("const START_REFUSALS") : block.index("const START_LINKS")]
    assert "Workspace settings" not in refusals
    assert "Add one under Add a machine in Cloud workspaces" in refusals
    assert refusals.count("Admin > Runner machines") >= 4

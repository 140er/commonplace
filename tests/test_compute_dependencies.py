"""Development installs must collect the optional Compute transport tests."""

from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib


def test_dev_extra_includes_every_compute_requirement():
    project = Path(__file__).resolve().parents[1] / "pyproject.toml"
    extras = tomllib.loads(project.read_text())["project"]["optional-dependencies"]
    missing = set(extras["compute"]) - set(extras["dev"])
    assert not missing, f"Compute requirements missing from dev: {sorted(missing)}"

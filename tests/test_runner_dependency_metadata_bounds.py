"""ABI pins must not silently satisfy an ambiguous Python patch requirement."""

import sys

import pytest
from test_runner_prefix_inventory_round import actual_archive, package

from commonplace.runner import machine_setup


@pytest.mark.parametrize("field", ["Requires-Python", "python_full_version", "inactive-extra"])
def test_abi_only_manifest_refuses_ambiguous_or_invalid_python_metadata(field):
    files, manifest = actual_archive()
    name = next(
        path
        for path in files
        if path.startswith("site/cryptography-") and path.endswith("/METADATA")
    )
    text = files[name].decode()
    version = f"{sys.version_info.major}.{sys.version_info.minor}.1"
    if field == "Requires-Python":
        text = text.replace("Requires-Python: >=3.9", f"Requires-Python: <={version}")
    elif field == "python_full_version":
        text = text.replace("python_full_version < '3.11'", f"python_full_version <= '{version}'")
    else:
        text = text.replace("bcrypt>=3.1.5", "bcrypt>=invalid")
    assert text.encode() != files[name]
    files[name] = text.encode()
    raw, pin = package(files, manifest)
    with pytest.raises(ValueError):
        machine_setup.program_contents(raw, pin)

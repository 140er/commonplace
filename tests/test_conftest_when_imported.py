"""The conftest's ``_when_imported`` patches a module whether a test loads it before or during the test."""

import sys

import pytest
from conftest import _after_import, _when_imported


@pytest.fixture
def lazy_module(tmp_path, monkeypatch):
    (tmp_path / "_lazy_target_mod.py").write_text("VALUE = 'original'\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "_lazy_target_mod", raising=False)
    yield "_lazy_target_mod"
    sys.modules.pop("_lazy_target_mod", None)


def _patch(monkeypatch):
    return lambda mod: monkeypatch.setattr(mod, "VALUE", "patched")


def test_patches_a_loaded_module_at_once(lazy_module):
    import _lazy_target_mod

    with pytest.MonkeyPatch.context() as mp:
        _when_imported(mp, lazy_module, _patch(mp))
        assert _lazy_target_mod.VALUE == "patched"
        assert lazy_module not in _after_import.pending
    assert _lazy_target_mod.VALUE == "original"


def test_patches_a_module_imported_later_and_restores_it(lazy_module):
    with pytest.MonkeyPatch.context() as mp:
        _when_imported(mp, lazy_module, _patch(mp))
        assert lazy_module not in sys.modules
        import _lazy_target_mod

        assert _lazy_target_mod.VALUE == "patched"
        assert lazy_module not in _after_import.pending
    assert _lazy_target_mod.VALUE == "original"


def test_drops_a_callback_whose_module_never_loads(lazy_module):
    with pytest.MonkeyPatch.context() as mp:
        _when_imported(mp, lazy_module, _patch(mp))
        assert lazy_module in _after_import.pending
    assert lazy_module not in _after_import.pending


def test_uses_the_finder_pytest_installed():
    finders = [f for f in sys.meta_path if type(f).__name__ == "_AfterImport"]
    assert finders == [_after_import]

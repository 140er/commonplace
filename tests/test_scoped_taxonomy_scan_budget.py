"""The scoped taxonomy probe must not rescan the whole palace on every first page.

Every authenticated overview read (and every session start that searches memory)
opens a scoped probe whose first page used to run the full wing/room GROUP BY over
the palace database, only to look at its ``overview_too_large`` verdict and discard
the tally. A busy palace writes constantly, so the write-keyed taxonomy cache never
hit and each probe read the whole database again, holding the palace lock for the
duration and churning the page cache of the container.
"""

import pytest

PROBE = {"probe": True, "after": None, "timeout": 5.0}


@pytest.fixture
def scan(monkeypatch, config):
    import mempalace.mcp_server as m
    from mempalace import read_scope
    from mempalace.backends import chroma_sqlite

    class Scan:
        calls = 0
        error = None
        clock = 1000.0
        stamp = 0

    state = Scan()

    def acquire(*args):
        state.calls += 1
        if state.error:
            raise state.error
        return 1, {"w": {"r": 1}}

    def fingerprint():
        # A committed write moves the database stat between probes.
        state.stamp += 1
        return ((1, state.stamp, 1), None)

    monkeypatch.setattr(m, "_config", config)
    monkeypatch.setattr(m, "_is_chroma_backend", lambda: True)
    monkeypatch.setattr(m, "_palace_db_fingerprint", fingerprint)
    monkeypatch.setattr(m, "_selected_backend_name", lambda: "chroma")
    monkeypatch.setattr(chroma_sqlite, "_sqlite_wing_room_counts", acquire)
    monkeypatch.setattr(read_scope, "metadata_page", lambda *a, **k: ([], None))
    monkeypatch.setattr(m, "_advisory_clock", lambda: state.clock, raising=False)
    m._invalidate_overview_caches()
    getattr(m, "_reset_advisory_taxonomy", lambda: None)()
    return m, state


def test_probe_first_pages_share_one_scan_across_writes(scan):
    m, state = scan
    for _ in range(5):
        assert m._scoped_taxonomy(dict(PROBE)) == {
            "taxonomy": {},
            "probe": {"rows": [], "next": None},
        }
        # A write lands between probes: the exact cache is dropped, as in production.
        m._invalidate_overview_caches()
    assert state.calls == 1


def test_clean_verdict_expires_and_recounts(scan):
    m, state = scan
    m._scoped_taxonomy(dict(PROBE))
    state.clock += m._ADVISORY_TAXONOMY_TTL_S - 1
    m._scoped_taxonomy(dict(PROBE))
    assert state.calls == 1
    state.clock += 2
    m._scoped_taxonomy(dict(PROBE))
    assert state.calls == 2


def test_later_pages_never_scan(scan):
    m, state = scan
    m._scoped_taxonomy({"probe": True, "after": "id:x", "timeout": 5.0})
    assert state.calls == 0


def test_too_large_verdict_is_never_reused(scan):
    from mempalace.overview import OverviewError

    m, state = scan
    state.error = OverviewError("overview_too_large")
    for expected in (1, 2, 3):
        result = m._scoped_taxonomy(dict(PROBE))
        assert result["error"] == "overview_too_large"
        m._invalidate_overview_caches()
        assert state.calls == expected


def test_other_palace_does_not_inherit_the_verdict(scan, tmp_path):
    m, state = scan
    m._scoped_taxonomy(dict(PROBE))
    m._config._palace_path_override = str(tmp_path / "other")
    m._scoped_taxonomy(dict(PROBE))
    assert state.calls == 2

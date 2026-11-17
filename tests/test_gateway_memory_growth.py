"""Varied Deck polling must plateau after the workspace stores have opened."""

import pytest

from scripts import gateway_memory_harness as harness


def test_polling_does_not_retain_requests_or_open_resources():
    with harness.Rig(trace=True, frames=5, env={"COMMONPLACE_RATE_LIMITS": "on"}) as rig:
        routes = ["/api/events?after=0", "/api/needs-me", "/api/bots", "/api/me", "/api/cards"]
        people = rig.fixture["people"][:4]
        for person in people:
            auth = {"Cookie": "cmmn_session=" + person["cookie"]}
            for route in routes:
                assert rig.http("GET", route, headers=auth) == 200

        def request(rng):
            person = rng.choice(people)
            route = rng.choice(routes)
            route += ("&" if "?" in route else "?") + "probe=" + harness._id()
            return "GET", route, None, {"Cookie": "cmmn_session=" + person["cookie"]}

        # Imports, statement caches and all selected workspaces are warm before measuring.
        assert harness.drive(rig, 600, 2, request, seed=5) == {200: 600}
        before = rig.control("sample")
        rig.control("baseline")
        assert harness.drive(rig, 600, 2, request, seed=6) == {200: 600}
        after = rig.control("sample")
        growth = after["traced_kib"] - before["traced_kib"]
        if growth >= 1024:
            # A one-time lazy allocation (a first-use buffer) can land inside any one window
            # under load; retained growth shows up again in the next window, a one-off does not.
            first_top = rig.control("diff", n=8)["top"]
            before = rig.control("sample")
            rig.control("baseline")
            assert harness.drive(rig, 600, 2, request, seed=7) == {200: 600}
            after = rig.control("sample")
            growth = after["traced_kib"] - before["traced_kib"]
            assert growth < 1024, (first_top, rig.control("diff", n=8)["top"])
        # Background report passes can briefly own a pair of read connections and WAL fds.
        assert after["sqlite_connections"] <= before["sqlite_connections"] + 4
        assert after["threads"] <= before["threads"] + 2
        assert after["fds"] <= before["fds"] + 12
        assert after["sockets"] <= before["sockets"] + 2
        assert after["asyncio_tasks"] == before["asyncio_tasks"]


def test_shipped_gateway_keeps_allocator_arenas_bounded_under_capture():
    # Use the image's actual startup setting; do not pass a cap from the test itself.
    build = next(build for name, _weight, build in harness._realistic() if "transcript" in name)
    with harness.Rig(trace=False) as rig:
        if rig.control("arenas")["arenas"] is None:
            pytest.skip("glibc allocation statistics are unavailable")
        for batch in range(3):
            assert harness.drive(rig, 40, 8, lambda rng: build(rng, rig), seed=40 + batch) == {
                200: 40
            }
            assert 1 <= rig.control("arenas")["arenas"] <= 2
        for index in range(40):
            person = rig.fixture["people"][index % len(rig.fixture["people"])]
            assert (
                rig.http(
                    "GET",
                    f"/api/memory/search?q=busy_{index}",
                    headers={"Cookie": "cmmn_session=" + person["cookie"]},
                )
                == 503
            )

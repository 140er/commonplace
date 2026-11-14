"""HTTP regressions that also collect on the pinned pre-feature revision."""

from tests.test_coworker_api import API
from tests.test_coworker_dispatch import dispatch as dispatch
from tests.test_coworker_progress import progress as progress
from tests.test_coworker_runtime import env as env


def test_coworker_tail_and_stream_contract(env, dispatch, progress):
    item = progress.start()
    api = API(env.gateway, env.wid, env.uid, env.other, "outsider")
    for route in ("log", "log/stream"):
        status, body = api.request("GET", f"/conversations/{item.cid}/{route}")
        assert status == 200, body
        assert body["lines"] == []
        assert body["cursor"] and body["working_on"]


def test_coworker_detail_has_structured_working_on(env, dispatch, progress):
    item = progress.start()
    api = API(env.gateway, env.wid, env.uid, env.other, "outsider")
    status, body = api.request("GET", f"/conversations/{item.cid}")
    assert status == 200
    assert "working_on" in body
    assert all(
        step["state"] in {"working", "done", "waiting", "idle"} for step in body["working_on"]
    )

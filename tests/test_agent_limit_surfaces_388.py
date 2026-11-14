"""Both config submission surfaces use the shared 1..100 bound."""

import json

from tests._commonplace_helpers import hub_fixture  # noqa: F401
from tests.test_orch_surfaces import BOARD, config_text, env  # noqa: F401


def test_config_proposal_bound_over_rest_and_mcp(env):  # noqa: F811
    text = config_text().replace("max_agents = 3", "max_agents = 100")
    ok, body = env.mcp(env.untrusted_token, "orch_config_propose", board=BOARD, text=text)
    assert ok and body["status"] == "proposed"
    status, body = env.rest("POST", f"boards/{BOARD}/configs", {"text": text}, token=env.b_token)
    assert status == 201 and body["status"] == "proposed"
    text = text.replace("max_agents = 100", "max_agents = 101")
    ok, body = env.mcp(env.untrusted_token, "orch_config_propose", board=BOARD, text=text)
    assert not ok and "invalid_value" in str(body)
    status, body = env.rest("POST", f"boards/{BOARD}/configs", {"text": text}, token=env.b_token)
    assert status == 400 and "invalid_value" in json.dumps(body)

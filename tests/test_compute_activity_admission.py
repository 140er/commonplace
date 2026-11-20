"""The existing product must expose Activity without inventing zero activity."""

from activity_http import activity_hub, request  # noqa: F401


def test_activity_missing_is_not_zero(activity_hub):  # noqa: F811
    status, body, headers = request(activity_hub)
    assert status == 200, body
    assert body == {"hosts": [], "reason": "no_source"}
    assert headers["Cache-Control"] == "no-store"


def test_activity_always_requires_sign_in(activity_hub):  # noqa: F811
    assert request(activity_hub, cookie=False)[0] == 401

"""Public asset bursts have a separate ceiling from documents, identity and auth."""

import pytest

from commonplace import ratelimit
from commonplace.web import STATIC
from tests.test_commonplace_public_surface import (
    make_surface as make_surface,
    request,
    surface as surface,
    token,
)


@pytest.mark.parametrize("signed_in", [False, True])
def test_twelve_cold_module_sets_do_not_spend_shell_or_api_budget(surface, signed_in):
    headers = token(surface)[2] if signed_in else {}
    paths = ["/app/" + p.name for p in sorted(STATIC.glob("*.js"))[:46]]
    paths += ["/app/" + p.name for p in sorted(STATIC.glob("*.css"))[:9]]
    paths += ["/app/fonts/inter-latin.woff2"]
    for _ in range(12):
        for path in paths:
            assert request(surface, "GET", path, headers=headers)[0] == 200, path
    assert request(surface, "GET", "/app/auth/providers")[0] == 200
    assert request(surface, "GET", "/api/me", headers=headers)[0] == (200 if signed_in else 401)


@pytest.mark.parametrize("signed_in", [False, True])
def test_static_asset_ceiling_still_refuses_abuse(surface, signed_in):
    ceiling = ratelimit.STATIC_ASSETS
    assert ceiling == ratelimit.Limit("static-assets", 6000, 60)
    headers = token(surface)[2] if signed_in else {}
    for _ in range(ceiling.capacity):
        assert surface.gateway.limiter.check([(ceiling, "127.0.0.1")]) == 0
    status, response_headers, _ = request(surface, "GET", "/app/api.js", headers=headers)
    assert status == 429 and response_headers["Retry-After"] == "1"
    # A public asset refusal does not consume the identity/API policy.
    assert request(surface, "GET", "/api/me", headers=headers)[0] == (200 if signed_in else 401)
    surface.clock.now += 1
    assert request(surface, "GET", "/app/api.js", headers=headers)[0] == 200


def test_api_and_credential_limits_remain_independent(surface):
    for _ in range(ratelimit.ANONYMOUS.capacity):
        assert request(surface, "GET", "/api/me")[0] == 401
    assert request(surface, "GET", "/api/me")[0] == 429
    assert request(surface, "GET", "/app/api.js")[0] == 200
    headers = token(surface)[2]
    for _ in range(ratelimit.AUTHENTICATED.capacity):
        assert request(surface, "GET", "/api/me", headers=headers)[0] == 200
    assert request(surface, "GET", "/api/me", headers=headers)[0] == 429
    assert request(surface, "GET", "/app/api.js", headers=headers)[0] == 200


def test_asset_suffix_cannot_bypass_credential_limits(surface):
    for _ in range(ratelimit.CREDENTIALS.capacity):
        assert request(surface, "GET", "/app/auth/missing.js")[0] == 404
    assert request(surface, "GET", "/app/auth/missing.js")[0] == 429

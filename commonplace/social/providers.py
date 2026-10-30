"""Configured sign-in providers and bounded, direct HTTPS requests."""

from __future__ import annotations

import base64
import json
import logging
import math
import os
import secrets
import time
from dataclasses import dataclass, field
from urllib.parse import quote, quote_plus, urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .. import urls
from ..oauth.store import challenge

logger = logging.getLogger("commonplace.social")
MAX_RESPONSE = 1024 * 1024


class SignInError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, repr=False)
class Profile:
    subject: str
    name: str
    emails: tuple


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch(url, *, form=None, headers=None):
    try:
        hdrs = {"User-Agent": "Commonplace", "Accept": "application/json", **(headers or {})}
        data = None
        if form is not None:
            data = urlencode(form, quote_via=quote).encode("utf-8")
            hdrs["Content-Type"] = "application/x-www-form-urlencoded"
        opener = build_opener(ProxyHandler({}), _NoRedirect())
        with opener.open(Request(url, data=data, headers=hdrs), timeout=10) as response:
            raw = response.read(MAX_RESPONSE + 1)
            if response.status != 200 or len(raw) > MAX_RESPONSE:
                raise SignInError("failed")
        return json.loads(raw)
    except Exception:
        raise SignInError("failed") from None


def _name(value) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join("".join(c for c in value if c.isprintable() or c.isspace()).split())[:100]


def _email(value) -> tuple:
    if not isinstance(value, str):
        return ()
    value = value.strip()
    if (
        "@" not in value
        or len(value) > 254
        or any(c.isspace() or not c.isprintable() for c in value)
    ):
        return ()
    return (value,)


def _encoded(value) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _json_part(value) -> str:
    return _encoded(json.dumps(value, separators=(",", ":")).encode("utf-8"))


def _apple_key(value):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    value = value.replace("\\n", "\n").strip()
    if not value.startswith("-----BEGIN"):
        value = "-----BEGIN PRIVATE KEY-----\n" + value + "\n-----END PRIVATE KEY-----"
    key = serialization.load_pem_private_key(value.encode("utf-8"), password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
        raise ValueError("unsupported key")
    return key


@dataclass(frozen=True)
class Provider:
    id: str
    name: str
    keys: tuple
    authorize: str
    token: str
    scope: str
    issuers: tuple = ()
    credentials: dict = field(default_factory=dict, repr=False)
    private_key: object = field(default=None, repr=False)

    @property
    def pkce(self):
        return self.id != "apple"

    def redirect_uri(self):
        return urls.APP_URL + f"/app/auth/{self.id}/callback"

    def authorize_url(self, state, verifier, nonce):
        parameters = {
            "response_type": "code",
            "client_id": self.credentials["CLIENT_ID"],
            "redirect_uri": self.redirect_uri(),
            "scope": self.scope,
            "state": state,
        }
        if self.pkce:
            parameters.update(code_challenge=challenge(verifier), code_challenge_method="S256")
        if self.issuers:
            parameters["nonce"] = nonce
        if self.id == "google":
            parameters["prompt"] = "select_account"
        if self.id == "apple":
            parameters["response_mode"] = "form_post"
        return self.authorize + "?" + urlencode(parameters, quote_via=quote)

    def client_secret(self):
        if self.id != "apple":
            return self.credentials["CLIENT_SECRET"]
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

        now = int(time.time())
        head = _json_part({"alg": "ES256", "kid": self.credentials["KEY_ID"]})
        payload = _json_part(
            {
                "iss": self.credentials["TEAM_ID"],
                "iat": now,
                "exp": now + 300,
                "aud": "https://appleid.apple.com",
                "sub": self.credentials["CLIENT_ID"],
            }
        )
        signing_input = f"{head}.{payload}"
        signature = self.private_key.sign(signing_input.encode("ascii"), ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(signature)
        return signing_input + "." + _encoded(r.to_bytes(32, "big") + s.to_bytes(32, "big"))

    def _claims(self, token, nonce):
        try:
            # OIDC Core 3.1.3.7 step 6 permits TLS instead of signature checks at this client-authenticated token endpoint.
            parts = token.split(".")
            if len(parts) != 3:
                raise ValueError
            claims = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
            if not isinstance(claims, dict):
                raise ValueError
            audience = claims.get("aud")
            expiry = claims.get("exp")
            token_nonce = claims.get("nonce")
            subject = claims.get("sub")
            if (
                claims.get("iss") not in self.issuers
                or not (
                    audience == self.credentials["CLIENT_ID"]
                    or isinstance(audience, list)
                    and self.credentials["CLIENT_ID"] in audience
                )
                or type(expiry) not in (int, float)
                or not math.isfinite(expiry)
                or expiry <= time.time()
                or not isinstance(token_nonce, str)
                or not secrets.compare_digest(token_nonce.encode("utf-8"), nonce.encode("utf-8"))
                or not isinstance(subject, str)
                or not subject.strip()
            ):
                raise ValueError
            return claims
        except (AttributeError, ValueError, TypeError, UnicodeError, OverflowError, RecursionError):
            raise SignInError("failed") from None

    def profile(self, code, verifier, nonce, user="") -> Profile:
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri(),
        }
        headers = {}
        if self.pkce:
            form["code_verifier"] = verifier
        if self.id == "x":
            basic = ":".join(
                quote_plus(self.credentials[key]) for key in ("CLIENT_ID", "CLIENT_SECRET")
            )
            headers["Authorization"] = "Basic " + base64.b64encode(basic.encode("ascii")).decode(
                "ascii"
            )
        else:
            form.update(client_id=self.credentials["CLIENT_ID"], client_secret=self.client_secret())
        token = fetch(self.token, form=form, headers=headers)
        if (
            not isinstance(token, dict)
            or "error" in token
            or not isinstance(token.get("access_token"), str)
            or not token["access_token"]
        ):
            raise SignInError("failed")
        if self.issuers:
            return self._oidc_profile(token.get("id_token"), nonce, user)
        headers = {"Authorization": "Bearer " + token["access_token"]}
        if self.id == "github":
            headers.update(
                {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
            )
            data = fetch("https://api.github.com/user", headers=headers)
            if not isinstance(data, dict) or type(data.get("id")) is not int:
                raise SignInError("failed")
            addresses = fetch("https://api.github.com/user/emails", headers=headers)
            if not isinstance(addresses, list):
                raise SignInError("failed")
            verified = [a for a in addresses if isinstance(a, dict) and a.get("verified") is True]
            verified.sort(key=lambda a: a.get("primary") is not True)
            emails = tuple(email for a in verified for email in _email(a.get("email")))
            return Profile(str(data["id"]), _name(data.get("name") or data.get("login")), emails)
        if self.id == "x":
            data = fetch(
                "https://api.x.com/2/users/me?user.fields=confirmed_email", headers=headers
            )
            data = data.get("data") if isinstance(data, dict) else None
            emails = _email(data.get("confirmed_email")) if isinstance(data, dict) else ()
            name = data.get("name") if isinstance(data, dict) else None
        else:
            data = fetch("https://discord.com/api/v10/users/@me", headers=headers)
            emails = (
                _email(data.get("email"))
                if isinstance(data, dict) and data.get("verified") is True
                else ()
            )
            name = (
                data.get("global_name") or data.get("username") if isinstance(data, dict) else None
            )
        if (
            not isinstance(data, dict)
            or not isinstance(data.get("id"), str)
            or not data["id"].strip()
        ):
            raise SignInError("failed")
        return Profile(data["id"], _name(name), emails)

    def _oidc_profile(self, token, nonce, user):
        claims = self._claims(token, nonce)
        verified = claims.get("email_verified")
        emails = (
            _email(claims.get("email"))
            if verified is True or self.id == "apple" and verified == "true"
            else ()
        )
        name = claims.get("name")
        if self.id == "apple":
            name = ""
            try:
                data = json.loads(user)
                parts = data.get("name") if isinstance(data, dict) else None
                if isinstance(parts, dict):
                    name = " ".join(_name(parts.get(key)) for key in ("firstName", "lastName"))
            except (ValueError, TypeError, RecursionError):
                pass
        return Profile(claims["sub"], _name(name), emails)


_SPECS = (
    (
        "google",
        "Google",
        ("CLIENT_ID", "CLIENT_SECRET"),
        "https://accounts.google.com/o/oauth2/v2/auth",
        "https://oauth2.googleapis.com/token",
        "openid email profile",
        ("https://accounts.google.com", "accounts.google.com"),
    ),
    (
        "github",
        "GitHub",
        ("CLIENT_ID", "CLIENT_SECRET"),
        "https://github.com/login/oauth/authorize",
        "https://github.com/login/oauth/access_token",
        "read:user user:email",
        (),
    ),
    (
        "apple",
        "Apple",
        ("CLIENT_ID", "TEAM_ID", "KEY_ID", "PRIVATE_KEY"),
        "https://appleid.apple.com/auth/authorize",
        "https://appleid.apple.com/auth/token",
        "name email",
        ("https://appleid.apple.com",),
    ),
    (
        "x",
        "X",
        ("CLIENT_ID", "CLIENT_SECRET"),
        "https://x.com/i/oauth2/authorize",
        "https://api.x.com/2/oauth2/token",
        "tweet.read users.read users.email",
        (),
    ),
    (
        "discord",
        "Discord",
        ("CLIENT_ID", "CLIENT_SECRET"),
        "https://discord.com/oauth2/authorize",
        "https://discord.com/api/oauth2/token",
        "identify email",
        (),
    ),
)


def configured() -> list[Provider]:
    result = []
    for spec in _SPECS:
        provider_id, _name, keys, *_rest = spec
        credentials = {
            key: os.environ.get(f"COMMONPLACE_{provider_id.upper()}_{key}", "").strip()
            for key in keys
        }
        if not any(credentials.values()):
            continue
        missing = [
            f"COMMONPLACE_{provider_id.upper()}_{key}" for key in keys if not credentials[key]
        ]
        if missing:
            logger.warning("sign-in %s missing %s", provider_id, ", ".join(missing))
            continue
        key = None
        if provider_id == "apple":
            try:
                key = _apple_key(credentials["PRIVATE_KEY"])
            except Exception:
                logger.warning("sign-in apple: ES256 key or cryptography unavailable")
                continue
        result.append(Provider(*spec, credentials=credentials, private_key=key))
    return result

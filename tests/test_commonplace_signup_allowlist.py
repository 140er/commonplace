"""COMMONPLACE_SIGNUP_ALLOW_EMAILS lets named verified emails sign up while sign-up is closed."""

import logging

import pytest

from commonplace.social import accounts
from tests.test_commonplace_email_auth import (
    counts,
    delivered,
    fresh as fresh,
    refused as email_refused,
)
from tests.test_commonplace_public_surface import make_surface as make_surface
from tests.test_commonplace_social import (
    apple_key as apple_key,
    finish,
    make_signin as make_signin,
    refused,
    remote as remote,
    rows,
    signed_in,
    start,
)

ALLOW = "COMMONPLACE_SIGNUP_ALLOW_EMAILS"
LANDING = "/app/#/board/commonplace"


def github_signin(surface, remote, emails):
    remote.github.update(id=777, name="Allowed Person")
    remote.emails = [
        {"email": email, "verified": verified, "primary": n == 0}
        for n, (email, verified) in enumerate(emails)
    ]
    return finish(surface, start(surface, remote, "github"))


def created_emails(surface):
    return sorted(row["email"] for row in rows(surface, "users"))


def attempt(surface, remote, email, *, verified=True):
    """Sign in through GitHub with one email; return True when an account was created."""
    before = len(rows(surface, "users"))
    response = github_signin(surface, remote, [(email, verified)])
    created = len(rows(surface, "users")) == before + 1
    if created:
        assert response[0] == 303 and response[1]["Location"] == LANDING
    else:
        assert response[0] == 303 and "Set-Cookie" not in response[1]
        assert "signin=" in response[1]["Location"]
    return created


@pytest.fixture
def closed(make_signin, monkeypatch):
    def make(allow, ids=("github",)):
        monkeypatch.delenv("COMMONPLACE_SIGNUP", raising=False)
        if allow is None:
            monkeypatch.delenv(ALLOW, raising=False)
        else:
            monkeypatch.setenv(ALLOW, allow)
        return make_signin(ids, signup=False)

    return make


def test_closed_allowlisted_verified_email_creates_the_account(closed, remote):
    surface = closed("tester@example.test")
    response = github_signin(surface, remote, [("tester@example.test", True)])
    me = signed_in(surface, response, surface.db.user("tester@example.test")["id"], LANDING)
    assert me["email"] == "tester@example.test"
    assert len(rows(surface, "users")) == 2  # the fixture owner plus the new account
    assert [r["subject"] for r in rows(surface, "user_identities")][-1] == "777"


def test_closed_allowlisted_email_through_google_oidc(closed, remote):
    surface = closed("tester@example.test", ("google",))
    remote.claims["email"] = "tester@example.test"
    response = finish(surface, start(surface, remote))
    signed_in(surface, response, surface.db.user("tester@example.test")["id"], LANDING)


def test_closed_allowlisted_but_unverified_email_is_refused(closed, remote):
    surface = closed("tester@example.test")
    refused(github_signin(surface, remote, [("tester@example.test", False)]), "email")
    assert created_emails(surface) == ["owner@example.com"]
    assert rows(surface, "user_identities") == [] or all(
        r["subject"] != "777" for r in rows(surface, "user_identities")
    )


def test_closed_allowlisted_but_unverified_google_email_is_refused(closed, remote):
    surface = closed("tester@example.test", ("google",))
    remote.claims.update(email="tester@example.test", email_verified=False)
    refused(finish(surface, start(surface, remote)), "email")
    assert created_emails(surface) == ["owner@example.com"]


def test_closed_unverified_allowlisted_email_does_not_ride_a_verified_other_email(closed, remote):
    surface = closed("tester@example.test")
    response = github_signin(
        surface, remote, [("other@example.test", True), ("tester@example.test", False)]
    )
    refused(response, "account")
    assert created_emails(surface) == ["owner@example.com"]


def test_closed_other_email_keeps_the_existing_refusal(closed, remote):
    surface = closed("tester@example.test")
    refused(github_signin(surface, remote, [("other@example.test", True)]), "account")
    assert created_emails(surface) == ["owner@example.com"]
    assert len(rows(surface, "workspaces")) == 1


def test_match_is_case_insensitive_and_trimmed(closed, remote):
    surface = closed("  Tester@Example.TEST ,\nother@example.test")
    assert attempt(surface, remote, "TESTER@example.test")


@pytest.mark.parametrize("separator", [",", " ", "\n", ", ", " \t,\n"])
def test_entries_split_on_commas_and_whitespace(closed, remote, separator):
    surface = closed("first@example.test" + separator + "second@example.test")
    assert attempt(surface, remote, "second@example.test")


@pytest.mark.parametrize("allow", [None, "", "   ", ",,\n"])
def test_empty_or_unset_list_changes_nothing(closed, remote, allow):
    surface = closed(allow)
    assert accounts.allowed_signup_emails() == frozenset()
    assert not attempt(surface, remote, "tester@example.test")


@pytest.mark.parametrize(
    "email",
    [
        "tester+extra@example.test",
        "tester+@example.test",
        "tester@sub.example.test",
        "tester@example.test.evil.test",
        "xtester@example.test",
        "tester@example.tes",
        "tester@@example.test",
    ],
)
def test_only_the_exact_address_is_allowed(closed, remote, email):
    surface = closed("tester@example.test")
    assert not attempt(surface, remote, email)
    assert created_emails(surface) == ["owner@example.com"]


@pytest.mark.parametrize(
    "entry", ["*@example.test", "@example.test", "example.test", "tester@", "tester", "*"]
)
def test_wildcards_domains_and_partial_entries_match_nothing(closed, remote, entry):
    surface = closed(entry)
    assert accounts.allowed_signup_emails() == frozenset()
    assert not attempt(surface, remote, "tester@example.test")


def test_plus_entry_matches_only_itself(closed, remote):
    surface = closed("tester+one@example.test")
    assert not attempt(surface, remote, "tester@example.test")
    assert not attempt(surface, remote, "tester+two@example.test")
    assert attempt(surface, remote, "tester+one@example.test")


def test_second_verified_email_on_the_list_creates_the_account_under_that_email(closed, remote):
    surface = closed("tester@example.test")
    response = github_signin(
        surface, remote, [("other@example.test", True), ("tester@example.test", True)]
    )
    signed_in(surface, response, surface.db.user("tester@example.test")["id"], LANDING)
    assert created_emails(surface) == ["owner@example.com", "tester@example.test"]


def test_existing_user_signs_in_as_before_even_when_not_listed(closed, remote):
    surface = closed("tester@example.test")
    remote.emails = [{"email": "OWNER@example.com", "verified": True, "primary": True}]
    signed_in(surface, finish(surface, start(surface, remote, "github")))


def test_open_mode_is_unchanged_by_the_list(make_signin, monkeypatch, remote):
    monkeypatch.setenv(ALLOW, "tester@example.test")
    surface = make_signin(("github",), signup=True)
    assert attempt(surface, remote, "anyone@example.test")


def test_list_is_capped_at_twenty_valid_entries_and_bad_ones_are_ignored(
    closed, remote, monkeypatch, caplog
):
    entries = ["no-at-sign", "@x", "two@@ats"] + [f"user{n}@example.test" for n in range(25)]
    surface = closed(" ".join(entries))
    with caplog.at_level(logging.DEBUG):
        allowed = accounts.allowed_signup_emails()
    assert allowed == {f"user{n}@example.test" for n in range(20)}
    assert not attempt(surface, remote, "user20@example.test")
    assert not attempt(surface, remote, "user24@example.test")
    assert attempt(surface, remote, "user19@example.test")
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    # three malformed entries and five surplus ones, counted but never named
    assert warnings and set(warnings) == {"Ignored 8 invalid or surplus sign-up allowlist entries"}


def test_list_and_matched_email_never_reach_logs_or_responses(closed, remote, caplog):
    secret = "tester@example.test"
    surface = closed(f"{secret} second-secret@example.test not-valid")
    with caplog.at_level(logging.DEBUG):
        response = github_signin(surface, remote, [(secret, True)])
        refused_response = github_signin(surface, remote, [("nope@example.test", True)])
    text = caplog.text + repr(response[1]) + repr(refused_response[1])
    for fragment in (secret, "second-secret", "not-valid", "tester"):
        assert fragment not in text
    assert [r.getMessage() for r in caplog.records if "allowlisted" in r.getMessage()] == [
        "allowlisted signup"
    ]
    audit = repr(surface.db.audit_entries())
    assert "second-secret" not in audit and "not-valid" not in audit


def test_email_link_allowlisted_address_can_sign_up_while_closed(fresh, monkeypatch):
    client, mailbox = fresh()
    monkeypatch.setenv("COMMONPLACE_SIGNUP", "closed")
    monkeypatch.setenv(ALLOW, "Tester@example.test")
    assert client.ask("tester@example.test")[0] == 202
    status, headers, _ = client.verify(mailbox.link())
    assert status == 303 and headers["Set-Cookie"]
    assert counts(client) == (1, 1)
    assert client.db.user("tester@example.test")["id"]


def test_email_link_other_and_plus_addresses_stay_closed(fresh, monkeypatch):
    client, mailbox = fresh()
    monkeypatch.setenv("COMMONPLACE_SIGNUP", "closed")
    monkeypatch.setenv(ALLOW, "tester@example.test")
    for email in ("other@example.test", "tester+x@example.test"):
        assert client.ask(email)[0] == 202
    delivered(client.gateway.email_auth)
    assert mailbox.messages == [] and counts(client) == (0, 0)


def test_email_link_verify_rechecks_the_list(fresh, monkeypatch):
    client, mailbox = fresh()
    monkeypatch.setenv("COMMONPLACE_SIGNUP", "closed")
    monkeypatch.setenv(ALLOW, "tester@example.test")
    assert client.ask("tester@example.test")[0] == 202
    link = mailbox.link()
    monkeypatch.setenv(ALLOW, "someone-else@example.test")
    email_refused(client.verify(link))
    assert counts(client) == (0, 0)


def test_email_link_empty_list_changes_nothing(fresh, monkeypatch):
    client, mailbox = fresh()
    monkeypatch.setenv("COMMONPLACE_SIGNUP", "closed")
    monkeypatch.delenv(ALLOW, raising=False)
    assert client.ask("tester@example.test")[0] == 202
    delivered(client.gateway.email_auth)
    assert mailbox.messages == [] and counts(client) == (0, 0)


def sloppy_match(emails):
    """The mutation: prefix match after stripping plus tags."""
    allowed = accounts.allowed_signup_emails()
    for email in emails:
        local, _, domain = email.strip().casefold().partition("@")
        if any((local.split("+")[0] + "@" + domain).startswith(a.split("@")[0]) for a in allowed):
            return email
    return None


def test_mutating_exact_match_to_prefix_or_plus_stripping_is_caught(closed, remote, monkeypatch):
    surface = closed("tester@example.test")
    monkeypatch.setattr(accounts, "allowlisted_email", sloppy_match)
    # The exactness checks above must fail under the mutation: a plus-address is let in.
    with pytest.raises(AssertionError):
        assert not attempt(surface, remote, "tester+extra@example.test")

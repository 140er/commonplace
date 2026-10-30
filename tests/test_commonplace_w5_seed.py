"""Seed validation and idempotent use of the shared boards service."""

import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from commonplace.extensions import ToolError
from commonplace.seed import load_seed, seed_file


@pytest.fixture
def owner_context():
    return SimpleNamespace(principal=SimpleNamespace(person=True, role="owner"))


@pytest.fixture
def fake_service(monkeypatch):
    class FakeBoardService:
        cards = []
        contexts = []
        calls = []
        fail_title = None

        def __init__(self, ctx):
            self.contexts.append(ctx)

        def board_list(self):
            return [{"slug": board} for board in sorted({c["board"] for c in self.cards})]

        def task_list(self, *, board, all_statuses):
            assert all_statuses is True
            return [c for c in self.cards if c["board"] == board]

        def seed_card(self, **card):
            self.calls.append(card)
            if card["title"] == self.fail_title:
                raise ToolError("no: temporary service failure")
            for existing in self.cards:
                if (existing["board"], existing["title"]) == (card["board"], card["title"]):
                    return existing
            created = {"number": len(self.cards) + 1, **card}
            self.cards.append(created)
            return created

    package = ModuleType("commonplace.boards")
    package.__path__ = []
    module = ModuleType("commonplace.boards.service")
    module.BoardService = FakeBoardService
    monkeypatch.setitem(sys.modules, "commonplace.boards", package)
    monkeypatch.setitem(sys.modules, "commonplace.boards.service", module)
    return FakeBoardService


def write_seed(tmp_path, cards):
    path = tmp_path / "cards.json"
    path.write_text(json.dumps(cards), encoding="utf-8")
    return path


def test_load_preserves_verbatim_text_and_defaults(tmp_path):
    title = " A title with spacing "
    body = "A sentence.\n\n    Original indentation.\n"
    path = write_seed(tmp_path, [{"board": "commonplace", "title": title, "body": body}])
    assert load_seed(path) == [
        {"board": "commonplace", "title": title, "body": body, "status": "todo", "labels": []}
    ]


@pytest.mark.parametrize(
    "bad",
    [
        None,
        [],
        {},
        {"board": None, "title": "Title"},
        {"board": "commonplace", "title": " "},
        {"board": "../other", "title": "Title"},
        {"board": "commonplace", "title": "Title", "body": []},
        {"board": "commonplace", "title": "Title", "status": []},
        {"board": "commonplace", "title": "Title", "status": "closed"},
        {"board": "commonplace", "title": "Title", "labels": "mvp"},
        {"board": "commonplace", "title": "Title", "labels": [1]},
        {"board": "commonplace", "title": "Title", "labels": [""]},
        {"board": "commonplace", "title": "Title", "repo": False},
        {"board": "commonplace", "title": "Title", "branch": None},
        {"board": "commonplace", "title": "Title", "pr_url": "javascript:alert(1)"},
        {"board": "commonplace", "title": "Title", "pr_url": "https://[broken"},
        {"board": "commonplace", "title": "Title", "assignee": "unverified"},
    ],
)
def test_rejects_bad_card(tmp_path, bad):
    path = write_seed(tmp_path, [{"board": "commonplace", "title": "Valid first"}, bad])
    with pytest.raises(ValueError, match="seed card 2"):
        load_seed(path)


@pytest.mark.parametrize("data", [{}, None, "cards"])
def test_rejects_non_array(tmp_path, data):
    with pytest.raises(ValueError, match="JSON array"):
        load_seed(write_seed(tmp_path, data))


def test_imports_all_statuses_and_metadata_then_skips_repeated_file(
    tmp_path, owner_context, fake_service
):
    cards = [
        {
            "board": "commonplace",
            "title": f"Original {status}",
            "body": "Exact words.\n  Still exact.\n",
            "status": status,
            "labels": ["mvp"],
            "repo": "github.com/140er/mempalace",
            "branch": "feat/example",
            "pr_url": "https://github.com/140er/mempalace/pull/12",
        }
        for status in ("backlog", "todo", "doing", "review", "done")
    ]
    path = write_seed(tmp_path, cards)
    assert seed_file(owner_context, path) == {"created": 5, "skipped": 0}
    assert fake_service.calls == cards
    assert fake_service.contexts == [owner_context]
    assert seed_file(owner_context, path) == {"created": 0, "skipped": 5}
    assert len(fake_service.cards) == len(fake_service.calls) == 5


def test_identity_is_exact_board_and_title_and_existing_cards_are_untouched(
    tmp_path, owner_context, fake_service
):
    original = {
        "board": "commonplace",
        "title": "Same",
        "body": "Later user edit",
        "status": "done",
    }
    fake_service.cards.append(original)
    path = write_seed(
        tmp_path,
        [
            {"board": "commonplace", "title": "Same", "body": "Old seed content"},
            {"board": "another", "title": "Same"},
            {"board": "commonplace", "title": "same"},
            {"board": "commonplace", "title": " Same "},
            {"board": "another", "title": "Same", "body": "Duplicate in same file"},
        ],
    )
    assert seed_file(owner_context, path) == {"created": 3, "skipped": 2}
    assert fake_service.cards[0] is original
    assert original["body"] == "Later user edit"
    assert original["status"] == "done"


def test_validates_every_card_before_constructing_service(tmp_path, owner_context, fake_service):
    path = write_seed(
        tmp_path,
        [{"board": "commonplace", "title": "Good"}, {"board": "commonplace", "title": 3}],
    )
    with pytest.raises(ValueError, match="seed card 2"):
        seed_file(owner_context, path)
    assert fake_service.contexts == []
    assert fake_service.calls == []


@pytest.mark.parametrize("person, role", [(False, "owner"), (True, "member")])
def test_import_requires_owner_person(tmp_path, fake_service, person, role):
    ctx = SimpleNamespace(principal=SimpleNamespace(person=person, role=role))
    with pytest.raises(ToolError, match="workspace owner person"):
        seed_file(ctx, write_seed(tmp_path, [{"board": "commonplace", "title": "Title"}]))
    assert fake_service.contexts == []


def test_retry_after_service_failure_resumes_without_duplicates(
    tmp_path, owner_context, fake_service
):
    path = write_seed(
        tmp_path,
        [{"board": "commonplace", "title": "First"}, {"board": "commonplace", "title": "Second"}],
    )
    fake_service.fail_title = "Second"
    with pytest.raises(ToolError, match="temporary service failure"):
        seed_file(owner_context, path)
    assert len(fake_service.cards) == 1
    fake_service.fail_title = None
    assert seed_file(owner_context, path) == {"created": 1, "skipped": 1}
    assert len(fake_service.cards) == 2


def test_empty_file_does_not_construct_service(tmp_path, owner_context, fake_service):
    assert seed_file(owner_context, write_seed(tmp_path, [])) == {"created": 0, "skipped": 0}
    assert fake_service.contexts == []


def test_repository_seed_has_real_statuses_and_pr_links():
    cards = load_seed(Path(__file__).resolve().parents[1] / "seed" / "commonplace-mvp.json")
    assert len(cards) >= 20
    assert len({(card["board"], card["title"]) for card in cards}) == len(cards)
    assert {card["status"] for card in cards} == {"backlog", "todo", "doing", "review", "done"}
    assert all(card["board"] == "commonplace" for card in cards)
    assert all(card["repo"] == "github.com/140er/mempalace" for card in cards)
    assert all("\n\nSource: " in card["body"] for card in cards)
    review = {card["pr_url"] for card in cards if card["status"] == "review"}
    assert review == {f"https://github.com/140er/mempalace/pull/{n}" for n in (8, 10, 12)}

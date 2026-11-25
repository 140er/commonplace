"""Card and roster first reads avoid work proportional to duplicate rows."""

from contextlib import contextmanager

from commonplace.policy import Policy
from tests.test_cold_start_routes import cold_hub as cold_hub
from tests.test_commonplace_boards import api


def seed_cards(service, count=100):
    service.task_add(board="project", title="A fixture card")
    with service.store.connection() as conn:
        columns = ",".join(r[1] for r in conn.execute("PRAGMA table_info(tasks)") if r[1] != "id")
        for _ in range(count - 1):
            conn.execute(f"INSERT INTO tasks({columns}) SELECT {columns} FROM tasks LIMIT 1")
        conn.commit()


def test_card_listing_does_not_repeat_join_per_card(cold_hub, monkeypatch):
    _hub, _cookie, service = cold_hub
    seed_cards(service)
    statements = []
    connection = service.store.connection

    @contextmanager
    def traced():
        with connection() as conn:
            conn.set_trace_callback(statements.append)
            yield conn

    monkeypatch.setattr(service.store, "connection", traced)
    cards = service.task_list(board="project", all_statuses=True)
    assert len(cards) == 100
    individual = [sql for sql in statements if "JOIN boards" in sql and "WHERE t.id=" in sql]
    assert len(individual) <= 1, f"Repeated {len(individual)} individual card joins"


def test_queue_authorizes_each_wing_once_per_request(cold_hub, monkeypatch):
    _hub, _cookie, service = cold_hub
    seed_cards(service)
    checks = []
    original = Policy.can_read_wing

    def observed(policy, wing):
        checks.append(wing)
        return original(policy, wing)

    monkeypatch.setattr(Policy, "can_read_wing", observed)
    service.needs_me(cards_only=True)
    assert checks.count("shared") <= 2
    checks.clear()
    service.needs_me(cards_only=True)
    assert checks, "Authority must be evaluated again on every request"


def test_bot_badge_does_not_build_unrelated_setup_attention(cold_hub, monkeypatch):
    from commonplace.boards.service import BoardService

    hub, cookie, _service = cold_hub

    def unrelated(_service):
        raise AssertionError("Bot decision badges must not build the setup-attention queue")

    monkeypatch.setattr(BoardService, "_missing_model_roles", unrelated)
    status, roster = api(hub, cookie, "GET", "/api/bots")
    assert status == 200
    assert {b["kind"] for b in roster["bots"]} == {"general", "project"}


def test_card_urls_share_one_transaction_and_keep_stable_links(cold_hub, monkeypatch):
    from commonplace.boards.display import with_urls

    hub, _cookie, service = cold_hub
    count = 0
    transaction = hub.db._tx

    @contextmanager
    def observed():
        nonlocal count
        count += 1
        with transaction() as conn:
            yield conn

    monkeypatch.setattr(hub.db, "_tx", observed)
    cards = [{"number": n, "status": "todo", "board": "project"} for n in range(100)]
    first = with_urls(service.ctx, cards)
    assert count == 1
    assert len({card["url"] for card in first}) == 100
    assert with_urls(service.ctx, cards) == first
    assert with_urls(service.ctx, {"cards": cards, "linked": cards})["cards"] == first

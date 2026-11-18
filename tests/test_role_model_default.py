"""#419: a role with no model runs on the documented default; with none usable the owner is told once."""

import pytest

from commonplace.boards.service import BoardService
from commonplace.orch import tools as orch_tools
from commonplace.projects import models
from tests._commonplace_helpers import hub_fixture  # noqa: F401
from tests._projects_helpers import ProjectsHub, projects_hub_fixture  # noqa: F401
from tests.test_orch_runner import BOARD, MODEL, PROVIDER, World, refused


@pytest.fixture
def world(hub):
    return World(hub)


def forget_every_model(world):
    """No choice for any role and an empty model list: nothing is usable."""
    with world.db._lock, world.db._tx() as conn:
        conn.execute("DELETE FROM chat_model_settings WHERE workspace_id=?", (world.ws,))
        conn.execute("DELETE FROM project_model_catalog WHERE workspace_id=?", (world.ws,))


def forget_choices_keep_list(world):
    with world.db._lock, world.db._tx() as conn:
        conn.execute("DELETE FROM chat_model_settings WHERE workspace_id=?", (world.ws,))


def model_items(world):
    return BoardService(world.ctx(world.owner)).needs_me().get("model_roles", [])


def test_project_with_no_model_choices_starts_on_the_default_and_records_it(world):
    world.configure()
    forget_choices_keep_list(world)
    assert models.resolve(world.db, world.ws, BOARD, "worker") is None  # nothing chosen
    card = world.card()
    status, job = world.person_start(card)
    assert status == 201
    [row] = world.jobs_for(card)
    assert (row["model_provider"], row["model"], row["model_source"]) == (
        PROVIDER,
        MODEL,
        "default",
    )
    assert model_items(world) == []  # nothing for the owner to do


def test_a_chosen_model_is_recorded_as_chosen_not_default(world):
    world.configure()
    card = world.card()
    world.person_start(card)
    assert world.jobs_for(card)[0]["model_source"] == "project"


def test_orchestrator_step_start_reply_names_the_default(world):
    world.configure()
    forget_choices_keep_list(world)
    lease = world.claim(world.a)
    reply = world.orch_start(world.a, lease, world.card(), "s1")
    assert reply["model"] == {"provider": PROVIDER, "model": MODEL}
    assert reply["model_source"] == "default"


def test_with_no_usable_model_the_owner_sees_exactly_one_item_until_it_is_fixed(world):
    world.configure()
    forget_every_model(world)
    card = world.card()
    refusal = refused("model_unresolved", world.person_start, card)
    assert "model list is empty" in str(refusal)
    refused("model_unresolved", world.person_start, world.card())  # repeated misses
    [item] = model_items(world)  # one item for the board, not one per role or per miss
    assert (item["board"], item["kind"]) == (BOARD, "model_role")
    assert item["action"]["type"] == "project_models" and item["action"]["board"] == BOARD
    assert BOARD in item["message"] and "Agent models" in item["message"]
    assert item["roles"] and set(item["roles"]) <= {"main", "worker", "reviewer", "gate"}
    # Adding any model to the list clears it and the next start runs on it as the default.
    models.catalog_add(world.db, world.owner, BOARD, {"provider": PROVIDER, "model": MODEL})
    assert model_items(world) == []
    status, _ = world.person_start(card)
    assert status == 201
    assert world.jobs_for(card)[0]["model_source"] == "default"


def test_every_missing_role_is_one_item_for_the_board(world):
    world.configure(model_role="reviewer")  # the builder role runs the reviewer slot
    forget_every_model(world)
    [item] = model_items(world)
    assert item["roles"] == ["main", "reviewer"] and item["role"] == "main"
    assert sorted(item["config_roles"]) == ["builder", "main"]


def test_effective_model_is_labelled_per_role(world):
    forget_choices_keep_list(world)
    models.put(
        world.db,
        world.owner,
        BOARD,
        {"worker": {"provider": PROVIDER, "model": MODEL}},
    )
    got = {r: e.public() for r, e in models.effective_roles(world.db, world.ws, BOARD).items()}
    assert got["worker"] == {"provider": PROVIDER, "model": MODEL, "source": "project"}
    assert got["main"] == {"provider": PROVIDER, "model": MODEL, "source": "default"}
    forget_every_model(world)
    none = models.effective(world.db, world.ws, BOARD, "gate")
    assert none.public() == {"provider": None, "model": None, "source": "none"}


def test_the_config_render_shows_the_effective_model_per_role(world):
    world.configure()
    forget_choices_keep_list(world)
    models.put(world.db, world.owner, BOARD, {"worker": {"provider": PROVIDER, "model": MODEL}})
    text = orch_tools.config_render(world.ctx(world.owner), BOARD)["markdown"]
    assert f"effective model: `{PROVIDER}/{MODEL}` (chosen for this project)" in text
    assert f"effective model: `{PROVIDER}/{MODEL}` (default: the first model" in text
    forget_every_model(world)
    text = orch_tools.config_render(world.ctx(world.owner), BOARD)["markdown"]
    assert "effective model: none (no model is set" in text


def test_the_project_view_carries_the_effective_models(projects_hub):
    h = projects_hub
    h.create_board(h.owner, "alpha")
    cheap = {"provider": PROVIDER, "model": "example-cheap"}
    assert h.models(h.owner, "alpha", {"worker": cheap}, declare=True)[0] == 200
    shown = h.project(h.owner, "alpha")[1]["models_effective"]
    assert shown["worker"] == {**cheap, "source": "project"}
    assert shown["main"] == {**cheap, "source": "default"}
    assert h.project(h.member, "alpha")[1]["models_effective"]["gate"]["source"] == "default"


def test_an_empty_project_view_says_none_not_blank(projects_hub):
    h = projects_hub
    h.create_board(h.owner, "alpha")
    shown = h.project(h.owner, "alpha")[1]["models_effective"]
    assert set(shown) == {"main", "worker", "reviewer", "gate"}
    assert all(
        item == {"provider": None, "model": None, "source": "none"} for item in shown.values()
    )

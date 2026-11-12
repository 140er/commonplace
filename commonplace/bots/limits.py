"""Bot turn limits and the model budget bots share with project chat.

One unit is one model request sent (taken just before sending, given back if the request
never went out). Bots count theirs in the control table
``bot_model_calls`` (numbers only); ``chat.store.counts_today`` adds it to project chat's
count, so both draw on one ``daily_limit`` / ``user_daily_limit``. The per-person
per-minute rate is project chat's ``USER_RATE`` bucket.
"""

import threading

from commonplace.chat import store as chat_store
from commonplace.chat.types import USER_RATE

BOT_MAX_STEPS = 6  # tool calls per turn
BOT_CALL_TIMEOUT_S = 30  # one model request
BOT_TURN_TIMEOUT_S = 60  # the whole turn
BOT_MAX_OUTPUT_TOKENS = 800  # per model request
BOT_MAX_INPUT_CHARS = 48_000  # system + messages + tool specs, per model request
BOT_MAX_RESPONSE_BYTES = 512 * 1024  # streamed bytes per model request
BOT_MAX_REPLY_CHARS = 16_000  # stored reply text

TURNS_PER_BOT = 4  # a shared bot, over all its threads
TURNS_PER_PERSON = 4
TURNS_PER_PROCESS = 32


def take(gateway, config, workspace_id, user_id) -> tuple[str, str | None]:
    """Take one unit for a model request about to be sent.

    ``("", day)`` on admission, else ``("limit" | "rate", None)``. The daily check and count
    are one atomic step, so concurrent turns cannot overshoot; the shared per-minute bucket is
    charged too. A request that is then never sent is given back with :func:`give_back` and
    the ``day`` it was counted on, so a unit stays one request sent.
    """
    day = chat_store.take_bot_call(
        gateway.db, workspace_id, user_id, config.daily_limit, config.user_daily_limit
    )
    if day is None:
        return "limit", None
    if gateway.limiter.check([(USER_RATE, f"{workspace_id}:{user_id}")]):
        chat_store.give_back_bot_call(gateway.db, workspace_id, user_id, day)
        return "rate", None
    return "", day


def give_back(gateway, workspace_id, user_id, day) -> None:
    chat_store.give_back_bot_call(gateway.db, workspace_id, user_id, day)


class Busy(Exception):
    """A cap is full. ``status`` is 409 (this bot) or 429 (person or process)."""

    def __init__(self, status):
        super().__init__("busy")
        self.status = status


class Registry:
    """Process-local running turns: one per thread, a few per bot and per person, a bounded total."""

    def __init__(self):
        self._lock = threading.Lock()
        self._turns = {}  # (workspace_id, bot_id, thread_id) -> turn; thread_id None: owner's

    @staticmethod
    def _key(turn):
        return (turn.workspace_id, turn.bot_id, turn.thread_id)

    def admit(self, turn) -> None:
        with self._lock:
            if self._key(turn) in self._turns:
                raise Busy(409)
            held = list(self._turns.values())
            if sum(1 for t in held if (t.workspace_id, t.bot_id) == self._key(turn)[:2]) >= (
                TURNS_PER_BOT
            ):
                raise Busy(409)
            person = sum(
                1 for t in held if (t.workspace_id, t.user_id) == (turn.workspace_id, turn.user_id)
            )
            if person >= TURNS_PER_PERSON or len(held) >= TURNS_PER_PROCESS:
                raise Busy(429)
            self._turns[self._key(turn)] = turn

    def release(self, turn) -> None:
        with self._lock:
            if self._turns.get(self._key(turn)) is turn:
                del self._turns[self._key(turn)]

    def running(self, workspace_id, bot_id, thread_id=None):
        with self._lock:
            return self._turns.get((workspace_id, bot_id, thread_id))

    def running_bots(self, workspace_id, user_id) -> set[str]:
        with self._lock:
            return {
                t.bot_id
                for t in self._turns.values()
                if (t.workspace_id, t.user_id) == (workspace_id, user_id)
            }

    def holding(self, workspace_id, bot_id, request_id, thread_id=None) -> bool:
        """True while a turn for exactly this request is admitted in this process."""
        with self._lock:
            turn = self._turns.get((workspace_id, bot_id, thread_id))
            return turn is not None and turn.request_id == request_id


REGISTRY = Registry()

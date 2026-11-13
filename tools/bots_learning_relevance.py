"""Informational synthetic retrieval comparison; no network, score threshold or live data.

    uv run python tools/bots_learning_relevance.py --seed 464 --conversations 36

The first line is the learned-item hit-rate gain in percentage points. The table compares
whether the expected item reached the prompt and mean input characters added to the baseline.
This measures retrieval, not answer quality or extraction quality. Run on a quiet machine;
never use the number to gate a merge. All stores are temporary and removed on exit.
"""

import argparse
import random
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from commonplace.bots import context
from commonplace.bots.learned import WORKSPACE, Learned
from commonplace.bots.store import BotStore

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
# Balanced cases include a paraphrase without lexical overlap: local FTS may miss it.
CASES = (
    ("correction", "staging name", "Our staging is called the sandbox.", "Where is staging?"),
    (
        "preference",
        "answer style",
        "Please keep answers short and in plain language.",
        "Explain composting.",
    ),
    (
        "instruction",
        "date format",
        "Always write dates as day month year.",
        "Draft a welcome message.",
    ),
    (
        "fact",
        "irrigation schedule",
        "Our irrigation runs at sunrise.",
        "When should we water the garden?",
    ),
)
DISTRACTORS = (
    ("fact", "bicycle", "My bicycle is blue and has a basket."),
    ("fact", "pottery", "My pottery class meets on Thursdays."),
    ("fact", "library", "Our library opens at noon on Saturdays."),
    ("fact", "orchard", "Our orchard grows pears and plums."),
    ("preference", "examples", "I prefer examples before explanations."),
    ("instruction", "spelling", "Always use British spelling."),
)


class StubModel:
    """A fixed catalogue of verbatim extraction outputs, with no provider or transport."""

    def extract(self, message):
        for kind, topic, text in (*[c[:3] for c in CASES], *DISTRACTORS):
            if message == text:
                return {"kind": kind, "topic": topic, "text": text}
        return None

    def reply(self, _message):
        return "Understood."


def prompt(bot, query, history, found):
    system = context.system_prompt(bot, learned_items=bool(found.items))
    newest = "The person's current message (quoted): " + context.quote(query)
    earlier = [
        context.quote(
            {
                "from": "person" if message["role"] == "user" else "you",
                "text": message["text"],
                "at": message["at"],
            }
        )
        for message in reversed(history)
    ]
    ctx = context.Context(
        system, [], {}, earlier, newest, learned_items=found.items, left_out=found.left_out
    )
    messages = ctx.messages([])
    return ctx.learned_used, context.size(system, messages, [])


def measure(seed, conversations):
    rng, model = random.Random(seed), StubModel()
    hits, added = [0, 0], [0, 0]
    with tempfile.TemporaryDirectory(prefix="bot-relevance-") as directory:
        supervisor = SimpleNamespace(workspace_dir=lambda _: directory)
        bots = BotStore(supervisor, "synthetic")
        try:
            learned = Learned(bots)
            learned.set_policy(WORKSPACE, "owner", enabled=True)
            for n in range(conversations):
                user = f"person-{n}"
                bot = bots.create(user, "Garden helper")
                learned.configure(bot["id"], user, mode="auto", now=NOW)
                kind, topic, target, query = CASES[n % len(CASES)]
                teaching = [(kind, topic, target), *DISTRACTORS]
                rng.shuffle(teaching)
                expected = None
                for turn, (_kind, _topic, text) in enumerate(teaching):
                    message = bots.add_user_message(bot["id"], user, text, f"teach-{turn}")
                    item = learned.add_item(
                        user,
                        bot["id"],
                        source_message_id=message["id"],
                        now=NOW,
                        **model.extract(text),
                    )
                    bots.add_bot_message(bot["id"], user, message["id"], model.reply(text))
                    if text == target:
                        expected = item["id"]
                # Push teaching beyond the same recent-history window the bot normally uses.
                for turn in range(context.HISTORY_MESSAGES // 2 + 1):
                    text = f"Thank you for helping with question {turn}."
                    message = bots.add_user_message(bot["id"], user, text, f"filler-{turn}")
                    bots.add_bot_message(bot["id"], user, message["id"], model.reply(text))
                history = bots.messages(bot["id"], user, limit=context.HISTORY_MESSAGES)
                baseline = None
                for mode, label in enumerate(("off", "auto")):
                    learned.configure(bot["id"], user, mode=label, now=NOW)
                    found = learned.recall(bot["id"], user, query, now=NOW)
                    used, chars = prompt(bot, query, history, found)
                    if baseline is None:
                        baseline = chars
                    hits[mode] += expected in used
                    added[mode] += chars - baseline
        finally:
            bots.close()
    rates = [100 * h / conversations for h in hits]
    print(f"{rates[1] - rates[0]:.2f}")
    print("| Retrieval | Conversations | Expected item in prompt (%) | Mean added prompt chars |")
    print("| --- | ---: | ---: | ---: |")
    for mode, label in enumerate(("Without learned items", "With learned items")):
        print(
            f"| {label} | {conversations} | {rates[mode]:.2f} | {added[mode] / conversations:.2f} |"
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=464)
    parser.add_argument("--conversations", type=int, default=36)
    args = parser.parse_args()
    if not 1 <= args.conversations <= 100:
        parser.error("--conversations must be between 1 and 100")
    measure(args.seed, args.conversations)


if __name__ == "__main__":
    main()

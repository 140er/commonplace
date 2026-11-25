"""The one vocabulary of review comments: statuses, reasons and the words a person reads.

Every status a comment can be in and every reason it can carry is defined here, with the
plain text and the one-sentence next step the Deck shows. The browser draws these fields and
keeps no second copy of the words. Design: ``docs/system/pr-review-loop.md``.
"""

from __future__ import annotations

NEW, WORKING, ADDRESSED, ANSWERED, NEEDS_YOU, SKIPPED = (
    "new",
    "working",
    "addressed",
    "answered",
    "needs_you",
    "skipped",
)
STATUSES = (NEW, WORKING, ADDRESSED, ANSWERED, NEEDS_YOU, SKIPPED)
# Statuses a comment can be left in by a Coworker's report.
REPORTED = (ADDRESSED, ANSWERED, NEEDS_YOU)
# Statuses that still wait on something (the card chip counts these).
WAITING = (NEW, WORKING, NEEDS_YOU)

WORDS = {
    NEW: "New",
    WORKING: "The Coworker is working on it",
    ADDRESSED: "Fixed",
    ANSWERED: "Answered",
    NEEDS_YOU: "Needs you",
    SKIPPED: "Not for the Coworker",
}

# code -> (plain text, next step). A comment's ``reason_text`` and ``next`` come from here.
REASONS = {
    "feed_stale": (
        "Comments may be missing: nothing was read from GitHub for 12 minutes.",
        "Check that the comment reader is running, or press Check now.",
    ),
    "feed_rate_limited": (
        "GitHub's rate limit is near; reading is paused until the reset time.",
        "Nothing to do; it resumes by itself.",
    ),
    "no_card": (
        "These comments are not linked to one card.",
        "Put the PR's link on the right card.",
    ),
    "ambiguous_card": (
        "More than one card has this PR's link.",
        "Keep the link on one card.",
    ),
    "lookup_failed": (
        "The board could not be read just now, so the PR's card is not known.",
        "Try again in a moment; nothing was lost.",
    ),
    "no_coworker": (
        "No Coworker is set up to take this card.",
        "Start a Coworker on the card.",
    ),
    "no_coworker_config": (
        "This board has no Coworker set up to start work by itself.",
        "Start a Coworker on the card, or set one up for the board.",
    ),
    "machine_unavailable": (
        "The machine that ran this card is offline or switched off.",
        "Bring it back, or start the card on another machine.",
    ),
    "start_refused": (
        "The start was refused.",
        "Follow the reason on the card's Runs list; transient refusals are retried a few times first.",
    ),
    "run_failed": (
        "The run ended without reporting every comment.",
        "The comments return to new once; after the second time they ask a person.",
    ),
    "run_lost": (
        "The run did not report back and was lost twice.",
        "Start the Coworker again, or handle the comments yourself.",
    ),
    "commit_not_found": (
        "The Coworker named a commit that is not on the PR.",
        "Look at the branch; retry or handle it yourself.",
    ),
    "files_unknown": (
        "The files this fix changes were not reported, so the checks rule could not be applied.",
        "Look at the change yourself, and check that the comment reader sends changed files.",
    ),
    "touches_checks": (
        "The fix changes the checks or rules that judge the PR.",
        "Read the change; accept it or ask for another.",
    ),
    "force_push_detected": (
        "The branch changed in a way the loop does not do.",
        "Look at the branch on GitHub.",
    ),
    "branch_not_owned": (
        "The PR's branch is not the card's branch.",
        "Look at the branch on GitHub, or fix the card's branch.",
    ),
    "fork_pr": (
        "The PR comes from a fork, which the loop does not work on.",
        "Handle the comments yourself.",
    ),
    "head_moved": (
        "Someone pushed to the PR while the Coworker worked; the fix cannot be placed.",
        "Retry on the new head, or handle it yourself.",
    ),
    "reply_not_posted": (
        "The fix is pushed but the reply did not reach GitHub.",
        "Post the reply yourself; its text is on the card.",
    ),
    "comment_changed": (
        "The reviewer edited the comment during the run; it will be handled again.",
        "Nothing; it runs in the next round.",
    ),
    "rounds_exhausted": (
        "The review rounds are used up and comments remain.",
        "Choose: two more rounds, handle it yourself, or dismiss.",
    ),
    "waiting_on_person": (
        "The card is waiting for a person's answer, so the Coworker is not started on it.",
        "Answer the card; the comments then go to the Coworker.",
    ),
    "card_done": (
        "The card is done, so the Coworker is not started on it.",
        "Reopen the card, or handle the comments yourself.",
    ),
    "loop_owner_unavailable": (
        "The person who switched this on can no longer start work.",
        "Switch the review loop off and on again as a workspace owner.",
    ),
    "card_not_startable": (
        "The card cannot be handed back to the Coworker right now.",
        "Open the card for the reason, or handle the comments yourself.",
    ),
    "author_not_trusted": (
        "Written by someone the workspace does not act on.",
        "Add them as a trusted reviewer, or ignore.",
    ),
    "own_reply": (
        "Written by the Coworker itself.",
        "Nothing to do.",
    ),
    "verdict_line": (
        "A review verdict, not a request.",
        "Nothing to do; a changes verdict is handled as a failing check.",
    ),
    "removed_by_reviewer": (
        "The reviewer removed this comment.",
        "Nothing to do.",
    ),
    "dismissed_by_person": (
        "A person dismissed this comment.",
        "Nothing to do.",
    ),
    "coworker_asks": (
        "The Coworker needs a decision it cannot make.",
        "Read its note and decide.",
    ),
    "handled_by_person": (
        "A person is handling the comments on this PR.",
        "Nothing to do.",
    ),
    "intake_limit": (
        "The comment limit for this PR or workspace is reached.",
        "Dismiss old comments; nothing was dropped.",
    ),
}


def reason_fields(code):
    """``{reason, reason_text, next}`` for a code (an unknown code reads as itself, never blank)."""
    text, step = REASONS.get(code or "", ("", ""))
    if code and not text:
        text, step = code, ""
    return {"reason": code or None, "reason_text": text, "next": step}

#!/usr/bin/env python3
"""Recall an invented agent gate without laundering it into a user decision."""

from __future__ import annotations

import json
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mempalace import provenance
from mempalace.knowledge_graph import KnowledgeGraph

_AGENT = dict(
    user="eval-user",
    connection="eval-agent",
    client="codex",
    auth="pat",
    trust="trusted",
    workspace="eval",
)
_PERSON = {
    **_AGENT,
    "connection": "eval-person",
    "client": "web",
    "auth": "session",
    "person": True,
}


def run():
    checks = {}
    with KnowledgeGraph.empty() as kg:

        def recall(value):
            return next(r for r in kg.query_entity("SimSteer") if r["object"] == value)

        with provenance.verified(_AGENT):
            invented = kg.save_fact(
                "SimSteer",
                "perf_gate",
                "5%",
                source_thread="mcp:agent-invention",
                source_seq_start=3,
                wing="shared",
                single_valued=True,
            )
        row = recall("5%")
        checks["invented_rule_is_proposal"] = (
            row["fact_status"] == "proposal"
            and row["origin_kind"] == "agent"
            and row["approved_by"] is None
        )
        checks["proposal_has_exact_source"] = (
            row["origin_link"] == "t3-thread://v1/mcp:agent-invention"
            and row["source_seq_start"] == 3
        )
        with provenance.verified({**_AGENT, "connection": "successor-agent"}):
            kg.save_fact(
                "SimSteer",
                "perf_gate",
                "5%",
                source_thread="mcp:repeated-invention",
                source_seq_start=7,
                wing="shared",
                single_valued=True,
            )
        row = recall("5%")
        checks["repetition_does_not_approve"] = (
            row["fact_status"] == "proposal" and row["origin_id"] == "eval-agent"
        )
        with provenance.verified(_PERSON):
            approved = kg.save_fact(
                "SimSteer",
                "perf_gate",
                "2%",
                source_thread="mcp:user-decision",
                source_seq_start=4,
                wing="shared",
                single_valued=True,
            )
        row = recall("2%")
        checks["user_decision_is_approved"] = (
            row["fact_status"] == "approved"
            and row["approved_by"] == "eval-user"
            and row["approval_link"] == "t3-thread://v1/mcp:user-decision"
        )
        old = recall("5%")
        checks["superseded_rule_links_successor"] = (
            old["id"] == invented["triple_id"]
            and old["fact_status"] == "superseded"
            and old["superseded_by"] == approved["triple_id"]
        )
        with provenance.verified(_AGENT):
            kg.save_fact(
                "SimSteer",
                "perf_gate",
                "5%",
                source_thread="mcp:later-agent",
                wing="shared",
                single_valued=True,
            )
        checks["agent_cannot_retire_user_decision"] = (
            recall("2%")["valid_to"] is None and recall("2%")["fact_status"] == "approved"
        )
    return {
        "provenance_accuracy": sum(checks.values()) / len(checks),
        "checks": checks,
        "passed": all(checks.values()),
    }


if __name__ == "__main__":
    report = run()
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report["passed"] else 1)

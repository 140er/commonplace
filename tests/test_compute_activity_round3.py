"""Unified-diff headers and repeated-prefix changed lines use actual hunk boundaries."""

import json
import sqlite3

import pytest
from activity_fixtures import END, START, source

from commonplace.compute.activity_collector import _diff_counts, collect


@pytest.mark.parametrize(
    "diff,expected",
    [
        ("--- before\n+++ after\n@@ -1 +1 @@\n---counter;\n+++counter;\n", [1, 1]),
        (
            "--- first\n+++ first\n@@ -1,2 +1,2 @@\n context\n---counter;\n+++counter;\n"
            "--- second\n+++ second\n@@ -0,0 +1 @@\n+++value;\n",
            [2, 1],
        ),
        (
            "--- before\n+++ after\n@@ -1 +1 @@\n-old\n+new\n"
            "\\ No newline at end of file\n@@ -3,0 +4,1 @@\n+++counter;\n",
            [2, 1],
        ),
    ],
)
def test_unified_diff_hunk_content_is_not_a_file_header(tmp_path, diff, expected):
    assert json.loads(_diff_counts(diff)) == expected
    path = source(tmp_path / "source.sqlite")
    with sqlite3.connect(path) as db:
        db.execute(
            "UPDATE orchestration_v2_projection_turn_items "
            "SET payload_json=json_set(payload_json,'$.diffStr',?) WHERE type='file_change'",
            (diff,),
        )
    snapshot = collect(path, START, END)
    assert snapshot["metrics"]["lines_added"] == {"value": expected[0], "reason": None}
    assert snapshot["metrics"]["lines_deleted"] == {"value": expected[1], "reason": None}

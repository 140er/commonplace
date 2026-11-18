"""Disposable 1,000-card / 20,000-event HTTP timing fixture.

Run with python -m tests.cold_api_measure [archived-source-root]. It never targets an existing service.
"""

import json
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace


def main():
    if len(sys.argv) > 1:
        sys.path.insert(0, sys.argv[1])
    import os

    for name in list(os.environ):
        if name.startswith("COMMONPLACE_"):
            os.environ.pop(name)
    from commonplace import sessions
    from commonplace.boards.service import BoardService
    from commonplace.extensions import ToolContext
    from tests._commonplace_helpers import Hub
    from tests.test_commonplace_boards import api

    def unavailable(*_):
        raise RuntimeError("Fixture worker unavailable")

    with tempfile.TemporaryDirectory(prefix="cp-594-api-") as d:
        h = Hub(Path(d), SimpleNamespace(start=unavailable))
        try:
            p = sessions.person_principal(h.db, h.admin, h.workspace)
            s = BoardService(ToolContext(p, h.gateway))
            s.board_add(slug="project", wing="shared")
            s.task_add(board="project", title="Cold fixture card")
            with s.store.connection() as c:
                cols = ",".join(r[1] for r in c.execute("pragma table_info(tasks)") if r[1] != "id")
                for _i in range(999):
                    c.execute(f"insert into tasks({cols}) select {cols} from tasks limit 1")
                c.commit()
            with h.db._lock:
                h.db._conn.executemany(
                    "INSERT INTO events(at,workspace_id,kind,subject) VALUES (?,?,?,?)",
                    [
                        ("2026-01-01T00:00:00Z", h.workspace, "card.updated", str(n))
                        for n in range(20000)
                    ],
                )
                h.db._conn.commit()
            t = sessions.redeem_login_link(
                h.db, sessions.create_login_link(h.db, "owner@example.com", h.workspace)
            )
            cookie = {"Cookie": f"{sessions.COOKIE}={t}"}
            for path in [
                "/api/boards/project/cards",
                "/api/bots",
                "/api/events?after=0&epoch=",
                "/api/needs-me",
            ]:
                samples = []
                for _n in range(3):
                    begin = time.monotonic()
                    status, body = api(h, cookie, "GET", path)
                    samples.append(round((time.monotonic() - begin) * 1000, 2))
                    assert status == 200
                print(
                    json.dumps(
                        {
                            "path": path,
                            "ms": samples,
                            "rows": len(
                                body.get("events", body.get("cards", body.get("bots", [])))
                            ),
                        }
                    ),
                    flush=True,
                )
        finally:
            h.close()


if __name__ == "__main__":
    main()

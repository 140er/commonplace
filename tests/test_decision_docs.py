"""Hermetic card-267 proofs; stdlib only. Run with python -m unittest discover."""

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TREE = Path(os.environ.get("DECISION_PROOF_TREE", Path(__file__).resolve().parents[1]))
spec = importlib.util.spec_from_file_location(
    "decision_docs_check", TREE / "commonplace/orch/docs_check.py"
)
check = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = check
spec.loader.exec_module(check)


class DecisionProofs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        # No user config, hooks, signing, credentials or network transports.
        self.env = {
            "PATH": os.defpath,
            "HOME": self.tmp.name,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_ALLOW_PROTOCOL": "",
            "LC_ALL": "C",
        }
        self.git("init", "-q")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.test")
        self.modules = "# Modules\n"
        for name, paths in (
            ("system", "`SYSTEM.md`, `MODULES.md`, `docs/`"),
            ("alpha", "`alpha.py`"),
            ("beta", "`beta.py`"),
        ):
            self.modules += f"\n## {name}\n\n- **Paths:** {paths}\n"
            self.modules += "".join(
                f"- **{field}:** {name} contract.\n" for field in check.FIELDS[1:]
            )
        self.write("MODULES.md", self.modules)
        self.write("SYSTEM.md", "# System\n\n## Decisions\n\n## Waivers\n\nNone.\n")
        self.write("alpha.py", "VALUE = 1\n")
        self.write("beta.py", "VALUE = 1\n")
        self.write("docs/decisions/topic-existing.md", "### Existing\n\nExisting contract.\n")
        self.base = self.commit()

    def git(self, *args):
        result = subprocess.run(
            [
                "git",
                "-C",
                str(self.repo),
                "-c",
                "core.hooksPath=" + os.devnull,
                "-c",
                "commit.gpgsign=false",
                *args,
            ],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def write(self, path, text):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)

    def commit(self):
        self.git("add", ".")
        self.git("commit", "-qm", "fixture")
        return self.git("rev-parse", "HEAD")

    def behavior(self, name="alpha"):
        self.write(name + ".py", "VALUE = 2\n")
        self.write(
            "MODULES.md",
            self.modules.replace(
                f"**Promises:** {name} contract.", f"**Promises:** {name} revised contract."
            ),
        )

    def test_independent_decisions_merge_cleanly_and_satisfy_obligations(self):
        for name in ("alpha", "beta"):
            self.git("checkout", "-qb", name, self.base)
            self.behavior(name)
            self.write(f"docs/decisions/topic-{name}.md", f"### {name}\n\n{name} now uses two.\n")
            self.commit()
            self.assertEqual(check.check(self.repo, self.base), [])
        self.git("merge", "--no-edit", "alpha")
        self.assertEqual(check.check(self.repo, self.base), [])
        for name in ("alpha", "beta"):
            self.assertIn(
                f"{name} now uses two.", (self.repo / f"docs/decisions/topic-{name}.md").read_text()
            )

    def test_behavior_without_decision_is_rejected(self):
        self.behavior()
        self.commit()
        self.assertIn("SYSTEM.md did not change", check.check(self.repo, self.base))

    def test_bad_decision_updates_do_not_waive_obligation(self):
        for content in (
            "",
            "### Title only\n",
            "Unstructured prose\n",
            "### Title\n\n## Body heading\n",
        ):
            with self.subTest(content=content):
                self.git("reset", "--hard", self.base)
                self.behavior()
                self.write("docs/decisions/topic-existing.md", content)
                self.commit()
                self.assertIn("SYSTEM.md did not change", check.check(self.repo, self.base))
        self.git("reset", "--hard", self.base)
        self.behavior()
        (self.repo / "docs/decisions/topic-existing.md").unlink()
        self.commit()
        self.assertIn("SYSTEM.md did not change", check.check(self.repo, self.base))

    def test_whitespace_and_unrelated_docs_do_not_count(self):
        self.behavior()
        self.write("docs/decisions/topic-existing.md", "### Existing\n\nExisting  contract.\n\n")
        self.write("docs/unrelated.md", "### Unrelated\n\nNot a system decision.\n")
        self.commit()
        self.assertIn("SYSTEM.md did not change", check.check(self.repo, self.base))

    def test_decision_does_not_waive_module_update(self):
        self.write("alpha.py", "VALUE = 2\n")
        self.write("docs/decisions/topic-new.md", "### New\n\nNow uses two.\n")
        self.commit()
        self.assertTrue(
            any("module 'alpha' changed" in p for p in check.check(self.repo, self.base))
        )

    def test_legacy_system_edit_remains_accepted(self):
        self.behavior()
        self.write("SYSTEM.md", "# System\n\n## Decisions\n\n### Legacy branch\n\nNow uses two.\n")
        self.commit()
        self.assertEqual(check.check(self.repo, self.base), [])

    def test_migration_preserves_every_byte_of_existing_decisions(self):
        # Immutable migration input works in shallow checkouts and source archives.
        original_system = (
            Path(__file__).parent / "fixtures/decision_docs/system-before-267.md"
        ).read_bytes()
        original = original_system.split(b"## Decisions\n", 1)[1].split(b"## Numbers\n", 1)[0]
        migrated = b"".join(
            p.read_bytes() for p in sorted((TREE / "docs/decisions").glob("legacy-*.md"))
        )
        self.assertEqual(migrated, original)
        # All other original lines survive, apart from explicitly replaced guidance.
        remaining = original_system.split(b"## Numbers\n", 1)[1].split(
            b"## Keeping this file current\n", 1
        )[0]
        self.assertIn(remaining, (TREE / "SYSTEM.md").read_bytes())


if __name__ == "__main__":
    unittest.main()

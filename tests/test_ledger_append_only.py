"""CLAUDE.md rule 4: the ledger is append-only, and only agent/ledger.py writes it.

Greps the repo for any update/replace/delete on `ledger`, and any direct insert outside agent/ledger.py.
Same for custody_events, which only services/scanner may write.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".venv", "node_modules", ".git", "dist", "__pycache__"}
# files allowed to mention the forbidden patterns: this test, setup/cleanup scripts run as admin
ALLOWED_MUTATORS = {"tests/test_ledger_append_only.py", "db/check_cluster.py", "db/seed.py", "scripts/reset_demo.py",
                    "tests/test_gate_integration.py"}  # admin-only setup/cleanup

MUTATION = r"(update_one|update_many|replace_one|delete_one|delete_many|find_one_and_update|" \
           r"find_one_and_replace|find_one_and_delete|bulk_write|drop|remove)"
LEDGER_MUTATION = re.compile(rf"""(\.ledger|\[["']ledger["']\])\s*\.\s*{MUTATION}\b""")
LEDGER_INSERT = re.compile(r"""(\.ledger|\[["']ledger["']\])\s*\.\s*insert_(one|many)\b""")
EVENTS_WRITE = re.compile(rf"""(\.custody_events|\[["']custody_events["']\])\s*\.\s*(insert_one|insert_many|{MUTATION})\b""")


def source_files():
    for p in ROOT.rglob("*"):
        if p.suffix in {".py", ".ts", ".tsx", ".js"} and not SKIP_DIRS & set(p.relative_to(ROOT).parts):
            yield p, p.relative_to(ROOT).as_posix()


def test_no_ledger_mutations_anywhere():
    bad = [f"{rel}:{n}" for p, rel in source_files() if rel not in ALLOWED_MUTATORS
           for n, line in enumerate(p.read_text(errors="ignore").splitlines(), 1) if LEDGER_MUTATION.search(line)]
    assert not bad, f"ledger is append-only; found mutations at {bad}"


def test_only_ledger_module_inserts_into_ledger():
    bad = [f"{rel}:{n}" for p, rel in source_files() if rel not in ALLOWED_MUTATORS | {"agent/ledger.py"}
           for n, line in enumerate(p.read_text(errors="ignore").splitlines(), 1) if LEDGER_INSERT.search(line)]
    assert not bad, f"only agent/ledger.py may insert into ledger; found {bad}"


def test_only_scanner_writes_custody_events():
    bad = [f"{rel}:{n}" for p, rel in source_files()
           if rel not in ALLOWED_MUTATORS and not rel.startswith("services/scanner/")
           for n, line in enumerate(p.read_text(errors="ignore").splitlines(), 1) if EVENTS_WRITE.search(line)]
    assert not bad, f"only services/scanner may write custody_events; found {bad}"


def test_ledger_module_exposes_only_append():
    src = (ROOT / "agent/ledger.py").read_text()
    public = re.findall(r"^(?:async )?def ([a-z]\w*)", src, re.M)
    assert public == ["append"], f"agent/ledger.py must expose only append(); found {public}"

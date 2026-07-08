"""Discover agent log files and reconcile them into the store.

Read-only with respect to the agents: we only ever read their logs. Ingest is
idempotent (events upsert by a stable id) and incremental (unchanged files are
skipped via mtime+size), so re-running is cheap and safe.
"""

from __future__ import annotations

import os
from pathlib import Path
from dataclasses import dataclass

from . import parse_claude, parse_codex, parse_spool
from .store import Store, WARDEN_HOME


@dataclass
class IngestReport:
    files_seen: int = 0
    files_ingested: int = 0
    files_skipped: int = 0
    events: int = 0
    errors: int = 0


def _claude_logs() -> list[Path]:
    root = Path.home() / ".claude" / "projects"
    if not root.exists():
        return []
    # Every *.jsonl under projects is a session or subagent transcript, except
    # workflow bookkeeping files, which our parser would just skip anyway.
    return [p for p in root.rglob("*.jsonl") if p.name != "journal.jsonl"]


def _codex_logs() -> list[Path]:
    root = Path.home() / ".codex" / "sessions"
    if not root.exists():
        return []
    return list(root.rglob("rollout-*.jsonl"))


def _spool_logs() -> list[Path]:
    root = WARDEN_HOME / "spool"
    if not root.exists():
        return []
    return sorted(root.glob("decisions-*.jsonl"))


def ingest_all(store: Store, force: bool = False, verbose: bool = False) -> IngestReport:
    rep = IngestReport()
    sources = [(p, parse_claude.parse) for p in _claude_logs()] + \
              [(p, parse_codex.parse) for p in _codex_logs()] + \
              [(p, parse_spool.parse) for p in _spool_logs()]
    state = store.load_ingest_state_map()  # one scan, not one SELECT per file

    for path, parser in sources:
        rep.files_seen += 1
        try:
            st = path.stat()
        except OSError:
            continue
        key = str(path)
        prev = state.get(key)
        if not force and prev and prev[0] == st.st_mtime and prev[1] == st.st_size:
            rep.files_skipped += 1
            continue

        n = 0
        try:
            for event in parser(key):
                store.upsert_event(event)
                n += 1
        except Exception as exc:  # one bad file must not abort the whole run
            rep.errors += 1
            if verbose:
                print(f"  ! {path.name}: {type(exc).__name__}: {exc}")
            continue

        store.set_ingest_state(key, st.st_mtime, st.st_size, n)
        store.commit()
        rep.files_ingested += 1
        rep.events += n
        if verbose:
            print(f"  + {path.name}: {n} events")

    return rep

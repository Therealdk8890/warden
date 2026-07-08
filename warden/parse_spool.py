"""Parse the enforcement spool (~/.warden/spool/decisions-*.jsonl) into Events.

The Warden hook appends one JSON record per FLAGGED action (deny / ask /
injection). We fold them into the store as kind='decision' events so they show up
in `warden timeline` and `warden decisions` alongside the reconciled tool calls.
"""

from __future__ import annotations

import json
from typing import Iterator

from .store import Event, stable_id


def parse(path: str) -> Iterator[Event]:
    with open(path, "r", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(r, dict):
                continue
            action = r.get("action", "?")
            yield Event(
                event_id=r.get("id") or stable_id(path, r.get("ts"), r.get("summary")),
                ts=r.get("ts"),
                agent="claude",                       # hooks fire for Claude Code
                kind="decision",
                session_id=r.get("session_id"),
                cwd=r.get("cwd"),
                tool_name=r.get("tool"),
                tool_namespace=action,                # deny | ask | inject-flag
                input_json=r.get("summary"),
                output_text=r.get("reason"),
                output_len=len(r.get("reason") or ""),
                is_error=1 if action == "deny" else 0,
                source_file=path,
                extra_json=json.dumps({"rule": r.get("rule"), "severity": r.get("severity"),
                                       "hook": r.get("hook")}, default=str),
            )

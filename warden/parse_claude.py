"""Parse Claude Code session logs (~/.claude/projects/**/*.jsonl) into Events.

Each line is a record. We care about three:
  - user record, message.content is a str + origin.kind == 'human'  -> prompt
  - assistant record, message.content[] has tool_use items          -> tool_call (pending)
  - user record, message.content[] has tool_result items           -> completes a tool_call
  - assistant record with message.usage                            -> usage
Everything else (thinking, text, queue-operation, attachment, system) is skipped.
"""

from __future__ import annotations

import json
from typing import Iterator

from .store import Event, FileEffect, stable_id
from .effects import shell_effects

# Claude built-in tools that mutate the filesystem, and how to read their target.
_WRITE_TOOLS = {
    "Write": "write",
    "Edit": "edit",
    "MultiEdit": "edit",
    "NotebookEdit": "edit",
}


def _namespace(tool_name: str) -> str:
    if tool_name.startswith("mcp__"):
        return "mcp"
    if tool_name in ("Bash", "BashOutput", "KillShell"):
        return "shell"
    if tool_name in ("WebFetch", "WebSearch"):
        return "web"
    return "builtin"


def _result_text(content) -> tuple[str, int]:
    """tool_result content may be a str or a list of {type:text,text:...} blocks."""
    if content is None:
        return "", 0
    if isinstance(content, str):
        return content, len(content)
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                parts.append(block.get("text") or block.get("content") or "")
            else:
                parts.append(str(block))
        s = "\n".join(p for p in parts if p)
        return s, len(s)
    s = json.dumps(content, default=str)
    return s, len(s)


def _effects_for(tool_name: str, tool_input: dict) -> list[FileEffect]:
    if not isinstance(tool_input, dict):
        return []
    op = _WRITE_TOOLS.get(tool_name)
    if op:
        path = tool_input.get("file_path") or tool_input.get("notebook_path") or tool_input.get("path")
        return [FileEffect(path=path, op=op)] if path else []
    if tool_name == "Read":
        path = tool_input.get("file_path")
        return [FileEffect(path=path, op="read")] if path else []
    if tool_name == "Bash":
        cmd = tool_input.get("command")
        return shell_effects(cmd) if cmd else []
    return []


def parse(path: str) -> Iterator[Event]:
    session_id = None
    cwd = None
    pending: dict[str, dict] = {}  # tool_use_id -> partial event data

    with open(path, "r", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict):
                continue

            session_id = rec.get("sessionId", session_id)
            cwd = rec.get("cwd", cwd)
            rtype = rec.get("type")
            ts = rec.get("timestamp")
            msg = rec.get("message")

            if rtype == "user" and isinstance(msg, dict):
                content = msg.get("content")
                if isinstance(content, str):
                    origin = rec.get("origin") or {}
                    if origin.get("kind") == "human" or rec.get("promptSource"):
                        yield Event(
                            event_id=rec.get("uuid") or stable_id(path, ts, "prompt"),
                            ts=ts, agent="claude", kind="prompt",
                            session_id=session_id, cwd=cwd,
                            input_json=content, output_len=len(content),
                            source_file=path,
                        )
                elif isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_result":
                            tid = block.get("tool_use_id")
                            if not tid:
                                continue
                            text, length = _result_text(block.get("content"))
                            is_err = 1 if block.get("is_error") else 0
                            p = pending.pop(tid, None)
                            if p is None:
                                # Result without a seen tool_use (rare); emit a stub.
                                p = dict(event_id=tid, ts=ts, tool_name="?",
                                         tool_namespace="builtin", input_json=None,
                                         mutation=0, model=None, effects=[])
                            yield Event(
                                event_id=p["event_id"], ts=p.get("ts") or ts,
                                agent="claude", kind="tool_call",
                                session_id=session_id, cwd=cwd,
                                tool_name=p["tool_name"], tool_namespace=p["tool_namespace"],
                                input_json=p["input_json"], output_text=text,
                                output_len=length, is_error=is_err,
                                mutation=p["mutation"], model=p.get("model"),
                                source_file=path,
                                effects=p["effects"],
                            )

            elif rtype == "assistant" and isinstance(msg, dict):
                model = msg.get("model")
                content = msg.get("content")
                usage = msg.get("usage")
                if isinstance(usage, dict):
                    yield Event(
                        event_id=stable_id(path, rec.get("uuid"), "usage"),
                        ts=ts, agent="claude", kind="usage",
                        session_id=session_id, cwd=cwd, model=model,
                        input_tokens=usage.get("input_tokens"),
                        output_tokens=usage.get("output_tokens"),
                        source_file=path,
                    )
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_use":
                            tid = block.get("id") or stable_id(path, ts, block.get("name"))
                            name = block.get("name") or "?"
                            tinput = block.get("input") if isinstance(block.get("input"), dict) else {}
                            effects = _effects_for(name, tinput)
                            pending[tid] = dict(
                                event_id=tid, ts=ts, tool_name=name,
                                tool_namespace=_namespace(name),
                                input_json=json.dumps(tinput, default=str),
                                mutation=1 if any(x.op in ("write", "edit", "delete", "patch", "shell") for x in effects) else 0,
                                model=model, effects=effects,
                            )

    # Tool calls still in flight when the log ended (no result yet).
    for p in pending.values():
        yield Event(
            event_id=p["event_id"], ts=p.get("ts"), agent="claude", kind="tool_call",
            session_id=session_id, cwd=cwd, tool_name=p["tool_name"],
            tool_namespace=p["tool_namespace"], input_json=p["input_json"],
            output_text=None, output_len=0, is_error=0, mutation=p["mutation"],
            model=p.get("model"), source_file=path, extra_json='{"pending":true}',
            effects=p["effects"],
        )

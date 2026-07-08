"""Parse Codex rollout logs (~/.codex/sessions/**/rollout-*.jsonl) into Events.

Records (each line has top-level `type`, `timestamp`, `payload`):
  - session_meta                          -> session_id + cwd for the file
  - event_msg / user_message              -> prompt
  - response_item / function_call         -> tool_call request (keyed by call_id)
  - response_item / function_call_output  -> completes a tool_call
  - response_item / custom_tool_call      -> apply_patch (and other custom tools)
  - response_item / custom_tool_call_output -> completes a custom tool call
  - event_msg / mcp_tool_call_end         -> enriches an MCP call (server, tool, result)
  - event_msg / token_count               -> usage (per-turn delta)

Codex file edits arrive as `custom_tool_call` (name='apply_patch'), NOT as a
function_call — missing that branch silently drops every Codex edit, so both are
handled here. Errors are inferred from exit codes since Codex has no is_error flag.
"""

from __future__ import annotations

import re
import json
import os
from typing import Iterator

from .store import Event, stable_id
from .effects import shell_effects, apply_patch_effects

_SHELL_TOOLS = {"exec_command", "shell", "local_shell", "write_stdin", "run_command"}
_EXIT_RE = re.compile(r"Process exited with code (\d+)")


def _exit_code_error(out: str | None) -> int:
    """Codex exec output ends with 'Process exited with code N'."""
    if not out:
        return 0
    m = _EXIT_RE.search(out)
    return 1 if (m and m.group(1) != "0") else 0


def _custom_output(raw) -> tuple[str, int]:
    """custom_tool_call_output.output may be text or JSON with metadata.exit_code."""
    if isinstance(raw, dict):
        meta = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
        is_err = 1 if meta.get("exit_code") not in (0, None) else 0
        text = raw.get("output") if isinstance(raw.get("output"), str) else json.dumps(raw, default=str)
        return text, is_err
    text = raw or ""
    if isinstance(text, str):
        try:
            j = json.loads(text)
            if isinstance(j, dict):
                meta = j.get("metadata") if isinstance(j.get("metadata"), dict) else {}
                is_err = 1 if meta.get("exit_code") not in (0, None) else 0
                inner = j.get("output")
                return (inner if isinstance(inner, str) else text), is_err
        except json.JSONDecodeError:
            pass
    return text, 0


def _mcp_result_text(result) -> tuple[str, int]:
    """mcp_tool_call_end result is {'Ok': {content:[{type,text}]}} or {'Err': ...}."""
    if not isinstance(result, dict):
        s = json.dumps(result, default=str)
        return s, len(s)
    payload = result.get("Ok", result.get("Err", result))
    if isinstance(payload, dict) and isinstance(payload.get("content"), list):
        parts = [b.get("text", "") for b in payload["content"] if isinstance(b, dict)]
        s = "\n".join(p for p in parts if p)
        return s, len(s)
    s = json.dumps(payload, default=str)
    return s, len(s)


def _namespace(name: str, enriched: bool) -> str:
    if enriched:
        return "mcp"
    if name in _SHELL_TOOLS:
        return "shell"
    if name in ("update_plan", "view_image", "apply_patch"):
        return "builtin"
    return "plugin"  # github/computer-use/etc. surfaced as function_calls


def parse(path: str) -> Iterator[Event]:
    session_id = os.path.basename(path).split("-", 6)[-1].rsplit(".", 1)[0] or None
    cwd = None
    pending: dict[str, dict] = {}       # call_id -> {name, arguments, ts, custom}
    mcp_enrich: dict[str, dict] = {}    # call_id -> {invocation, result}
    usage_idx = 0

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
            ts = rec.get("timestamp")
            rtype = rec.get("type")
            pl = rec.get("payload")
            if not isinstance(pl, dict):
                continue
            ptype = pl.get("type")

            if rtype == "session_meta":
                session_id = pl.get("session_id") or session_id
                cwd = pl.get("cwd") or cwd
                continue
            if rtype == "turn_context":
                cwd = pl.get("cwd") or cwd
                continue

            if rtype == "event_msg" and ptype == "user_message":
                text = pl.get("message") or ""
                if text.strip():
                    yield Event(
                        event_id=stable_id(path, ts, pl.get("client_id"), text[:40]),
                        ts=ts, agent="codex", kind="prompt",
                        session_id=session_id, cwd=cwd,
                        input_json=text, output_len=len(text), source_file=path,
                    )
                continue

            if rtype == "event_msg" and ptype == "token_count":
                info = pl.get("info") or {}
                last = info.get("last_token_usage") or {}
                if last:
                    usage_idx += 1
                    yield Event(
                        event_id=stable_id(path, "usage", usage_idx),
                        ts=ts, agent="codex", kind="usage",
                        session_id=session_id, cwd=cwd,
                        input_tokens=last.get("input_tokens"),
                        output_tokens=last.get("output_tokens"),
                        source_file=path,
                    )
                continue

            if rtype == "event_msg" and ptype == "mcp_tool_call_end":
                cid = pl.get("call_id")
                if cid:
                    mcp_enrich[cid] = {"invocation": pl.get("invocation") or {},
                                       "result": pl.get("result")}
                continue

            if rtype == "response_item" and ptype in ("function_call", "custom_tool_call"):
                cid = pl.get("call_id") or pl.get("id")
                if not cid:
                    continue
                pending[cid] = dict(
                    name=pl.get("name") or "?",
                    # function_call args are a JSON string; custom_tool_call.input is raw (e.g. a patch)
                    arguments=pl.get("arguments") if ptype == "function_call" else pl.get("input"),
                    ts=ts,
                    custom=(ptype == "custom_tool_call"),
                )
                continue

            if rtype == "response_item" and ptype in ("function_call_output", "custom_tool_call_output"):
                cid = pl.get("call_id")
                if not cid:
                    continue
                p = pending.pop(cid, None) or dict(name="?", arguments="", ts=ts, custom=False)
                if ptype == "custom_tool_call_output":
                    out, is_err = _custom_output(pl.get("output"))
                else:
                    out = pl.get("output")
                    if isinstance(out, (dict, list)):
                        out = json.dumps(out, default=str)
                    out = out or ""
                    is_err = _exit_code_error(out)
                yield _make_tool_event(path, session_id, cwd, cid, p,
                                       out, len(out or ""), is_err, mcp_enrich.get(cid))
                continue

    # Requests with no output yet (session still running).
    for cid, p in pending.items():
        yield _make_tool_event(path, session_id, cwd, cid, p, None, 0, 0,
                               mcp_enrich.get(cid), pending=True)


def _make_tool_event(path, session_id, cwd, cid, p, out, out_len, is_err,
                     enrich, pending=False) -> Event:
    name = p["name"]
    args_raw = p.get("arguments")
    display_name = name
    namespace = _namespace(name, enrich is not None)

    if name == "apply_patch":
        # args_raw is the raw patch body ("*** Begin Patch / *** Update File: …").
        patch_text = args_raw if isinstance(args_raw, str) else json.dumps(args_raw, default=str)
        effects = apply_patch_effects(patch_text)
        input_json = patch_text
    else:
        try:
            args = json.loads(args_raw) if isinstance(args_raw, str) else (args_raw or {})
        except json.JSONDecodeError:
            args = {}
        if enrich:
            inv = enrich.get("invocation") or {}
            server, tool = inv.get("server"), inv.get("tool")
            if server and tool:
                display_name = f"{server}/{tool}"
            if inv.get("arguments") is not None:
                args = inv["arguments"]
            if out is None and enrich.get("result") is not None:
                out, out_len = _mcp_result_text(enrich["result"])
                if isinstance(enrich["result"], dict) and "Err" in enrich["result"]:
                    is_err = 1
        effects = []
        if name in _SHELL_TOOLS and isinstance(args, dict):
            effects = shell_effects(args.get("cmd") or args.get("command") or "")
        input_json = (json.dumps(args, default=str)
                      if isinstance(args, (dict, list)) else str(args_raw))

    return Event(
        event_id=cid, ts=p.get("ts"), agent="codex", kind="tool_call",
        session_id=session_id, cwd=cwd, tool_name=display_name, tool_namespace=namespace,
        input_json=input_json, output_text=out, output_len=out_len, is_error=is_err,
        mutation=1 if any(e.op in ("write", "edit", "delete", "patch", "shell") for e in effects) else 0,
        source_file=path, extra_json='{"pending":true}' if pending else None, effects=effects,
    )

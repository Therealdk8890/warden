"""warden — command-line interface over the unified event store.

Commands:
  ingest         Reconcile Claude + Codex logs into the store (incremental).
  timeline       Chronological, cross-agent feed of prompts and tool calls.
  what-changed   Files touched by agents, grouped by path.
  stats          Totals: events, tools, tokens, sessions, date range.
  search         Full-text search across tool calls and prompts.
"""

from __future__ import annotations

import os
import sys
import json
import shutil
import argparse
from pathlib import Path
from datetime import datetime, timedelta, timezone

from .store import Store
from .ingest import ingest_all
from . import __version__

_TTY = sys.stdout.isatty()
_MUTATING = ("write", "edit", "delete", "patch", "shell")


def c(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _TTY else text


_AGENT_COLOR = {"claude": "38;5;208", "codex": "38;5;39"}


def _since_epoch(spec: str | None) -> float | None:
    """Parse --since: 'today', 'yesterday', 'YYYY-MM-DD', '7d', '12h'."""
    if not spec:
        return None
    now = datetime.now().astimezone()
    s = spec.strip().lower()
    if s == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    if s == "yesterday":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
        return start.timestamp()
    if s.endswith("d") and s[:-1].isdigit():
        return (now - timedelta(days=int(s[:-1]))).timestamp()
    if s.endswith("h") and s[:-1].isdigit():
        return (now - timedelta(hours=int(s[:-1]))).timestamp()
    try:
        dt = datetime.strptime(s, "%Y-%m-%d").astimezone()
        return dt.timestamp()
    except ValueError:
        # Abort rather than silently dropping the filter and returning all history.
        raise SystemExit(f"warden: unrecognized --since '{spec}' "
                         f"(use: today | yesterday | YYYY-MM-DD | 7d | 12h)")


def _local(ts_epoch: float | None) -> datetime | None:
    if ts_epoch is None:
        return None
    return datetime.fromtimestamp(ts_epoch, tz=timezone.utc).astimezone()


def _key_arg(input_json: str | None) -> str:
    if not input_json:
        return ""
    try:
        d = json.loads(input_json)
    except (json.JSONDecodeError, TypeError):
        return str(input_json)[:80]
    if not isinstance(d, dict):
        return str(d)[:80]
    for k in ("file_path", "notebook_path", "path", "command", "cmd", "query",
              "url", "pattern", "prompt"):
        if k in d and d[k]:
            return str(d[k]).replace("\n", " ")[:100]
    return json.dumps(d, default=str)[:80]


def _sync(store: Store, args) -> None:
    if getattr(args, "no_sync", False):
        return
    ingest_all(store, force=False, verbose=False)


# --------------------------------------------------------------------------- #
def cmd_ingest(store: Store, args) -> int:
    print("Reconciling agent logs …")
    rep = ingest_all(store, force=args.force, verbose=args.verbose)
    print(f"\nSeen {rep.files_seen} logs · ingested {rep.files_ingested} · "
          f"skipped {rep.files_skipped} (unchanged) · "
          f"{rep.events} events · {rep.errors} errors")
    print(f"Store: {store.path}")
    return 0


def cmd_timeline(store: Store, args) -> int:
    _sync(store, args)
    where = ["kind IN ('prompt','tool_call','decision')"]
    params: list = []
    if args.agent:
        where.append("agent = ?"); params.append(args.agent)
    if args.session:
        where.append("session_id = ?"); params.append(args.session)
    if args.tool:
        where.append("tool_name LIKE ?"); params.append(f"%{args.tool}%")
    if args.cwd:
        where.append("cwd LIKE ?"); params.append(f"%{args.cwd}%")
    if args.errors_only:
        where.append("is_error = 1")
    since = _since_epoch(args.since)
    if since is not None:
        where.append("ts_epoch >= ?"); params.append(since)

    sql = (f"SELECT * FROM events WHERE {' AND '.join(where)} "
           f"ORDER BY ts_epoch DESC LIMIT ?")
    params.append(args.limit)
    rows = list(reversed(store.query(sql, params)))  # oldest→newest within the window
    if not rows:
        print("No matching activity. Try `warden ingest` or widen filters.")
        return 0

    day = None
    for r in rows:
        dt = _local(r["ts_epoch"])
        d = dt.strftime("%Y-%m-%d") if dt else "unknown-date"
        if d != day:
            day = d
            print(c(f"\n── {day} " + "─" * 40, "2"))
        tstr = dt.strftime("%H:%M:%S") if dt else "--:--:--"
        agent = c(f"{r['agent']:<6}", _AGENT_COLOR.get(r["agent"], "0"))
        if r["kind"] == "prompt":
            body = c(f'“{(r["input_json"] or "").strip()[:100]}”', "1")
            print(f"  {c(tstr,'2')} {agent} {c('prompt','35')}  {body}")
        elif r["kind"] == "decision":
            act = r["tool_namespace"] or "?"          # deny | ask | inject-flag
            col = "31" if act == "deny" else "33"
            print(f"  {c(tstr,'2')} {agent} {c('🛡 '+act,col)} {c(r['tool_name'] or '','36')}  "
                  f"{c((r['output_preview'] or '')[:90],col)}")
        else:
            err = c(" ✗", "31") if r["is_error"] else ""
            mut = c(" ✎", "33") if r["mutation"] else ""
            name = r["tool_name"] or "?"
            ns = f"{r['tool_namespace']}:" if r["tool_namespace"] not in (None, "builtin") else ""
            osz = "  " + c(f"({r['output_len']}b)", "2") if r["output_len"] else ""
            print(f"  {c(tstr,'2')} {agent} {c(ns+name,'36')}{err}{mut}  "
                  f"{_key_arg(r['input_json'])}{osz}")
    print(c(f"\n{len(rows)} events (newest {args.limit} in window).", "2"))
    return 0


def cmd_what_changed(store: Store, args) -> int:
    _sync(store, args)
    where = []
    params: list = []
    if args.reads:
        where.append("op IN ('write','edit','delete','patch','shell','read')")
    else:
        where.append("op IN ('write','edit','delete','patch','shell')")
    if args.agent:
        where.append("agent = ?"); params.append(args.agent)
    if args.session:
        where.append("session_id = ?"); params.append(args.session)
    since = _since_epoch(args.since)
    if since is not None:
        where.append("ts_epoch >= ?"); params.append(since)

    clause = " AND ".join(where)
    total = store.query(
        f"SELECT COUNT(DISTINCT path) n FROM file_effects WHERE {clause}", params)[0]["n"]
    if not total:
        print("No file changes recorded in that window.")
        return 0
    # Aggregate per-path in SQL and LIMIT there, rather than pulling every row.
    rows = store.query(
        f"""SELECT path,
                   GROUP_CONCAT(DISTINCT op)    AS ops,
                   GROUP_CONCAT(DISTINCT agent) AS agents,
                   COUNT(*)                     AS cnt,
                   MAX(ts_epoch)                AS last
            FROM file_effects WHERE {clause}
            GROUP BY path ORDER BY last DESC LIMIT ?""", params + [args.limit])

    print(c(f"{total} paths touched · showing {len(rows)}\n", "2"))
    for r in rows:
        ops = ",".join(sorted((r["ops"] or "").split(",")))
        agents = "+".join(sorted(c(a, _AGENT_COLOR.get(a, "0"))
                                 for a in (r["agents"] or "").split(",") if a))
        last = _local(r["last"])
        when = last.strftime("%m-%d %H:%M") if last else "?"
        shell = r["path"].startswith("$(shell:")
        pcol = c(r["path"], "33" if shell else "36")
        print(f"  {c(when,'2')}  {c(ops,'35'):<24} ×{r['cnt']:<3} {agents:>6}  {pcol}")
    return 0


def cmd_stats(store: Store, args) -> int:
    _sync(store, args)
    q = store.query
    rng = q("SELECT MIN(ts_epoch) a, MAX(ts_epoch) b FROM events WHERE ts_epoch IS NOT NULL")[0]
    lo, hi = _local(rng["a"]), _local(rng["b"])
    print(c("Warden — flight recorder", "1"))
    if lo and hi:
        print(c(f"span   {lo:%Y-%m-%d} → {hi:%Y-%m-%d}", "2"))
    print()

    print(c("events by agent", "4"))
    for r in q("SELECT agent, kind, COUNT(*) n FROM events GROUP BY agent, kind ORDER BY agent, kind"):
        print(f"  {r['agent']:<7} {r['kind']:<10} {r['n']:>7}")

    print(c("\nsessions", "4"))
    for r in q("SELECT agent, COUNT(DISTINCT session_id) n FROM events GROUP BY agent"):
        print(f"  {r['agent']:<7} {r['n']:>7}")

    print(c("\ntop tools", "4"))
    for r in q("""SELECT agent, tool_name, COUNT(*) n FROM events WHERE kind='tool_call'
                  GROUP BY agent, tool_name ORDER BY n DESC LIMIT ?""", (args.top,)):
        print(f"  {r['agent']:<7} {(r['tool_name'] or '?'):<28} {r['n']:>6}")

    print(c("\ntokens (from usage records)", "4"))
    for r in q("""SELECT agent, SUM(input_tokens) i, SUM(output_tokens) o
                  FROM events WHERE kind='usage' GROUP BY agent"""):
        i, o = r["i"] or 0, r["o"] or 0
        print(f"  {r['agent']:<7} in {i:>12,}  out {o:>12,}")

    errs = q("SELECT COUNT(*) n FROM events WHERE kind='tool_call' AND is_error=1")[0]["n"]
    files = q("SELECT COUNT(DISTINCT path) n FROM file_effects WHERE op IN "
              "('write','edit','delete','patch','shell')")[0]["n"]
    print(c(f"\nfile paths changed  {files:>7}", "4"))
    print(c(f"tool-call errors    {errs:>7}", "4"))
    return 0


def cmd_search(store: Store, args) -> int:
    _sync(store, args)
    term = " ".join(args.query)
    rows = []
    if store.fts:
        try:
            rows = store.query(
                """SELECT e.* FROM events_fts JOIN events e ON e.rowid = events_fts.rowid
                   WHERE events_fts MATCH ? ORDER BY e.ts_epoch DESC LIMIT ?""",
                (term, args.limit))
        except Exception:
            rows = []
    if not rows:  # FTS unavailable or no hits → LIKE fallback
        rows = store.query(
            """SELECT * FROM events WHERE (input_json LIKE ? OR output_preview LIKE ?
               OR tool_name LIKE ?) ORDER BY ts_epoch DESC LIMIT ?""",
            (f"%{term}%", f"%{term}%", f"%{term}%", args.limit))
    if not rows:
        print(f"No matches for '{term}'.")
        return 0
    for r in rows:
        dt = _local(r["ts_epoch"])
        when = dt.strftime("%Y-%m-%d %H:%M") if dt else "?"
        agent = c(r["agent"], _AGENT_COLOR.get(r["agent"], "0"))
        label = r["tool_name"] or r["kind"]
        print(f"{c(when,'2')} {agent} {c(label,'36')}  {_key_arg(r['input_json'])}")
    print(c(f"\n{len(rows)} matches.", "2"))
    return 0


def cmd_show(store: Store, args) -> int:
    # A point lookup should not trigger a full re-ingest of active logs.
    eid = args.event_id
    rows = store.query("SELECT * FROM events WHERE event_id=?", (eid,))
    if not rows:  # allow a unique id prefix; escape LIKE wildcards in user input
        esc = eid.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        rows = store.query(
            "SELECT * FROM events WHERE event_id LIKE ? ESCAPE '\\' LIMIT 2", (esc + "%",))
        if len(rows) > 1:
            print(f"'{eid}' is ambiguous — give more of the id.")
            return 1
    if not rows:
        print(f"No event with id {eid!r}.")
        return 1
    r = rows[0]
    dt = _local(r["ts_epoch"])
    print(c(f"event   {r['event_id']}", "1"))
    print(f"when    {dt.isoformat() if dt else '?'}")
    print(f"agent   {c(r['agent'], _AGENT_COLOR.get(r['agent'], '0'))}   "
          f"session {r['session_id']}")
    print(f"kind    {r['kind']}   tool {r['tool_name']}"
          f"{' ✗error' if r['is_error'] else ''}{' ✎mutation' if r['mutation'] else ''}")
    print(f"cwd     {r['cwd']}")
    print(f"source  {r['source_file']}")
    print(c("\ninput", "4"))
    print(_pretty_json(r["input_json"]))
    print(c(f"\noutput ({r['output_len'] or 0} chars)", "4"))
    full = store.get_blob(r["output_sha"]) if r["output_sha"] else r["output_preview"]
    print(full or "(none)")
    return 0


def _pretty_json(s: str | None) -> str:
    if not s:
        return "(none)"
    try:
        return json.dumps(json.loads(s), indent=2, ensure_ascii=False)
    except (json.JSONDecodeError, TypeError):
        return str(s)


def _hook_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "hook.py")


def _settings_path(args) -> Path:
    if getattr(args, "project", None):
        return Path(args.project).expanduser() / ".claude" / "settings.json"
    return Path.home() / ".claude" / "settings.json"


def cmd_decisions(store: Store, args) -> int:
    _sync(store, args)
    where = ["kind='decision'"]
    params: list = []
    if args.action:
        where.append("tool_namespace = ?"); params.append(args.action)
    since = _since_epoch(args.since)
    if since is not None:
        where.append("ts_epoch >= ?"); params.append(since)
    rows = store.query(
        f"SELECT * FROM events WHERE {' AND '.join(where)} ORDER BY ts_epoch DESC LIMIT ?",
        params + [args.limit])
    if not rows:
        print("No enforcement decisions recorded yet.")
        print("Install the live firewall with:  warden install-hooks")
        return 0
    for r in rows:
        dt = _local(r["ts_epoch"])
        when = dt.strftime("%Y-%m-%d %H:%M") if dt else "?"
        act = r["tool_namespace"] or "?"
        col = "31" if act == "deny" else ("33" if act == "ask" else "35")
        extra = json.loads(r["extra_json"] or "{}")
        print(f"{c(when,'2')} {c('🛡 '+act, col)} {c(r['tool_name'] or '', '36')}  "
              f"[{extra.get('rule','')}]")
        print(f"    {r['output_preview'] or ''}")
        if r["input_json"]:
            print(c(f"    ↳ {r['input_json'][:160]}", "2"))
    print(c(f"\n{len(rows)} decisions.", "2"))
    return 0


def _write_json_atomic(path: Path, obj) -> None:
    """Write via temp file + os.replace so an interruption can't corrupt settings."""
    tmp = path.with_name(path.name + ".warden-tmp")
    tmp.write_text(json.dumps(obj, indent=2) + "\n")
    os.replace(tmp, path)


def _is_our_hook(cmd: str) -> bool:
    """Match our exact hook path as a whitespace-delimited token, not a loose substring."""
    return _hook_path() in (cmd or "").split()


def cmd_install_hooks(store: Store, args) -> int:
    hook = _hook_path()
    cmd = f"python3 {hook}"
    sp = _settings_path(args)
    sp.parent.mkdir(parents=True, exist_ok=True)
    settings: dict = {}
    made_backup = False
    if sp.exists():
        try:
            settings = json.loads(sp.read_text() or "{}")
        except json.JSONDecodeError:
            print(f"warden: {sp} is not valid JSON; refusing to touch it.")
            return 1
        if not isinstance(settings, dict):
            print(f"warden: {sp} is not a JSON object; refusing to touch it.")
            return 1
        bak = Path(str(sp) + ".warden-bak")
        if not bak.exists():                 # preserve the pristine pre-Warden backup
            shutil.copy2(sp, bak)
            made_backup = True
    hooks = settings.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        print(f"warden: 'hooks' in {sp} is not an object; refusing to touch it.")
        return 1
    added = []
    for event in ("PreToolUse", "PostToolUse"):
        groups = hooks.setdefault(event, [])
        if not isinstance(groups, list):
            print(f"warden: hooks.{event} in {sp} is not a list; refusing to touch it.")
            return 1
        present = any(
            isinstance(h, dict) and _is_our_hook(h.get("command"))
            for g in groups if isinstance(g, dict)
            for h in (g.get("hooks") if isinstance(g.get("hooks"), list) else []))
        if present:
            continue
        groups.append({"matcher": "*",
                       "hooks": [{"type": "command", "command": cmd, "timeout": 10}]})
        added.append(event)
    _write_json_atomic(sp, settings)
    if added:
        print(f"Installed Warden hooks ({', '.join(added)}) → {sp}")
        if made_backup:
            print(f"Backup of prior settings: {sp}.warden-bak")
    else:
        print(f"Warden hooks already present in {sp}")
    print("\nEnforcement is live for NEW Claude Code sessions (start a new session to load it).")
    print("Guardrail, not a sandbox: it reliably catches naive/accidental destructive")
    print("and credential/self-tamper actions and flags injection; it is not airtight")
    print("against a determined adversarial agent. Fail-open: a hook error never blocks a tool.")
    print(f"Undo any time:  warden uninstall-hooks"
          + (f" --project {args.project}" if getattr(args, 'project', None) else ""))
    return 0


def cmd_uninstall_hooks(store: Store, args) -> int:
    sp = _settings_path(args)
    if not sp.exists():
        print(f"No settings file at {sp}")
        return 0
    try:
        settings = json.loads(sp.read_text() or "{}")
    except json.JSONDecodeError:
        print(f"warden: {sp} is not valid JSON; refusing to touch it.")
        return 1
    if not isinstance(settings, dict) or not isinstance(settings.get("hooks"), dict):
        print(f"No Warden hooks found in {sp}.")
        return 0
    hooks = settings["hooks"]
    removed = 0
    for event in ("PreToolUse", "PostToolUse"):
        groups = hooks.get(event)
        if not isinstance(groups, list):
            continue
        kept_groups = []
        for g in groups:
            if not isinstance(g, dict):
                kept_groups.append(g)
                continue
            inner = g.get("hooks") if isinstance(g.get("hooks"), list) else []
            g["hooks"] = [h for h in inner
                          if not (isinstance(h, dict) and _is_our_hook(h.get("command")))]
            if g["hooks"]:
                kept_groups.append(g)
            else:
                removed += 1
        if kept_groups:
            hooks[event] = kept_groups
        else:
            hooks.pop(event, None)
    if not hooks:
        settings.pop("hooks", None)
    _write_json_atomic(sp, settings)
    print(f"Removed {removed} Warden hook group(s) from {sp}." if removed
          else f"No Warden hooks found in {sp}.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="warden",
                                description="Local flight recorder for AI coding agents.")
    p.add_argument("--version", action="version", version=f"warden {__version__}")
    sub = p.add_subparsers(dest="cmd")

    pi = sub.add_parser("ingest", help="reconcile agent logs into the store")
    pi.add_argument("--force", action="store_true", help="re-parse even unchanged logs")
    pi.add_argument("-v", "--verbose", action="store_true")
    pi.set_defaults(func=cmd_ingest)

    def add_read_flags(sp):
        sp.add_argument("--no-sync", action="store_true", help="skip the incremental ingest first")

    pt = sub.add_parser("timeline", help="cross-agent chronological feed")
    pt.add_argument("--agent", choices=["claude", "codex"])
    pt.add_argument("--session")
    pt.add_argument("--tool", help="substring match on tool name")
    pt.add_argument("--cwd", help="substring match on working dir")
    pt.add_argument("--since", help="today | yesterday | YYYY-MM-DD | 7d | 12h")
    pt.add_argument("--errors-only", action="store_true")
    pt.add_argument("-n", "--limit", type=int, default=60)
    add_read_flags(pt)
    pt.set_defaults(func=cmd_timeline)

    pw = sub.add_parser("what-changed", help="files touched by agents")
    pw.add_argument("--agent", choices=["claude", "codex"])
    pw.add_argument("--session")
    pw.add_argument("--since", help="today | yesterday | YYYY-MM-DD | 7d | 12h")
    pw.add_argument("--reads", action="store_true", help="include reads, not just changes")
    pw.add_argument("-n", "--limit", type=int, default=40)
    add_read_flags(pw)
    pw.set_defaults(func=cmd_what_changed)

    ps = sub.add_parser("stats", help="totals across the recorded fleet")
    ps.add_argument("--top", type=int, default=12, help="how many top tools to show")
    add_read_flags(ps)
    ps.set_defaults(func=cmd_stats)

    pf = sub.add_parser("search", help="full-text search prompts + tool calls")
    pf.add_argument("query", nargs="+")
    pf.add_argument("-n", "--limit", type=int, default=30)
    add_read_flags(pf)
    pf.set_defaults(func=cmd_search)

    psh = sub.add_parser("show", help="full detail + full output for one event")
    psh.add_argument("event_id", help="event id (or a unique prefix)")
    add_read_flags(psh)
    psh.set_defaults(func=cmd_show)

    pd = sub.add_parser("decisions", help="firewall enforcement log (deny / ask / injection)")
    pd.add_argument("--action", choices=["deny", "ask", "inject-flag"])
    pd.add_argument("--since", help="today | yesterday | YYYY-MM-DD | 7d | 12h")
    pd.add_argument("-n", "--limit", type=int, default=40)
    add_read_flags(pd)
    pd.set_defaults(func=cmd_decisions)

    pih = sub.add_parser("install-hooks",
                         help="wire the live firewall into Claude Code settings.json")
    pih.add_argument("--project", help="install into DIR/.claude/settings.json instead of global ~/.claude")
    pih.set_defaults(func=cmd_install_hooks)

    puh = sub.add_parser("uninstall-hooks", help="remove Warden's hooks from settings.json")
    puh.add_argument("--project", help="target DIR/.claude/settings.json instead of global ~/.claude")
    puh.set_defaults(func=cmd_uninstall_hooks)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if not getattr(args, "func", None):
        build_parser().print_help()
        return 0
    store = Store()
    try:
        return args.func(store, args)
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())

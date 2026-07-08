# Warden

**A local, model-agnostic flight recorder + policy guardrail for AI coding agents.**

Warden gives you one place to see *everything* your AI coding agents did across
your machine — Claude Code, OpenAI Codex, and more — and a safety floor that stops
the handful of actions you'd never want an agent to take. Everything runs locally;
nothing leaves your machine.

> Status: a personal / open-source project, not a commercial product. It's honest
> about what it is (a guardrail + audit trail) and what it isn't (a sandbox). See
> [Threat model](#threat-model-guardrail-not-a-sandbox) and [MARKET.md](MARKET.md).

---

## Why I built this

I run several coding agents (Claude Code, Codex) across dozens of projects, often
semi-autonomously. Two things bugged me:

1. **No unified memory of what they did.** Each app keeps its own siloed history.
   There was no way to ask *"what did my agents change on this machine this week?"*
   across all of them.
2. **No floor under the dangerous stuff.** When you run agents in auto-approve mode,
   nothing stops a bad `rm -rf`, a write to `~/.ssh`, or an agent quietly disabling
   its own logging.

Warden is the thing I wanted: a **black box** for the agent fleet, plus a **thin
safety net** — model-agnostic, cross-vendor, local-first.

(If you're wondering whether this is a *business*: I researched that too, honestly,
after building it — the short version is "real category, crowded and funded, better
as OSS than a startup." The full market memo is in [MARKET.md](MARKET.md).)

---

## What it does

**1. Flight recorder (read-only).** Reconciles the session logs both agents already
write to disk into one queryable SQLite timeline:

- `warden timeline` — cross-agent, chronological feed of prompts + tool calls
- `warden what-changed` — files created/edited/deleted, by which agent, when
- `warden stats` — totals: tool usage, tokens, errors, sessions
- `warden search "…"` / `warden show <id>` — full-text search + full output of any call

It never writes to the agents' own files, so it can't break or slow them down.

**2. Policy guardrail (live, Claude Code).** Hooks into Claude Code so that *before*
a tool runs, Warden can:

- **deny** catastrophic/irreversible actions — `rm -rf` on `/`|`~`|`$HOME`, fork
  bombs, `mkfs`, `dd` to a disk, `find … -delete` on home/root, and writes (via any
  tool *or* shell redirect/`cp`/`tee`/`sed -i`) to credentials, `~/.ssh`, launch
  agents, or Warden's own files (so an agent can't disable it);
- **ask** for confirmation on risky-but-legit actions — `curl … | bash`, decode-and-pipe-to-shell,
  `git push --force`, `git clean -fd`, `shutil.rmtree`;
- **flag prompt-injection** in tool results (WebFetch/Read/MCP) and warn the model.

`warden decisions` shows the enforcement log. **Fail-open by design:** the hook is
self-contained, all patterns are linear with a hard input cap (can't hang), and any
error exits cleanly — so a hook bug never blocks a tool.

---

## Install

Requires Python 3.10+. Stdlib only — no dependencies.

```bash
git clone <this repo> ~/warden
cd ~/warden

# Option A: proper install (adds a `warden` console script)
pip install -e .

# Option B: zero-install — put the launcher on your PATH
ln -sf "$PWD/bin/warden" ~/.local/bin/warden    # ~/.local/bin must be on PATH

warden ingest        # build the store from your existing agent logs
warden timeline --since today
```

Read commands auto-run a fast incremental ingest first (unchanged files skipped);
pass `--no-sync` to skip it. `--since` accepts `today | yesterday | YYYY-MM-DD | 7d | 12h`.

### Enable the live guardrail (optional)

```bash
warden install-hooks                 # global: ~/.claude/settings.json (backed up first)
warden install-hooks --project DIR   # or scope it to one project (good for testing)
warden decisions                     # see what it blocked / asked / flagged
warden uninstall-hooks               # fully reversible
```

Loads for new Claude Code sessions. To edit Warden's own source while the guardrail
is on, `warden uninstall-hooks` first (it protects its own files from tampering).

---

## How it works

- **Sources:** `~/.claude/projects/**/*.jsonl` (Claude Code) and
  `~/.codex/sessions/**/rollout-*.jsonl` (Codex). The recorder is agent-agnostic
  because it reads what the agents already log — it works for the desktop apps and CLIs.
- **Store:** `~/.warden/warden.db` — SQLite (WAL) + external-content FTS5, with full
  tool output de-duplicated into a content-addressed, zlib-compressed blob table
  (capped per output). Idempotent + incremental: events upsert by a stable id; files
  whose mtime+size are unchanged are skipped. (~113K events over 3 months of two heavy
  agents ≈ 200 MB; ~20 s cold rebuild, instant incremental.)
- **Enforcement:** a self-contained Claude Code PreToolUse/PostToolUse hook. The
  fail-closed denylist is baked into the hook, so it holds even if the rest is
  unavailable. Flagged actions spool to `~/.warden/spool/` and fold into the store as
  `decision` events.

Coverage today: recorder works for **Claude Code + Codex** (desktop apps and CLIs);
the guardrail is **Claude Code only** (Codex has its own sandbox and no equivalent hook).

---

## Threat model (guardrail, not a sandbox)

The guardrail is a **guardrail against accidents and naive/obvious bad actions**, plus
an audit trail and injection warnings. It is **not** a sandbox against a *determined
adversarial* agent: a denylist over shell-command strings is inherently evadable
(variables, encodings, alternate tools, quoting). It reliably catches the common cases
and every naive one; true containment is the job of OS-level sandboxing or the agent's
own sandbox. Treat it as **defense-in-depth** — most valuable when you run agents in
auto-approve mode and let them touch the web / untrusted files.

## Privacy

`~/.warden/warden.db` aggregates **verbatim prompts and full tool output** from every
session into one long-lived, searchable file. Tool output routinely contains secrets
(`cat .env`, printed keys, tokens in `curl` commands). Blobs are **compressed, not
encrypted.** Warden hardens the store to `0600` (dir `0700`) and warns if that fails —
but treat `~/.warden` as sensitive as `~/.ssh`. The same data already lives in the
agents' own logs; Warden's addition is *concentration*.

## Prior art & where this fits

This isn't a first-mover. By mid-2026 several funded companies and OSS projects
occupy this space — cross-vendor MCP gateways (Runlayer, MintMCP), local endpoint
tools (Operant AI), and OSS recorders (Hawkeye, Unworldly, AEGIS). Warden's niche, if
any, is **local-first + developer-owned + full non-MCP action coverage** (native
shell/file edits that gateways don't see). The honest competitive landscape is in
[MARKET.md](MARKET.md).

## License

MIT — see [LICENSE](LICENSE).

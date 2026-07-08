"""The unified event store: SQLite (WAL) + external-content FTS5 + a
content-addressed, compressed, de-duplicated blob store for tool output.

Design notes (why it's shaped this way — learned from a 478 MB / 36-min v1):
  - Tool output is stored ONCE per distinct value: sha256 -> zlib blob. Agents
    re-read the same files and re-emit the same reminders constantly, so dedup
    is a large win. The events row keeps only a bounded preview + the sha.
  - Large tool *inputs* (e.g. Write's full file body) have their long string
    values shrunk in place, so the row stays small but still valid JSON with
    file_path/command intact.
  - FTS5 is external-content keyed on rowid and maintained by triggers, so
    updates/deletes are O(log n) — v1 full-scanned the index on every upsert.

Every later Warden phase writes through this same interface.
"""

from __future__ import annotations

import os
import sys
import json
import zlib
import sqlite3
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Iterable

WARDEN_HOME = Path(os.environ.get("WARDEN_HOME", Path.home() / ".warden"))
DB_PATH = WARDEN_HOME / "warden.db"

SCHEMA_VERSION = 3
PREVIEW_CHARS = 800       # inline output preview kept on the event row
INPUT_VALUE_CAP = 1500    # per-string-value cap when shrinking tool inputs
MAX_BLOB_CHARS = 65_536   # cap full-output storage; the long tail is huge logs

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    rowid           INTEGER PRIMARY KEY,
    event_id        TEXT UNIQUE NOT NULL,
    ts              TEXT,
    ts_epoch        REAL,
    agent           TEXT NOT NULL,
    session_id      TEXT,
    cwd             TEXT,
    kind            TEXT NOT NULL,
    tool_name       TEXT,
    tool_namespace  TEXT,
    input_json      TEXT,               -- valid JSON, long values shrunk
    output_preview  TEXT,               -- first PREVIEW_CHARS of output
    output_sha      TEXT,               -- -> blobs.sha for the full output (nullable)
    output_len      INTEGER,
    is_error        INTEGER DEFAULT 0,
    mutation        INTEGER DEFAULT 0,
    model           TEXT,
    input_tokens    INTEGER,
    output_tokens   INTEGER,
    source_file     TEXT,
    extra_json      TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_ts      ON events(ts_epoch);
CREATE INDEX IF NOT EXISTS idx_events_agent   ON events(agent, ts_epoch);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id);
CREATE INDEX IF NOT EXISTS idx_events_tool    ON events(tool_name);
CREATE INDEX IF NOT EXISTS idx_events_kind    ON events(kind);

CREATE TABLE IF NOT EXISTS blobs (
    sha     TEXT PRIMARY KEY,
    z       BLOB,      -- zlib-compressed UTF-8
    len     INTEGER    -- uncompressed length in chars
);

CREATE TABLE IF NOT EXISTS file_effects (
    event_id    TEXT NOT NULL,
    path        TEXT NOT NULL,
    op          TEXT,
    inferred    INTEGER DEFAULT 0,
    agent       TEXT,
    session_id  TEXT,
    ts          TEXT,
    ts_epoch    REAL,
    PRIMARY KEY (event_id, path, op)
);
CREATE INDEX IF NOT EXISTS idx_effects_path ON file_effects(path);
CREATE INDEX IF NOT EXISTS idx_effects_ts   ON file_effects(ts_epoch);

CREATE TABLE IF NOT EXISTS ingest_state (
    source_file     TEXT PRIMARY KEY,
    mtime           REAL,
    size            INTEGER,
    records         INTEGER,
    last_ingested   TEXT
);
"""

FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(
    tool_name, input_json, output_preview, cwd,
    content='events', content_rowid='rowid'
);
CREATE TRIGGER IF NOT EXISTS events_ai AFTER INSERT ON events BEGIN
    INSERT INTO events_fts(rowid, tool_name, input_json, output_preview, cwd)
    VALUES (new.rowid, new.tool_name, new.input_json, new.output_preview, new.cwd);
END;
CREATE TRIGGER IF NOT EXISTS events_ad AFTER DELETE ON events BEGIN
    INSERT INTO events_fts(events_fts, rowid, tool_name, input_json, output_preview, cwd)
    VALUES ('delete', old.rowid, old.tool_name, old.input_json, old.output_preview, old.cwd);
END;
CREATE TRIGGER IF NOT EXISTS events_au AFTER UPDATE ON events BEGIN
    INSERT INTO events_fts(events_fts, rowid, tool_name, input_json, output_preview, cwd)
    VALUES ('delete', old.rowid, old.tool_name, old.input_json, old.output_preview, old.cwd);
    INSERT INTO events_fts(rowid, tool_name, input_json, output_preview, cwd)
    VALUES (new.rowid, new.tool_name, new.input_json, new.output_preview, new.cwd);
END;
"""


@dataclass
class Event:
    event_id: str
    ts: Optional[str]
    agent: str
    kind: str
    session_id: Optional[str] = None
    cwd: Optional[str] = None
    tool_name: Optional[str] = None
    tool_namespace: Optional[str] = None
    input_json: Optional[str] = None
    output_text: Optional[str] = None   # FULL output; store splits into preview+blob
    output_len: Optional[int] = None
    is_error: int = 0
    mutation: int = 0
    model: Optional[str] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    source_file: Optional[str] = None
    extra_json: Optional[str] = None
    effects: list["FileEffect"] = field(default_factory=list)

    @property
    def ts_epoch(self) -> Optional[float]:
        return to_epoch(self.ts)


@dataclass
class FileEffect:
    path: str
    op: str
    inferred: int = 0


def to_epoch(ts: Optional[str]) -> Optional[float]:
    if not ts:
        return None
    try:
        s = ts.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (ValueError, TypeError):
        return None


def stable_id(*parts: object) -> str:
    h = hashlib.sha1("\x1f".join(str(p) for p in parts).encode("utf-8", "replace"))
    return h.hexdigest()[:24]


def _shrink(obj, cap: int = INPUT_VALUE_CAP):
    """Recursively cap long string values so a tool input row stays small
    while remaining valid JSON (keys and short values like file_path survive)."""
    if isinstance(obj, str):
        return obj if len(obj) <= cap else obj[:cap] + f"…[+{len(obj) - cap} chars]"
    if isinstance(obj, dict):
        return {k: _shrink(v, cap) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_shrink(v, cap) for v in obj]
    return obj


def _shrink_input_json(s: Optional[str]) -> Optional[str]:
    if not s:
        return s
    try:
        return json.dumps(_shrink(json.loads(s)), default=str, ensure_ascii=False)
    except (json.JSONDecodeError, TypeError):
        return s[:4000]


class Store:
    def __init__(self, path: Path = DB_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        # Tighten permissions BEFORE enabling WAL: SQLite copies the main DB's
        # current mode onto the -wal/-shm sidecars when it creates them, so the
        # sensitive data they hold inherits 0600 rather than the default umask.
        self._harden(self.path.parent, 0o700)
        self._harden(self.path, 0o600)
        for pragma in ("journal_mode=WAL", "synchronous=NORMAL",
                       "temp_store=MEMORY", "cache_size=-40000"):
            self.conn.execute(f"PRAGMA {pragma}")
        for suffix in ("-wal", "-shm"):
            self._harden(Path(str(self.path) + suffix), 0o600)
        self._seen_sha: set[str] = set()
        self.fts = self._init_schema()

    _warned_perms = False

    @classmethod
    def _harden(cls, p: Path, mode: int) -> None:
        try:
            if p.exists():
                os.chmod(p, mode)
        except OSError as exc:
            if not cls._warned_perms:
                print(f"warden: warning — could not restrict permissions on {p} "
                      f"({exc}); ~/.warden holds raw prompts and tool output.",
                      file=sys.stderr)
                cls._warned_perms = True

    def _init_schema(self) -> bool:
        ver = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if ver < SCHEMA_VERSION:
            # The store is a rebuildable cache of on-disk logs — safe to reset.
            # (DROP IF EXISTS is a no-op on a fresh DB.)
            for stmt in ("DROP TABLE IF EXISTS events_fts",
                         "DROP TRIGGER IF EXISTS events_ai",
                         "DROP TRIGGER IF EXISTS events_ad",
                         "DROP TRIGGER IF EXISTS events_au",
                         "DROP TABLE IF EXISTS events",
                         "DROP TABLE IF EXISTS blobs",
                         "DROP TABLE IF EXISTS file_effects",
                         "DROP TABLE IF EXISTS ingest_state"):
                self.conn.execute(stmt)
            self.conn.commit()
            self.conn.execute("VACUUM")  # reclaim the file from the old data
        self.conn.executescript(SCHEMA)
        has_fts = True
        try:
            self.conn.executescript(FTS_SCHEMA)
        except sqlite3.OperationalError:
            has_fts = False  # SQLite without FTS5 -> search falls back to LIKE
        self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self.conn.commit()
        return has_fts

    # -- blob store ---------------------------------------------------------
    def _put_blob(self, text: str) -> str:
        sha = hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()
        if sha in self._seen_sha:
            return sha
        row = self.conn.execute("SELECT 1 FROM blobs WHERE sha=?", (sha,)).fetchone()
        if row is None:
            self.conn.execute(
                "INSERT OR IGNORE INTO blobs (sha, z, len) VALUES (?,?,?)",
                (sha, zlib.compress(text.encode("utf-8", "replace"), 6), len(text)),
            )
        self._seen_sha.add(sha)
        if len(self._seen_sha) > 400_000:   # bound the cache on a full/--force run
            self._seen_sha.clear()
        return sha

    def get_blob(self, sha: str) -> Optional[str]:
        row = self.conn.execute("SELECT z FROM blobs WHERE sha=?", (sha,)).fetchone()
        if row is None:
            return None
        return zlib.decompress(row["z"]).decode("utf-8", "replace")

    # -- writes -------------------------------------------------------------
    def upsert_event(self, e: Event) -> None:
        out = e.output_text
        preview, sha = None, None
        if out is not None:
            if len(out) > PREVIEW_CHARS:
                preview = out[:PREVIEW_CHARS]
                if len(out) > MAX_BLOB_CHARS:
                    out = out[:MAX_BLOB_CHARS] + f"\n…[truncated at {MAX_BLOB_CHARS} chars; {len(out)} total]"
                sha = self._put_blob(out)
            else:
                preview = out
        self.conn.execute(
            """
            INSERT INTO events (event_id, ts, ts_epoch, agent, session_id, cwd, kind,
                tool_name, tool_namespace, input_json, output_preview, output_sha,
                output_len, is_error, mutation, model, input_tokens, output_tokens,
                source_file, extra_json)
            VALUES (:event_id,:ts,:ts_epoch,:agent,:session_id,:cwd,:kind,:tool_name,
                :tool_namespace,:input_json,:output_preview,:output_sha,:output_len,
                :is_error,:mutation,:model,:input_tokens,:output_tokens,:source_file,
                :extra_json)
            ON CONFLICT(event_id) DO UPDATE SET
                ts=excluded.ts, ts_epoch=excluded.ts_epoch, cwd=excluded.cwd,
                tool_name=excluded.tool_name, tool_namespace=excluded.tool_namespace,
                input_json=excluded.input_json, model=excluded.model,
                extra_json=excluded.extra_json,
                output_preview=excluded.output_preview, output_sha=excluded.output_sha,
                output_len=excluded.output_len, is_error=excluded.is_error,
                mutation=excluded.mutation, input_tokens=excluded.input_tokens,
                output_tokens=excluded.output_tokens
            WHERE excluded.output_preview IS NOT NULL
              AND (events.output_sha IS NOT excluded.output_sha
                   OR events.output_len IS NOT excluded.output_len
                   OR events.tool_name IS NOT excluded.tool_name
                   OR events.tool_namespace IS NOT excluded.tool_namespace
                   OR events.input_json IS NOT excluded.input_json
                   OR events.is_error IS NOT excluded.is_error
                   OR events.extra_json IS NOT excluded.extra_json)
            """,
            dict(
                event_id=e.event_id, ts=e.ts, ts_epoch=e.ts_epoch, agent=e.agent,
                session_id=e.session_id, cwd=e.cwd, kind=e.kind, tool_name=e.tool_name,
                tool_namespace=e.tool_namespace, input_json=_shrink_input_json(e.input_json),
                output_preview=preview, output_sha=sha, output_len=e.output_len,
                is_error=e.is_error, mutation=e.mutation, model=e.model,
                input_tokens=e.input_tokens, output_tokens=e.output_tokens,
                source_file=e.source_file, extra_json=e.extra_json,
            ),
        )
        for eff in e.effects:
            self.conn.execute(
                """INSERT OR IGNORE INTO file_effects
                   (event_id, path, op, inferred, agent, session_id, ts, ts_epoch)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (e.event_id, eff.path, eff.op, eff.inferred, e.agent,
                 e.session_id, e.ts, e.ts_epoch),
            )

    def set_ingest_state(self, source_file: str, mtime: float, size: int, records: int) -> None:
        self.conn.execute(
            """INSERT INTO ingest_state (source_file, mtime, size, records, last_ingested)
               VALUES (?,?,?,?,?)
               ON CONFLICT(source_file) DO UPDATE SET
                 mtime=excluded.mtime, size=excluded.size,
                 records=excluded.records, last_ingested=excluded.last_ingested""",
            (source_file, mtime, size, records,
             datetime.now(timezone.utc).isoformat(timespec="seconds")),
        )

    def get_ingest_state(self, source_file: str) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            "SELECT mtime, size FROM ingest_state WHERE source_file=?", (source_file,)
        ).fetchone()

    def load_ingest_state_map(self) -> dict[str, tuple[float, int]]:
        """Whole table in one scan, so ingest does not do a SELECT per file."""
        return {r["source_file"]: (r["mtime"], r["size"])
                for r in self.conn.execute(
                    "SELECT source_file, mtime, size FROM ingest_state")}

    def commit(self) -> None:
        self.conn.commit()

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()

    # -- reads --------------------------------------------------------------
    def query(self, sql: str, params: Iterable = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, tuple(params)).fetchall()

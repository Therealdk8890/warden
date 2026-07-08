"""Warden — a local, model-agnostic flight recorder for AI coding agents.

Phase 0: a read-only reconciler that folds Claude Code and Codex on-disk
session logs into one queryable SQLite timeline. It never writes to the
agents' own files, so it cannot break them.
"""

__version__ = "0.1.0"

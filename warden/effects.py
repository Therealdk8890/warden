"""Shared, best-effort extraction of filesystem effects from shell commands.

These are *inferred* (marked inferred=1). Exact attribution of side effects is
Phase 3's job (an FSEvents watcher); here we just surface the obvious signals so
`warden what-changed` is useful on day one.
"""

from __future__ import annotations

import re
from .store import FileEffect

# Leading verbs that signal a filesystem mutation.
_SHELL_MUTATORS = {"rm", "mv", "cp", "touch", "mkdir", "tee", "dd", "chmod",
                   "chown", "ln", "truncate", "install", "rmdir", "unlink"}

_PATCH_LINE = re.compile(r"^\*\*\*\s+(Update|Add|Delete)\s+File:\s+(.+?)\s*$", re.MULTILINE)
_PATCH_OP = {"Update": "edit", "Add": "write", "Delete": "delete"}


def apply_patch_effects(cmd: str) -> list[FileEffect]:
    """Extract file paths from an `apply_patch` heredoc (Codex's patch mechanism)."""
    out = []
    for verb, path in _PATCH_LINE.findall(cmd):
        out.append(FileEffect(path=path.strip(), op=_PATCH_OP.get(verb, "edit"), inferred=1))
    return out


def shell_effects(cmd: str) -> list[FileEffect]:
    """Flag obviously file-mutating shell commands. Not exhaustive by design."""
    if not cmd:
        return []
    if "apply_patch" in cmd:
        patched = apply_patch_effects(cmd)
        if patched:
            return patched
    tokens = cmd.strip().split()
    verb = tokens[0].rsplit("/", 1)[-1] if tokens else ""
    redirect = (" > " in cmd) or (">>" in cmd) or ("| tee" in cmd)
    inplace = ("sed -i" in cmd) or ("perl -i" in cmd)
    if verb in _SHELL_MUTATORS or redirect or inplace:
        return [FileEffect(path=f"$(shell: {cmd[:120]})", op="shell", inferred=1)]
    return []

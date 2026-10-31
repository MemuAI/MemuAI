"""Pure sync planning: manifests, diffs and conflict rules.

A manifest maps a relative name to ``{"sha256", "size", "mtime"}``.  Given
the local manifest, the remote manifest and the last synced (base)
manifest, :func:`plan_sync` decides per entry:

* identical on both sides -> skip;
* only one side changed since base -> push / pull that side;
* both sides changed differently -> ``conflict``.

A conflict is resolved explicitly: ``"local"`` / ``"remote"`` wins, or
``"newer"`` — the side with the later ``mtime`` wins.  Equal mtimes stay
unresolved: the user must choose.  There is no silent merge and no silent
overwrite of a change made on both sides.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Literal, Optional

ActionKind = Literal["push", "pull", "conflict", "skip"]
Resolution = Literal["local", "remote", "newer"]


@dataclass(frozen=True)
class SyncAction:
    kind: ActionKind
    name: str
    reason: str = ""


def _same(a: Optional[Dict], b: Optional[Dict]) -> bool:
    if a is None or b is None:
        return a is b
    return a.get("sha256") == b.get("sha256") and a.get("size") == b.get("size")


def plan_sync(local: Dict[str, Dict], remote: Dict[str, Dict],
              base: Optional[Dict[str, Dict]] = None) -> List[SyncAction]:
    """Plan one-way-per-entry actions between two manifests."""
    base = base or {}
    actions: List[SyncAction] = []
    for name in sorted(set(local) | set(remote) | set(base)):
        mine, theirs, old = local.get(name), remote.get(name), base.get(name)
        if _same(mine, theirs):
            actions.append(SyncAction("skip", name, "identical"))
            continue
        if _same(mine, old):
            actions.append(SyncAction("pull", name, "changed on server"))
            continue
        if _same(theirs, old):
            actions.append(SyncAction("push", name, "changed locally"))
            continue
        actions.append(SyncAction("conflict", name, "changed on both sides"))
    return actions


def resolve_conflict(local: Optional[Dict], remote: Optional[Dict],
                     choice: Resolution) -> Literal["push", "pull", "unresolved"]:
    """Resolve one conflict. ``"newer"`` needs strictly different mtimes."""
    if choice == "local":
        return "push"
    if choice == "remote":
        return "pull"
    if choice == "newer":
        local_ts = float((local or {}).get("mtime") or 0)
        remote_ts = float((remote or {}).get("mtime") or 0)
        if local_ts > remote_ts:
            return "push"
        if remote_ts > local_ts:
            return "pull"
        return "unresolved"
    raise ValueError("unknown conflict resolution")


def apply_resolutions(actions: List[SyncAction], local: Dict[str, Dict],
                      remote: Dict[str, Dict],
                      choices: Dict[str, Resolution]) -> List[SyncAction]:
    """Replace ``conflict`` actions with push/pull per explicit choices."""
    out: List[SyncAction] = []
    for action in actions:
        if action.kind != "conflict":
            out.append(action)
            continue
        choice = choices.get(action.name)
        if choice is None:
            out.append(action)
            continue
        resolved = resolve_conflict(local.get(action.name), remote.get(action.name), choice)
        if resolved == "unresolved":
            out.append(SyncAction("conflict", action.name, "equal mtime: choose a side"))
        else:
            out.append(SyncAction(resolved, action.name, f"conflict resolved: {choice}"))
    return out

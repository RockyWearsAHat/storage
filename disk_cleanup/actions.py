"""Safe action executor — explicit deletion with dry-run default.

SAFETY RULES:
- Dry-run by default. Nothing is deleted unless --confirm is passed.
- Only operates on explicitly named paths. Never expands globs or implies extras.
- Refuses to touch protected paths.
- Refuses HIGH-risk items unless scan data confirms the user is aware.
- Every action is logged to stdout so Copilot can report what happened.
"""

from dataclasses import dataclass, field
from pathlib import Path

from disk_cleanup.cache import invalidate_categories, invalidate_scan, load_scan
from disk_cleanup.config import Config, HOME
from disk_cleanup.locks import is_locked
from disk_cleanup.scanners import RiskLevel
from disk_cleanup.utils import format_size, is_protected, move_to_trash, permanent_delete

_TRASH_DIR = HOME / ".Trash"


@dataclass
class RemoveResult:
    """Structured result from remove_paths."""
    output: str
    successes: int = 0
    failures: int = 0
    bytes_affected: int = 0
    moved_to_trash: bool = False
    trashed_paths: list[str] = field(default_factory=list)


def remove_paths(
    paths: list[str],
    config: Config,
    *,
    confirm: bool = False,
    permanent: bool = False,
) -> RemoveResult:
    """Remove explicitly listed paths.

    With confirm=False (default), prints what WOULD happen without touching anything.
    With confirm=True, performs the actual deletion.

    Returns a human-readable report of actions taken or planned.
    """
    if not paths:
        return "ERROR: No paths specified. Provide one or more paths to remove."

    resolved: list[Path] = []
    for p in paths:
        rp = Path(p).expanduser().resolve()
        resolved.append(rp)

    # Load scan data to check risk levels
    result, _ = load_scan()
    scan_lookup: dict[str, tuple[str, str]] = {}
    if result:
        for item in result.items:
            scan_lookup[str(item.path.resolve())] = (item.risk.value, item.reason)

    lines: list[str] = []
    if not confirm:
        lines.append("DRY RUN — nothing will be deleted. Pass --confirm to execute.\n")

    successes = 0
    failures = 0
    bytes_affected = 0

    for path in resolved:
        # Check existence
        if not path.exists():
            lines.append(f"  SKIP  {path} — does not exist")
            failures += 1
            continue

        # Check protection
        if is_protected(path, config):
            lines.append(f"  BLOCKED  {path} — protected path, will not touch")
            failures += 1
            continue

        # Check user locks
        if is_locked(path):
            lines.append(f"  BLOCKED  {path} — \U0001f512 LOCKED by user, will not touch")
            failures += 1
            continue

        # Check risk level from scan data
        risk_info = scan_lookup.get(str(path))
        risk_level = risk_info[0] if risk_info else "unknown"
        if risk_level == "high":
            lines.append(f"  BLOCKED  {path} — HIGH risk ({risk_info[1] if risk_info else 'unknown reason'})")
            lines.append(f"           High-risk items require manual review. Will not auto-delete.")
            failures += 1
            continue

        # Get size
        try:
            if path.is_dir():
                import subprocess
                out = subprocess.run(
                    ["du", "-sk", str(path)],
                    capture_output=True, text=True, timeout=30,
                )
                size = int(out.stdout.split()[0]) * 1024
            else:
                size = path.stat().st_size
        except (OSError, ValueError, subprocess.TimeoutExpired):
            size = 0

        # Decide method: permanent for --permanent, trash contents, or non-trash paths
        is_trash_path = (path == _TRASH_DIR.resolve()
                         or str(path).startswith(str(_TRASH_DIR.resolve()) + "/"))
        use_permanent = permanent or not config.use_trash or is_trash_path

        # Build action description
        risk_str = f" [{risk_level}]" if risk_level != "unknown" else ""
        size_str = format_size(size)

        if not confirm:
            verb = "permanently delete" if use_permanent else "move to Trash"
            lines.append(f"  WOULD {verb}: {path} ({size_str}){risk_str}")
            successes += 1
            bytes_affected += size
        else:
            # Actually perform the deletion
            if use_permanent:
                ok = permanent_delete(path)
                action = "Emptied Trash" if is_trash_path else "Permanently deleted"
            else:
                ok = move_to_trash(path)
                action = "Moved to Trash"

            if ok:
                lines.append(f"  OK  {action}: {path} ({size_str}){risk_str}")
                successes += 1
                bytes_affected += size
            else:
                lines.append(f"  FAIL  Could not delete: {path}")
                failures += 1

    # Summary
    lines.append("")
    if confirm:
        lines.append(f"Done: {successes} removed, {failures} failed/blocked, {format_size(bytes_affected)} freed")
        # Surgically invalidate only affected categories (not the whole cache)
        if successes > 0:
            affected_cats = set()
            for p in resolved:
                info = scan_lookup.get(str(p))
                if info and len(info) >= 3:
                    # scan_lookup stores (risk, reason) - look up category from scan items
                    pass
            # Find categories of deleted paths from scan data
            result_data, _ = load_scan()
            if result_data:
                deleted_strs = {str(p) for p in resolved}
                for item in result_data.items:
                    if str(item.path) in deleted_strs:
                        affected_cats.add(item.category)
            if affected_cats:
                invalidate_categories(list(affected_cats))
            else:
                invalidate_scan()
        # Remind user about Trash if items were moved there (not permanently deleted)
        any_trashed = not (permanent or not config.use_trash) and successes > 0
        all_were_trash_paths = all(
            str(p).startswith(str(_TRASH_DIR.resolve())) or p == _TRASH_DIR.resolve()
            for p in resolved
        )
        if any_trashed and not all_were_trash_paths:
            lines.append("Note: Items were moved to Trash. Empty Trash to reclaim disk space.")
    else:
        lines.append(f"Plan: {successes} would be removed ({format_size(bytes_affected)}), {failures} blocked/skipped")
        if successes > 0:
            lines.append(f"To execute: re-run with --confirm")

    return RemoveResult(
        output="\n".join(lines),
        successes=successes,
        failures=failures,
        bytes_affected=bytes_affected,
        moved_to_trash=any_trashed and not all_were_trash_paths if confirm else False,
        trashed_paths=[str(p) for p in resolved] if (confirm and any_trashed and not all_were_trash_paths) else [],
    )

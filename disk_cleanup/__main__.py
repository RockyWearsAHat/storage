"""Entry point for storage — AI-assisted macOS disk management.

`storage` with no arguments launches the custom REPL.
`storage <question>` runs a one-shot prompt through Copilot CLI.
Subcommands (scan, query, info, rm, etc.) are the tools Copilot calls.
"""

import argparse
import sys


def cmd_query(args):
    """Handle the `storage query` subcommand."""
    from disk_cleanup.query import query_scan, query_summary

    if getattr(args, "summary", False):
        print(query_summary(as_json=getattr(args, "json", False)))
        return

    print(query_scan(
        category=args.category,
        risk=args.risk,
        older_than_days=args.older_than,
        newer_than_days=args.newer_than,
        larger_than_mb=args.larger_than,
        smaller_than_mb=args.smaller_than,
        path_contains=args.path_contains,
        sort_by=args.sort,
        limit=args.limit,
        as_json=getattr(args, "json", False),
    ))


def cmd_info(args):
    """Handle the `storage info` subcommand."""
    from disk_cleanup.query import path_info
    print(path_info(args.path, as_json=getattr(args, "json", False)))


def cmd_rm(args):
    """Handle the `storage rm` subcommand."""
    from disk_cleanup.actions import remove_paths
    from disk_cleanup.config import Config

    config = Config.load()
    result = remove_paths(
        args.paths,
        config,
        confirm=args.confirm,
        permanent=args.permanent,
    )
    print(result.output)


def cmd_manifest(args):
    """Handle the `storage manifest` subcommand — show manifest status."""
    from disk_cleanup.manifest import load_manifest, discover_new_paths
    from disk_cleanup.scanners import CATEGORY_LABELS, Category
    from disk_cleanup.utils import format_size
    from datetime import datetime, timezone

    manifest = load_manifest()
    if manifest is None:
        print("No manifest found. Run `storage scan` to build one.")
        return

    created = manifest.get("created", 0)
    updated = manifest.get("updated", 0)
    last_full = manifest.get("last_full_discovery", 0)
    paths = manifest.get("paths", {})

    def _ts(t: float) -> str:
        if not t:
            return "never"
        return datetime.fromtimestamp(t, tz=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")

    print(f"Manifest v{manifest.get('version', '?')}")
    print(f"  Created:        {_ts(created)}")
    print(f"  Last updated:   {_ts(updated)}")
    print(f"  Last full scan: {_ts(last_full)}")
    print()

    total_paths = 0
    for cat_val in sorted(paths.keys()):
        entries = paths[cat_val]
        total_paths += len(entries)
        try:
            label = CATEGORY_LABELS.get(Category(cat_val), cat_val)
        except ValueError:
            label = cat_val
        existing = sum(1 for e in entries if e.get("exists", True))
        print(f"  {label:<35} {existing:>4} paths")

    print(f"\n  Total tracked paths: {total_paths}")

    # Show newly discovered paths
    new = discover_new_paths()
    if new:
        print(f"\n  New directories discovered ({sum(len(v) for v in new.values())} total):")
        for cat, dirs in new.items():
            label = CATEGORY_LABELS.get(cat, cat.value)
            for d in dirs[:5]:
                print(f"    [{label}] {d}")
            if len(dirs) > 5:
                print(f"    ... and {len(dirs) - 5} more")
    else:
        print("\n  No new directories discovered.")


def cmd_empty_trash(args):
    """Handle the `storage empty-trash` subcommand."""
    from disk_cleanup.cache import invalidate_scan
    from disk_cleanup.utils import empty_trash, format_size

    confirm = getattr(args, "confirm", False)
    if not confirm:
        # Dry-run: just show what's in the Trash
        from pathlib import Path
        trash_dir = Path.home() / ".Trash"
        if not trash_dir.exists() or not any(trash_dir.iterdir()):
            print("Trash is already empty.")
            return
        total = 0
        count = 0
        for child in trash_dir.iterdir():
            count += 1
            try:
                if child.is_dir():
                    import subprocess
                    out = subprocess.run(
                        ["du", "-sk", str(child)],
                        capture_output=True, text=True, timeout=30,
                    )
                    total += int(out.stdout.split()[0]) * 1024
                else:
                    total += child.stat().st_size
            except (OSError, ValueError):
                pass
        print(f"DRY RUN — Trash contains {count} items ({format_size(total)}).")
        print("To empty: re-run with --confirm")
        return

    ok, freed = empty_trash()
    if ok and freed > 0:
        print(f"OK  Trash emptied. {format_size(freed)} freed.")
        invalidate_scan()
    elif ok:
        print("Trash is already empty.")
    else:
        print("FAIL  Could not empty Trash.")


def cmd_lock(args):
    """Handle the `storage lock` subcommand."""
    from disk_cleanup.locks import lock_path
    reason = getattr(args, "reason", "") or ""
    ok, msg = lock_path(args.path, reason=reason)
    print(msg)


def cmd_unlock(args):
    """Handle the `storage unlock` subcommand."""
    from disk_cleanup.locks import unlock_path
    ok, msg = unlock_path(args.path)
    print(msg)


def cmd_locks(args):
    """Handle the `storage locks` subcommand — list all locks."""
    from datetime import datetime, timezone
    from disk_cleanup.locks import list_locks
    from disk_cleanup.utils import format_size
    from pathlib import Path

    locks = list_locks()
    if not locks:
        print("No locked paths.")
        return

    print(f"{len(locks)} locked path(s):\n")
    for entry in locks:
        p = Path(entry["path"])
        locked_at = entry.get("locked_at", 0)
        reason = entry.get("reason", "")
        ts = datetime.fromtimestamp(locked_at, tz=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M") if locked_at else "unknown"

        # Get current size
        try:
            if p.is_dir():
                import subprocess
                out = subprocess.run(["du", "-sk", str(p)], capture_output=True, text=True, timeout=30)
                size = int(out.stdout.split()[0]) * 1024
            elif p.exists():
                size = p.stat().st_size
            else:
                size = 0
        except (OSError, ValueError, subprocess.TimeoutExpired):
            size = 0

        size_str = format_size(size) if size else "?"
        print(f"  \U0001f512 {p}  ({size_str})")
        print(f"     Locked: {ts}" + (f"  Reason: {reason}" if reason else ""))
        if not p.exists():
            print(f"     \u26a0\ufe0f  Path no longer exists")


def _launch_repl(*, yolo: bool = True):
    """Launch the custom storage REPL."""
    from disk_cleanup.repl import run_repl
    run_repl(yolo=yolo)


def _one_shot(prompt: str, *, yolo: bool = True):
    """Run a single prompt through Copilot and print the result."""
    import os
    from disk_cleanup.repl import _find_copilot, _get_model, _load_prefs, _run_copilot

    copilot_bin = _find_copilot()
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    model = _get_model(_load_prefs())

    _run_copilot(copilot_bin, model, prompt, project_root, yolo=yolo)


def main():
    # Parse global flags before dispatching
    yolo = "--no-yolo" not in sys.argv
    argv = [a for a in sys.argv[1:] if a != "--no-yolo"]

    # No arguments → launch the REPL
    if not argv:
        _launch_repl(yolo=yolo)
        return

    # Check if the first arg is a known subcommand; if not, treat everything
    # as a natural-language prompt for Copilot (one-shot mode)
    known_commands = {
        "scan", "clean", "analyze", "overview", "quick", "map", "whereis",
        "query", "info", "rm", "empty-trash", "manifest",
        "lock", "unlock", "locks",
        "-h", "--help", "-i", "--interactive",
    }
    if argv[0] not in known_commands:
        _one_shot(" ".join(argv), yolo=yolo)
        return

    # Restore sys.argv for argparse (without --no-yolo)
    sys.argv = [sys.argv[0]] + argv

    # Otherwise, parse and run the subcommand (these are what Copilot calls)
    from disk_cleanup.cli import (
        cmd_analyze, cmd_clean, cmd_map, cmd_overview, cmd_quick, cmd_scan, cmd_whereis,
    )

    parser = argparse.ArgumentParser(
        prog="storage",
        description="storage — macOS disk management, powered by GitHub Copilot CLI",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # scan
    p_scan = subparsers.add_parser("scan", help="Scan for cleanable files (incremental by default)")
    p_scan.add_argument("--category", "-c", help="Scan a specific category only")
    p_scan.add_argument("--full", action="store_true",
                        help="Force a full rescan (skip incremental optimization)")
    p_scan.set_defaults(func=cmd_scan)

    # clean
    p_clean = subparsers.add_parser("clean", help="Interactive cleanup with confirmation")
    p_clean.add_argument("--category", "-c", help="Clean a specific category only")
    p_clean.add_argument("--permanent", action="store_true",
                         help="Permanently delete instead of moving to Trash")
    p_clean.set_defaults(func=cmd_clean)

    # analyze
    p_analyze = subparsers.add_parser("analyze", help="Generate AI analysis report")
    p_analyze.add_argument("--json", action="store_true", help="Output raw JSON")
    p_analyze.add_argument("--copilot", action="store_true",
                           help="Output a prompt for GitHub Copilot Chat")
    p_analyze.set_defaults(func=cmd_analyze)

    # overview
    p_overview = subparsers.add_parser("overview", help="Show disk usage overview")
    p_overview.set_defaults(func=cmd_overview)

    # quick
    p_quick = subparsers.add_parser("quick", help="Auto-clean only safe items")
    p_quick.add_argument("--permanent", action="store_true",
                         help="Permanently delete instead of moving to Trash")
    p_quick.set_defaults(func=cmd_quick)

    # map
    p_map = subparsers.add_parser("map", help="Map disk usage for any directory")
    p_map.add_argument("path", nargs="?", default=None,
                       help="Directory to map (default: home directory)")
    p_map.add_argument("--min-size", type=float, default=100,
                       help="Minimum directory size in MB to display (default: 100)")
    p_map.add_argument("--no-interactive", action="store_true",
                       help="Just show the map, skip interactive drill-down")
    p_map.set_defaults(func=cmd_map)

    # whereis
    p_whereis = subparsers.add_parser("whereis",
                                      help="Full system breakdown — where is ALL your space going?")
    p_whereis.set_defaults(func=cmd_whereis)

    # query
    p_query = subparsers.add_parser("query", help="Query scan data with filters")
    p_query.add_argument("--category", "-c", help="Filter by category")
    p_query.add_argument("--risk", "-r", help="Filter by risk level")
    p_query.add_argument("--older-than", type=float, metavar="DAYS",
                         help="Only items older than N days")
    p_query.add_argument("--newer-than", type=float, metavar="DAYS",
                         help="Only items newer than N days")
    p_query.add_argument("--larger-than", type=float, metavar="MB",
                         help="Only items larger than N MB")
    p_query.add_argument("--smaller-than", type=float, metavar="MB",
                         help="Only items smaller than N MB")
    p_query.add_argument("--path-contains", metavar="TEXT",
                         help="Only items whose path contains this text")
    p_query.add_argument("--sort", choices=["size", "age", "name", "risk"], default="size",
                         help="Sort order (default: size)")
    p_query.add_argument("--limit", type=int, default=50,
                         help="Max items to return (default: 50)")
    p_query.add_argument("--json", action="store_true", help="Output as JSON")
    p_query.add_argument("--summary", action="store_true",
                         help="Show summary by category/risk instead of item list")
    p_query.set_defaults(func=cmd_query)

    # info
    p_info = subparsers.add_parser("info", help="Inspect a file or directory in detail")
    p_info.add_argument("path", help="Path to inspect")
    p_info.add_argument("--json", action="store_true", help="Output as JSON")
    p_info.set_defaults(func=cmd_info)

    # rm
    p_rm = subparsers.add_parser("rm", help="Remove specific paths (dry-run by default)")
    p_rm.add_argument("paths", nargs="+", help="Paths to remove")
    p_rm.add_argument("--confirm", action="store_true",
                      help="Actually perform deletion (without this flag, only shows what WOULD happen)")
    p_rm.add_argument("--permanent", action="store_true",
                      help="Permanently delete instead of moving to Trash")
    p_rm.set_defaults(func=cmd_rm)

    # empty-trash
    p_empty = subparsers.add_parser("empty-trash", help="Empty the macOS Trash")
    p_empty.add_argument("--confirm", action="store_true",
                         help="Actually empty the Trash (without this flag, only shows what's in it)")
    p_empty.set_defaults(func=cmd_empty_trash)

    # manifest
    p_manifest = subparsers.add_parser("manifest",
                                       help="Show the adaptive scan manifest (debug)")
    p_manifest.set_defaults(func=cmd_manifest)

    # lock
    p_lock = subparsers.add_parser("lock",
                                   help="Lock a path — protects it from all operations")
    p_lock.add_argument("path", help="Path to lock")
    p_lock.add_argument("--reason", "-r", default="",
                        help="Optional reason for locking")
    p_lock.set_defaults(func=cmd_lock)

    # unlock
    p_unlock = subparsers.add_parser("unlock",
                                     help="Unlock a previously locked path")
    p_unlock.add_argument("path", help="Path to unlock")
    p_unlock.set_defaults(func=cmd_unlock)

    # locks
    p_locks = subparsers.add_parser("locks",
                                    help="List all locked paths")
    p_locks.set_defaults(func=cmd_locks)

    args = parser.parse_args()

    if not args.command:
        _launch_repl()
        return

    # Subcommands run directly (no banner for programmatic commands)
    args.func(args)


if __name__ == "__main__":
    main()

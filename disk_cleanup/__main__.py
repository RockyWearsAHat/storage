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


def _launch_repl():
    """Launch the custom storage REPL."""
    from disk_cleanup.repl import run_repl
    run_repl()


def _one_shot(prompt: str):
    """Run a single prompt through Copilot and print the result."""
    import os
    from disk_cleanup.repl import _find_copilot, _get_model, _load_prefs, _run_copilot

    copilot_bin = _find_copilot()
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    model = _get_model(_load_prefs())

    _run_copilot(copilot_bin, model, prompt, project_root)


def main():
    # No arguments → launch the REPL
    if len(sys.argv) == 1:
        _launch_repl()
        return

    # Check if the first arg is a known subcommand; if not, treat everything
    # as a natural-language prompt for Copilot (one-shot mode)
    known_commands = {
        "scan", "clean", "analyze", "overview", "quick", "map", "whereis",
        "query", "info", "rm", "empty-trash", "-h", "--help", "-i", "--interactive",
    }
    if sys.argv[1] not in known_commands:
        _one_shot(" ".join(sys.argv[1:]))
        return

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
    p_scan = subparsers.add_parser("scan", help="Scan for cleanable files")
    p_scan.add_argument("--category", "-c", help="Scan a specific category only")
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

    args = parser.parse_args()

    if not args.command:
        _launch_repl()
        return

    # Subcommands run directly (no banner for programmatic commands)
    args.func(args)


if __name__ == "__main__":
    main()

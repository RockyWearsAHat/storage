"""Interactive interface — the main experience for storage.

When you run `storage` you land here.  A numbered menu offers common
safe operations up front.  Type 'copilot' at any time to generate a
prompt for GitHub Copilot Chat with full context about your disk.
"""

import shlex
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from disk_cleanup.ai_advisor import generate_analysis, generate_copilot_prompt
from disk_cleanup.cache import format_age, is_stale, load_scan, save_scan
from disk_cleanup.cleaner import delete_items
from disk_cleanup.cli import (
    RISK_COLORS,
    RISK_ICONS,
    _interactive_drill,
    _render_node_table,
    _show_files_in_dir,
    _size_bar,
    run_scan_with_progress,
    show_category_detail,
    show_disk_overview,
    show_scan_results,
)
from disk_cleanup.config import Config, HOME
from disk_cleanup.disk_map import drill_into, get_full_disk_breakdown, map_disk
from disk_cleanup.scanners import CATEGORY_LABELS, Category, CleanupItem, RiskLevel, ScanResult
from disk_cleanup.utils import format_size, get_disk_usage

console = Console()


# ── Main menu shown on launch ──────────────────────────────────────
MAIN_MENU = """
[bold cyan]What would you like to do?[/]

  [bold cyan]1[/]  Scan            Run a full disk scan
  [bold cyan]2[/]  Quick Clean     Delete only safe items (caches, temp files)
  [bold cyan]3[/]  Clean           Review & select what to delete category by category
  [bold cyan]4[/]  Empty Trash     Reclaim space from macOS Trash
  [bold cyan]5[/]  Disk Map        See where all your space is going
  [bold cyan]6[/]  Copilot         Generate a prompt for GitHub Copilot Chat

  [bold cyan]7[/]  All Commands    Full command list for advanced usage
  [bold cyan]0[/]  Quit
"""

# ── Full command list ──────────────────────────────────────────────
HELP_TEXT = """
[bold cyan]Commands:[/]

  [bold]scan[/]                     Run a full disk scan (or rescan)
  [bold]scan [/][dim]<category>[/]          Scan a specific category (caches, downloads, etc.)
  [bold]status[/]                   Show current scan data + disk overview
  [bold]results[/]                  Show the last scan results table
  [bold]show [/][dim]<category>[/]          Show details for a category
  [bold]categories[/]               List all scan categories

  [bold]map[/]                      Map home directory usage
  [bold]map [/][dim]<path>[/]               Map a specific directory
  [bold]whereis[/]                  Full system-wide disk breakdown

  [bold]clean[/]                    Interactive category-by-category cleanup
  [bold]clean safe[/]               Auto-clean only safe/zero-risk items
  [bold]clean [/][dim]<category>[/]         Clean a specific category
  [bold]trash[/]                    Empty the macOS Trash

  [bold]copilot[/]                  Generate a prompt for GitHub Copilot Chat
  [bold]copilot map[/]              Generate a Copilot prompt from disk map

  [bold]menu[/]                     Back to main menu
  [bold]help[/]                     Show this help
  [bold]quit[/]                     Exit storage
"""


def run_interactive(config: Config, do_scan: bool = False):
    """Main interactive entry point."""
    # Load or run a scan so we always have data
    result, age = _ensure_scan_data(config, do_scan)

    # Show disk overview and scan summary
    show_disk_overview()
    _show_quick_status(result, age)

    # Enter the main menu loop
    _main_menu_loop(config, result, age)


# ── Main menu ──────────────────────────────────────────────────────

def _main_menu_loop(config: Config, result: ScanResult | None, age: float | None):
    """Numbered menu loop — the default experience."""
    while True:
        console.print(MAIN_MENU)
        try:
            choice = console.input("[bold cyan]>[/] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Have a good one![/]")
            return

        if choice in ("0", "quit", "exit", "q"):
            console.print("[dim]Have a good one![/]")
            return

        elif choice in ("1", "scan"):
            result = _do_scan(config)
            age = 0

        elif choice in ("2", "quick", "quick clean"):
            result = _do_quick_clean(config, result)

        elif choice in ("3", "clean"):
            result = _do_interactive_clean(config, result)

        elif choice in ("4", "trash"):
            _do_empty_trash(config)

        elif choice in ("5", "map", "disk map"):
            _do_disk_map()

        elif choice in ("6", "copilot"):
            _do_copilot(config, result)

        elif choice in ("7", "commands", "cmd", "all"):
            result, age = _command_loop(config, result, age)

        else:
            console.print("[yellow]Pick a number 0-7, or type a command name.[/]")


def _copilot_hint():
    """Print a short hint that Copilot is available."""
    console.print("\n  [dim]Tip: type [bold]copilot[/bold] to get AI-powered recommendations for what you just saw.[/]")


# ── Scan helpers ───────────────────────────────────────────────────

def _ensure_scan_data(config: Config, force: bool) -> tuple[ScanResult | None, float | None]:
    """Load cached scan or run a fresh one."""
    result, age = load_scan()

    if force or result is None:
        console.print("[bold]Running initial scan...[/]\n")
        result = run_scan_with_progress(config)
        save_scan(result)
        return result, 0

    if is_stale(age):
        console.print(f"[dim]Last scan: {format_age(age)} (stale)[/]")
        if Confirm.ask("Scan data is over an hour old. Rescan?", default=True):
            result = run_scan_with_progress(config)
            save_scan(result)
            return result, 0

    return result, age


def _show_quick_status(result: ScanResult | None, age: float | None):
    """One-liner status below the disk bar."""
    if not result:
        console.print("  [yellow]No scan data yet.[/]\n")
        return

    safe_items = [i for i in result.items if i.risk == RiskLevel.SAFE]
    safe_total = sum(i.size_bytes for i in safe_items)
    age_str = format_age(age) if age is not None else "unknown"

    console.print(
        f"  Scan: [bold]{len(result.items)}[/] items, "
        f"[bold cyan]{format_size(result.total_size)}[/] reclaimable  "
        f"[dim]({format_size(safe_total)} safe)  •  {age_str}[/]"
    )


# ── Menu actions ───────────────────────────────────────────────────

def _do_scan(config: Config) -> ScanResult:
    """Run a full scan and show results."""
    result = run_scan_with_progress(config)
    save_scan(result)
    show_scan_results(result)
    _copilot_hint()
    return result


def _do_quick_clean(config: Config, result: ScanResult | None) -> ScanResult:
    """Delete only safe items."""
    if not result or not result.items:
        console.print("[dim]No scan data — scanning first...[/]\n")
        result = run_scan_with_progress(config)
        save_scan(result)

    safe_items = [i for i in result.items if i.risk == RiskLevel.SAFE]
    if not safe_items:
        console.print("[green]Nothing safe to clean — your disk is tidy![/]")
        return result

    total = sum(i.size_bytes for i in safe_items)
    console.print(f"\n[bold]Quick Clean: {len(safe_items)} safe items, {format_size(total)}[/]\n")

    table = Table(show_lines=False, border_style="dim")
    table.add_column("Size", justify="right", width=10)
    table.add_column("Item")

    for item in safe_items[:15]:
        try:
            display = f"~/{item.path.relative_to(HOME)}"
        except ValueError:
            display = str(item.path)
        table.add_row(format_size(item.size_bytes), display)

    if len(safe_items) > 15:
        table.add_row("...", f"and {len(safe_items) - 15} more")

    console.print(table)

    method = "Trash" if config.use_trash else "permanent deletion"
    console.print(f"\n[dim]Method: {method}[/]")

    if not Confirm.ask(f"Delete all {len(safe_items)} safe items?", default=False):
        console.print("[yellow]Cancelled.[/]")
        return result

    success, failed, freed = delete_items(safe_items, config)
    console.print(f"\n[bold green]Done![/] Freed {format_size(freed)}")
    if failed:
        console.print(f"[yellow]{failed} items failed[/]")
    _copilot_hint()

    return result


def _do_interactive_clean(config: Config, result: ScanResult | None) -> ScanResult:
    """Category-by-category interactive cleanup."""
    if not result or not result.items:
        console.print("[dim]No scan data — scanning first...[/]\n")
        result = run_scan_with_progress(config)
        save_scan(result)
        show_scan_results(result)

    if not result.items:
        console.print("[green]Nothing to clean![/]")
        return result

    from disk_cleanup.cli import interactive_clean
    interactive_clean(result, config)
    _copilot_hint()
    return result


def _do_empty_trash(config: Config):
    """Empty macOS Trash."""
    from disk_cleanup.utils import dir_size
    import subprocess

    trash_path = HOME / ".Trash"
    if not trash_path.exists():
        console.print("[green]Trash is empty.[/]")
        return

    size = dir_size(trash_path)
    if size < 1024 * 1024:
        console.print("[green]Trash is basically empty.[/]")
        return

    console.print(f"Trash: [bold]{format_size(size)}[/]")
    if Confirm.ask("Empty Trash?", default=False):
        try:
            subprocess.run(
                ["osascript", "-e", 'tell application "Finder" to empty trash'],
                capture_output=True, timeout=60, check=True,
            )
            console.print("[bold green]Trash emptied![/]")
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            console.print("[red]Failed to empty trash.[/]")


def _do_disk_map():
    """Show where space is going, with interactive drill-down."""
    disk = get_disk_usage()
    disk_total = disk["total"]

    console.print(f"\n[bold]Mapping [cyan]~[/cyan]...[/]\n")

    with console.status("Scanning..."):
        root = map_disk(HOME, depth=3, min_size_mb=100)

    if not root.children:
        console.print(f"[yellow]No directories above 100 MB.[/]")
        return

    console.print(f"[bold]{format_size(root.size_bytes)}[/] total  •  Showing ≥ 100 MB\n")
    table = _render_node_table(root, disk_total)
    console.print(table)
    _interactive_drill(root, disk_total, 100)
    _copilot_hint()


def _do_copilot(config: Config, result: ScanResult | None):
    """Generate a Copilot Chat prompt from scan data or disk map."""
    console.print()
    console.print("[bold cyan]Copilot Analysis[/]")
    console.print("Generate a prompt you can paste into GitHub Copilot Chat")
    console.print("for AI-powered cleanup recommendations.\n")

    console.print("  [bold cyan]1[/]  From scan results — cleanup recommendations")
    console.print("  [bold cyan]2[/]  From disk map — full system breakdown")
    console.print("  [bold cyan]0[/]  Back\n")

    choice = Prompt.ask("Choose", choices=["0", "1", "2"], default="1")

    if choice == "0":
        return

    if choice == "1":
        if not result or not result.items:
            console.print("[dim]No scan data — scanning first...[/]\n")
            result = run_scan_with_progress(config)
            save_scan(result)

        analysis = generate_analysis(result, config)
        prompt = generate_copilot_prompt(analysis)
        console.print(Panel(prompt, title="Copy this into GitHub Copilot Chat", border_style="cyan"))

    elif choice == "2":
        with console.status("Scanning for full disk map..."):
            breakdown = get_full_disk_breakdown()
        disk = get_disk_usage()

        from disk_cleanup.cli import _generate_map_copilot_prompt
        prompt = _generate_map_copilot_prompt(breakdown, disk)
        console.print(Panel(prompt, title="Copy this into GitHub Copilot Chat", border_style="cyan"))


# ── All Commands (advanced usage) ──────────────────────────────────

def _command_loop(config: Config, result: ScanResult | None, age: float | None) -> tuple[ScanResult | None, float | None]:
    """Full command prompt for advanced usage."""
    console.print(HELP_TEXT)

    while True:
        try:
            console.print()
            raw = console.input("[bold cyan]storage>[/] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Back to menu.[/]")
            return result, age

        if not raw:
            continue

        try:
            parts = shlex.split(raw)
        except ValueError:
            parts = raw.split()

        cmd = parts[0].lower()
        args = parts[1:]

        if cmd in ("quit", "exit", "q"):
            console.print("[dim]Have a good one![/]")
            raise SystemExit(0)

        elif cmd in ("menu", "back"):
            return result, age

        elif cmd == "help":
            console.print(HELP_TEXT)

        elif cmd == "scan":
            result = _cmd_scan(config, args, result)
            age = 0

        elif cmd == "status":
            _cmd_status(result, age)

        elif cmd in ("results", "summary"):
            if result:
                show_scan_results(result)
            else:
                console.print("[yellow]No scan data. Run 'scan' first.[/]")

        elif cmd == "show":
            _cmd_show(result, args)

        elif cmd == "categories":
            _cmd_categories()

        elif cmd == "map":
            _cmd_map(args)

        elif cmd == "whereis":
            _cmd_whereis()

        elif cmd == "clean":
            result = _cmd_clean(config, result, args)

        elif cmd == "trash":
            _cmd_trash(config, result)

        elif cmd == "copilot":
            _cmd_copilot(config, result, args)

        else:
            console.print(f"[yellow]Unknown command: {cmd}[/]  — type [bold]help[/] for commands")

    return result, age


# ---------------------------------------------------------------------------
#  Command handlers
# ---------------------------------------------------------------------------

def _cmd_scan(config: Config, args: list[str], current: ScanResult | None) -> ScanResult:
    """Run a scan (full or by category)."""
    categories = None
    if args:
        try:
            categories = [Category(args[0])]
        except ValueError:
            console.print(f"[red]Unknown category: {args[0]}[/]")
            console.print(f"[dim]Available: {', '.join(c.value for c in Category)}[/]")
            return current or ScanResult()

    result = run_scan_with_progress(config, categories)
    save_scan(result)
    show_scan_results(result)
    _copilot_hint()
    return result


def _cmd_status(result: ScanResult | None, age: float | None):
    """Show disk overview + scan status."""
    show_disk_overview()
    if result:
        console.print(f"  Scan: [bold]{len(result.items)}[/] items, "
                      f"[bold cyan]{format_size(result.total_size)}[/] reclaimable")
        if age is not None:
            console.print(f"  Last scanned: {format_age(age)}")

        by_risk = result.by_risk()
        for risk in (RiskLevel.SAFE, RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH):
            items = by_risk.get(risk, [])
            if items:
                total = sum(i.size_bytes for i in items)
                color = RISK_COLORS[risk]
                icon = RISK_ICONS[risk]
                console.print(f"  [{color}]{icon} {risk.value}[/]: {len(items)} items, {format_size(total)}")
    else:
        console.print("  [yellow]No scan data yet. Run 'scan' to get started.[/]")


def _cmd_show(result: ScanResult | None, args: list[str]):
    """Show detailed items for a category."""
    if not result:
        console.print("[yellow]No scan data. Run 'scan' first.[/]")
        return

    if not args:
        # Show summary of all categories
        by_cat = result.by_category()
        for cat, items in sorted(by_cat.items(), key=lambda x: sum(i.size_bytes for i in x[1]), reverse=True):
            total = sum(i.size_bytes for i in items)
            console.print(f"  [bold]{format_size(total):>10}[/]  {CATEGORY_LABELS.get(cat, cat.value)} ({len(items)} items)")
        console.print(f"\n[dim]Use 'show <category>' to see details. e.g. show caches[/]")
        return

    try:
        cat = Category(args[0])
    except ValueError:
        console.print(f"[red]Unknown category: {args[0]}[/]")
        return

    by_cat = result.by_category()
    items = by_cat.get(cat, [])
    if not items:
        console.print(f"[dim]No items in {CATEGORY_LABELS.get(cat, cat.value)}[/]")
        return

    show_category_detail(items, cat)


def _cmd_categories():
    """List all categories."""
    for cat in Category:
        console.print(f"  [bold cyan]{cat.value:<20}[/] {CATEGORY_LABELS.get(cat, '')}")


def _cmd_map(args: list[str]):
    """Map disk usage for a directory."""
    disk = get_disk_usage()
    disk_total = disk["total"]

    target = HOME
    min_size = 100.0

    if args:
        target = Path(args[0]).expanduser().resolve()
        if not target.exists():
            console.print(f"[red]Path not found: {target}[/]")
            return

    if len(args) > 1:
        try:
            min_size = float(args[1])
        except ValueError:
            pass

    depth = 3 if target in (HOME, Path("/")) else 3

    console.print(f"[bold]Mapping [cyan]{target}[/cyan]...[/]\n")

    with console.status("Scanning..."):
        root = map_disk(target, depth=depth, min_size_mb=min_size)

    if not root.children:
        console.print(f"[yellow]No directories above {min_size} MB.[/]")
        console.print(f"[dim]Total: {format_size(root.size_bytes)}[/]")
        return

    console.print(f"[bold]{format_size(root.size_bytes)}[/] total  •  Showing ≥ {min_size} MB\n")
    table = _render_node_table(root, disk_total)
    console.print(table)

    # Enter drill-down
    _interactive_drill(root, disk_total, min_size)


def _cmd_whereis():
    """Full system-wide breakdown."""
    disk = get_disk_usage()
    disk_total = disk["total"]

    console.print("[bold]Scanning full system...[/]\n")

    with console.status("Scanning system directories..."):
        breakdown = get_full_disk_breakdown()

    console.print(f"[bold cyan]═══ System-Level Breakdown ═══[/]\n")

    sys_table = Table(show_lines=False, border_style="dim", padding=(0, 1))
    sys_table.add_column("Size", justify="right", min_width=10, style="bold")
    sys_table.add_column("% Disk", min_width=32)
    sys_table.add_column("Directory", min_width=20)

    accounted = 0
    for node in breakdown["system_overview"]:
        bar = _size_bar(node.size_bytes, disk_total)
        sys_table.add_row(format_size(node.size_bytes), bar, str(node.path))
        accounted += node.size_bytes

    unaccounted = disk["used"] - accounted
    if unaccounted > 1024 * 1024 * 1024:
        bar = _size_bar(unaccounted, disk_total)
        sys_table.add_row(
            f"[dim]{format_size(unaccounted)}[/]", bar,
            "[dim](APFS snapshots, VM, firmware, purgeable)[/]",
        )

    console.print(sys_table)

    home_node = breakdown["home_detail"]
    if home_node and home_node.children:
        console.print(f"\n[bold cyan]═══ Home Directory (~/) ═══[/]")
        console.print(f"Total: [bold]{format_size(home_node.size_bytes)}[/]\n")
        home_table = _render_node_table(home_node, disk_total)
        console.print(home_table)
    _copilot_hint()


def _cmd_clean(config: Config, result: ScanResult | None, args: list[str]) -> ScanResult | None:
    """Clean items from scan results."""
    if not result or not result.items:
        console.print("[yellow]No scan data. Running scan first...[/]\n")
        result = run_scan_with_progress(config)
        save_scan(result)
        show_scan_results(result)

    if not result.items:
        console.print("[green]Nothing to clean![/]")
        return result

    if args and args[0] == "safe":
        return _clean_safe(config, result)

    if args:
        try:
            cat = Category(args[0])
        except ValueError:
            console.print(f"[red]Unknown category: {args[0]}[/]")
            return result
        return _clean_category(config, result, cat)

    # Full interactive clean
    from disk_cleanup.cli import interactive_clean
    interactive_clean(result, config)
    return result


def _clean_safe(config: Config, result: ScanResult) -> ScanResult:
    """Clean only safe items."""
    safe_items = [i for i in result.items if i.risk == RiskLevel.SAFE]

    if not safe_items:
        console.print("[green]No safe items to clean![/]")
        return result

    total = sum(i.size_bytes for i in safe_items)
    console.print(f"\n[bold]Safe cleanup: {len(safe_items)} items, {format_size(total)}[/]\n")

    for item in safe_items[:10]:
        try:
            display = f"~/{item.path.relative_to(HOME)}"
        except ValueError:
            display = str(item.path)
        console.print(f"  [green]✓[/] {format_size(item.size_bytes):>10}  {display}")

    if len(safe_items) > 10:
        console.print(f"  [dim]... and {len(safe_items) - 10} more[/]")

    method = "Trash" if config.use_trash else "permanent deletion"
    console.print(f"\n[dim]Method: {method}[/]")

    if not Confirm.ask(f"\nDelete all {len(safe_items)} safe items?", default=False):
        console.print("[yellow]Cancelled.[/]")
        return result

    success, failed, freed = delete_items(safe_items, config)
    console.print(f"\n[bold green]Done![/] Freed {format_size(freed)}")
    if failed:
        console.print(f"[yellow]{failed} items failed[/]")

    return result


def _clean_category(config: Config, result: ScanResult, category: Category) -> ScanResult:
    """Clean items from a specific category."""
    by_cat = result.by_category()
    items = by_cat.get(category, [])

    if not items:
        console.print(f"[dim]No items in {CATEGORY_LABELS.get(category, category.value)}[/]")
        return result

    show_category_detail(items, category)

    safe_items = [i for i in items if i.risk in (RiskLevel.SAFE, RiskLevel.LOW)]
    safe_total = sum(i.size_bytes for i in safe_items)

    console.print(f"\n  {len(safe_items)} safe/low-risk items ({format_size(safe_total)})")

    from rich.prompt import Prompt
    choice = Prompt.ask("Action", choices=["safe", "all", "skip"], default="skip")

    to_delete = []
    if choice == "safe":
        to_delete = safe_items
    elif choice == "all":
        to_delete = [i for i in items if i.risk != RiskLevel.HIGH]

    if to_delete:
        if Confirm.ask(f"Delete {len(to_delete)} items ({format_size(sum(i.size_bytes for i in to_delete))})?", default=False):
            success, failed, freed = delete_items(to_delete, config)
            console.print(f"[bold green]Freed {format_size(freed)}[/]")

    return result


def _cmd_trash(config: Config, result: ScanResult | None):
    """Empty macOS Trash."""
    from disk_cleanup.utils import dir_size

    trash_path = HOME / ".Trash"
    if not trash_path.exists():
        console.print("[green]Trash is empty.[/]")
        return

    size = dir_size(trash_path)
    if size < 1024 * 1024:
        console.print("[green]Trash is basically empty.[/]")
        return

    console.print(f"Trash: [bold]{format_size(size)}[/]")
    if Confirm.ask("Empty Trash?", default=False):
        import subprocess
        try:
            subprocess.run(
                ["osascript", "-e", 'tell application "Finder" to empty trash'],
                capture_output=True, timeout=60, check=True,
            )
            console.print("[bold green]Trash emptied![/]")
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            console.print("[red]Failed to empty trash.[/]")


def _cmd_copilot(config: Config, result: ScanResult | None, args: list[str]):
    """Generate a Copilot Chat prompt."""
    if args and args[0] == "map":
        # Generate from disk map
        with console.status("Scanning for disk map..."):
            breakdown = get_full_disk_breakdown()
        disk = get_disk_usage()

        from disk_cleanup.cli import _generate_map_copilot_prompt
        prompt = _generate_map_copilot_prompt(breakdown, disk)
        console.print(Panel(prompt, title="Copy this into GitHub Copilot Chat", border_style="cyan"))
        return

    if not result or not result.items:
        console.print("[yellow]No scan data. Running scan first...[/]\n")
        result = run_scan_with_progress(config)
        save_scan(result)

    analysis = generate_analysis(result, config)
    prompt = generate_copilot_prompt(analysis)
    console.print(Panel(prompt, title="Copy this into GitHub Copilot Chat", border_style="cyan"))

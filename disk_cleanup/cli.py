"""Interactive CLI interface using Rich."""

import json
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

from disk_cleanup.ai_advisor import (
    format_report_text,
    generate_analysis,
    generate_copilot_prompt,
)
from disk_cleanup.cleaner import delete_items
from disk_cleanup.config import Config, HOME
from disk_cleanup.disk_map import DirNode, drill_into, get_full_disk_breakdown, map_disk
from disk_cleanup.scanner import run_scan
from disk_cleanup.scanners import CATEGORY_LABELS, Category, CleanupItem, RiskLevel, ScanResult
from disk_cleanup.utils import format_size, get_disk_usage

console = Console()

RISK_COLORS = {
    RiskLevel.SAFE: "green",
    RiskLevel.LOW: "yellow",
    RiskLevel.MEDIUM: "dark_orange",
    RiskLevel.HIGH: "red",
}

RISK_ICONS = {
    RiskLevel.SAFE: "✓",
    RiskLevel.LOW: "○",
    RiskLevel.MEDIUM: "△",
    RiskLevel.HIGH: "✗",
}


def show_banner():
    """Display the app banner."""
    console.print()
    console.print(Panel.fit(
        "[bold cyan]storage[/]\n"
        "[dim]Disk management for macOS[/]",
        border_style="cyan",
    ))
    console.print()


def show_disk_overview():
    """Show current disk usage."""
    disk = get_disk_usage()
    pct = disk["percent_used"]

    if pct > 90:
        color = "red"
        status = "CRITICAL"
    elif pct > 80:
        color = "dark_orange"
        status = "WARNING"
    elif pct > 60:
        color = "yellow"
        status = "OK"
    else:
        color = "green"
        status = "HEALTHY"

    bar_width = 40
    filled = int(bar_width * pct / 100)
    bar = f"[{color}]{'█' * filled}[/][dim]{'░' * (bar_width - filled)}[/]"

    console.print(f"  Disk: {bar} {pct:.1f}% [{color}]{status}[/]")
    console.print(f"  Used: {format_size(disk['used'])}  |  Free: {format_size(disk['free'])}  |  Total: {format_size(disk['total'])}")
    console.print()


def run_scan_with_progress(config: Config, categories: list[Category] | None = None) -> ScanResult:
    """Run scan with a nice progress indicator."""
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Scanning...", total=None)

        def on_progress(label: str):
            progress.update(task, description=f"Scanning {label}...")

        result = run_scan(config, categories, progress_callback=on_progress)
        progress.update(task, description="Scan complete!")

    return result


def show_scan_results(result: ScanResult):
    """Display scan results in a rich table."""
    if not result.items:
        console.print("[green]Nothing to clean! Your disk is tidy.[/]")
        return

    console.print(f"\n  Found [bold]{len(result.items)}[/] items totaling "
                  f"[bold cyan]{format_size(result.total_size)}[/] "
                  f"in {result.scan_time_seconds:.1f}s\n")

    # Summary table by category
    by_cat = result.by_category()
    table = Table(title="Cleanup Summary by Category", show_lines=True, border_style="dim")
    table.add_column("#", style="dim", width=3)
    table.add_column("Category", min_width=25)
    table.add_column("Items", justify="right", width=6)
    table.add_column("Size", justify="right", min_width=10)
    table.add_column("Risk", min_width=15)

    for i, (cat, items) in enumerate(
        sorted(by_cat.items(), key=lambda x: sum(i.size_bytes for i in x[1]), reverse=True),
        1,
    ):
        total = sum(item.size_bytes for item in items)
        risk_counts = {}
        for item in items:
            risk_counts[item.risk] = risk_counts.get(item.risk, 0) + 1

        risk_str = "  ".join(
            f"[{RISK_COLORS[r]}]{RISK_ICONS[r]} {c}[/]"
            for r, c in sorted(risk_counts.items(), key=lambda x: x[0].value)
        )

        table.add_row(
            str(i),
            CATEGORY_LABELS.get(cat, cat.value),
            str(len(items)),
            format_size(total),
            risk_str,
        )

    console.print(table)

    # Risk legend
    console.print()
    console.print("  [green]✓ safe[/] — always regenerated  "
                  "[yellow]○ low[/] — probably unneeded  "
                  "[dark_orange]△ medium[/] — review first  "
                  "[red]✗ high[/] — do not auto-delete")


def show_category_detail(items: list[CleanupItem], category: Category):
    """Show detailed item list for a category."""
    label = CATEGORY_LABELS.get(category, category.value)
    console.print(f"\n[bold]{label}[/]")
    console.print("-" * 60)

    for item in sorted(items, key=lambda x: x.size_bytes, reverse=True):
        risk_color = RISK_COLORS[item.risk]
        icon = RISK_ICONS[item.risk]

        # Shorten path for display
        try:
            display_path = f"~/{item.path.relative_to(HOME)}"
        except ValueError:
            display_path = str(item.path)

        console.print(
            f"  [{risk_color}]{icon}[/]  "
            f"[bold]{format_size(item.size_bytes):>10}[/]  "
            f"{display_path}"
        )
        console.print(f"              [dim]{item.reason}[/]")


def interactive_clean(result: ScanResult, config: Config):
    """Interactive cleanup flow — user selects what to delete."""
    if not result.items:
        return

    by_cat = result.by_category()
    categories = sorted(
        by_cat.items(),
        key=lambda x: sum(i.size_bytes for i in x[1]),
        reverse=True,
    )

    console.print("\n[bold]Interactive Cleanup[/]")
    console.print("Review each category and choose what to clean.\n")

    items_to_delete: list[CleanupItem] = []

    for cat, items in categories:
        label = CATEGORY_LABELS.get(cat, cat.value)
        total = sum(i.size_bytes for i in items)
        safe_items = [i for i in items if i.risk in (RiskLevel.SAFE, RiskLevel.LOW)]
        safe_total = sum(i.size_bytes for i in safe_items)

        console.print(f"\n[bold cyan]── {label} ──[/]")
        console.print(f"   {len(items)} items, {format_size(total)} total "
                      f"({format_size(safe_total)} safe/low risk)")

        # Show top items
        for item in sorted(items, key=lambda x: x.size_bytes, reverse=True)[:5]:
            risk_color = RISK_COLORS[item.risk]
            icon = RISK_ICONS[item.risk]
            try:
                display_path = f"~/{item.path.relative_to(HOME)}"
            except ValueError:
                display_path = str(item.path)
            console.print(f"   [{risk_color}]{icon}[/] {format_size(item.size_bytes):>10}  {display_path}")

        if len(items) > 5:
            console.print(f"   [dim]... and {len(items) - 5} more[/]")

        # Ask what to do
        console.print()
        choice = Prompt.ask(
            "   Action",
            choices=["safe", "all", "skip", "detail"],
            default="skip",
        )

        if choice == "safe":
            items_to_delete.extend(safe_items)
            console.print(f"   [green]Queued {len(safe_items)} safe items ({format_size(safe_total)})[/]")
        elif choice == "all":
            # Exclude high-risk items
            non_high = [i for i in items if i.risk != RiskLevel.HIGH]
            excluded = len(items) - len(non_high)
            items_to_delete.extend(non_high)
            msg = f"   [green]Queued {len(non_high)} items ({format_size(sum(i.size_bytes for i in non_high))})[/]"
            if excluded:
                msg += f" [red]({excluded} high-risk items excluded)[/]"
            console.print(msg)
        elif choice == "detail":
            show_category_detail(items, cat)
            # Ask again after showing detail
            choice2 = Prompt.ask(
                "   Action",
                choices=["safe", "all", "skip"],
                default="skip",
            )
            if choice2 == "safe":
                items_to_delete.extend(safe_items)
                console.print(f"   [green]Queued {len(safe_items)} safe items[/]")
            elif choice2 == "all":
                non_high = [i for i in items if i.risk != RiskLevel.HIGH]
                items_to_delete.extend(non_high)
                console.print(f"   [green]Queued {len(non_high)} items[/]")

    # Confirm and execute
    if not items_to_delete:
        console.print("\n[yellow]Nothing selected for cleanup.[/]")
        return

    total_size = sum(i.size_bytes for i in items_to_delete)
    console.print(f"\n[bold]Ready to clean {len(items_to_delete)} items "
                  f"({format_size(total_size)})[/]")

    method = "Trash" if config.use_trash else "permanent deletion"
    console.print(f"[dim]Method: {method}[/]")

    if not Confirm.ask("\nProceed with cleanup?", default=False):
        console.print("[yellow]Cancelled.[/]")
        return

    # Execute deletions
    console.print()
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Cleaning...", total=len(items_to_delete))

        def on_delete_progress(current, total, success, msg):
            status = "[green]✓[/]" if success else "[red]✗[/]"
            progress.update(task, advance=1, description=f"{status} {msg[:60]}")

        success, failed, freed = delete_items(
            items_to_delete, config, progress_callback=on_delete_progress,
        )

    console.print(f"\n[bold green]Done![/] "
                  f"Cleaned {success} items, freed {format_size(freed)}")
    if failed:
        console.print(f"[yellow]{failed} items could not be deleted[/]")


def cmd_scan(args):
    """Handle the 'scan' command."""
    config = Config.load()

    categories = None
    if args.category:
        try:
            categories = [Category(args.category)]
        except ValueError:
            console.print(f"[red]Unknown category: {args.category}[/]")
            console.print(f"Available: {', '.join(c.value for c in Category)}")
            return

    result = run_scan_with_progress(config, categories)
    show_scan_results(result)


def cmd_clean(args):
    """Handle the 'clean' command."""
    config = Config.load()
    if args.permanent:
        config.use_trash = False

    categories = None
    if args.category:
        try:
            categories = [Category(args.category)]
        except ValueError:
            console.print(f"[red]Unknown category: {args.category}[/]")
            return

    result = run_scan_with_progress(config, categories)
    show_scan_results(result)
    interactive_clean(result, config)


def cmd_analyze(args):
    """Handle the 'analyze' command."""
    config = Config.load()

    console.print("[bold]Running full scan for AI analysis...[/]\n")
    result = run_scan_with_progress(config)

    analysis = generate_analysis(result, config)

    if args.json:
        # Output raw JSON
        print(json.dumps(analysis, indent=2))
    elif args.copilot:
        # Output a prompt for Copilot Chat
        prompt = generate_copilot_prompt(analysis)
        console.print(Panel(prompt, title="Copy this into GitHub Copilot Chat", border_style="cyan"))
    else:
        # Pretty text report
        report = format_report_text(analysis)
        console.print(report)

        # Also offer Copilot prompt
        console.print()
        if Confirm.ask("Generate a prompt for GitHub Copilot Chat?", default=True):
            prompt = generate_copilot_prompt(analysis)
            console.print(Panel(prompt, title="Copy this into GitHub Copilot Chat", border_style="cyan"))

    # Save report
    report_path = Path("disk_cleanup_report.json")
    with open(report_path, "w") as f:
        json.dump(analysis, f, indent=2)
    console.print(f"\n[dim]Full report saved to {report_path}[/]")


def cmd_overview(args):
    """Handle the 'overview' command."""
    show_disk_overview()


def cmd_quick(args):
    """Handle the 'quick' command — auto-clean only safe items."""
    config = Config.load()
    if args.permanent:
        config.use_trash = False

    result = run_scan_with_progress(config)

    # Filter to safe items only
    safe_items = [i for i in result.items if i.risk == RiskLevel.SAFE]

    if not safe_items:
        console.print("[green]No safe items to clean![/]")
        return

    total = sum(i.size_bytes for i in safe_items)
    console.print(f"\n[bold]Quick Clean: {len(safe_items)} safe items, {format_size(total)}[/]\n")

    # Show what will be cleaned
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

    if not Confirm.ask(f"\nDelete all {len(safe_items)} safe items?", default=False):
        console.print("[yellow]Cancelled.[/]")
        return

    success, failed, freed = delete_items(safe_items, config)
    console.print(f"\n[bold green]Done![/] Freed {format_size(freed)}")
    if failed:
        console.print(f"[yellow]{failed} items failed[/]")


# ---------------------------------------------------------------------------
#   DISK MAP — comprehensive disk usage explorer
# ---------------------------------------------------------------------------

def _size_bar(size_bytes: int, total_bytes: int, width: int = 25) -> str:
    """Render a proportional size bar."""
    if total_bytes <= 0:
        return ""
    ratio = min(size_bytes / total_bytes, 1.0)
    filled = int(width * ratio)
    pct = ratio * 100

    if pct > 50:
        color = "red"
    elif pct > 25:
        color = "dark_orange"
    elif pct > 10:
        color = "yellow"
    else:
        color = "cyan"

    bar = f"[{color}]{'█' * filled}[/][dim]{'░' * (width - filled)}[/]"
    return f"{bar} {pct:>5.1f}%"


def _render_node_table(node: DirNode, disk_total: int, show_index: bool = True) -> Table:
    """Render a DirNode's children as a table."""
    table = Table(show_lines=False, border_style="dim", padding=(0, 1))
    if show_index:
        table.add_column("#", style="bold cyan", width=4, justify="right")
    table.add_column("Size", justify="right", min_width=10, style="bold")
    table.add_column("% Disk", min_width=32)
    table.add_column("Directory", min_width=30)

    accounted = 0
    for i, child in enumerate(node.children, 1):
        try:
            display = f"~/{child.path.relative_to(HOME)}"
        except ValueError:
            display = str(child.path)

        bar = _size_bar(child.size_bytes, disk_total)
        row = []
        if show_index:
            row.append(str(i))
        row.extend([format_size(child.size_bytes), bar, display])
        table.add_row(*row)
        accounted += child.size_bytes

    # Show unaccounted space (files smaller than threshold, permissions, etc.)
    unaccounted = node.size_bytes - accounted
    if unaccounted > 100 * 1024 * 1024:  # > 100 MB
        bar = _size_bar(unaccounted, disk_total)
        row = []
        if show_index:
            row.append("[dim]·[/]")
        row.extend([
            f"[dim]{format_size(unaccounted)}[/]",
            bar,
            "[dim](other small files/dirs below threshold)[/]",
        ])
        table.add_row(*row)

    return table


def cmd_map(args):
    """Handle the 'map' command — full disk usage explorer."""

    disk = get_disk_usage()
    disk_total = disk["total"]

    target = Path(args.path).expanduser().resolve() if args.path else HOME
    min_size = args.min_size

    console.print(f"[bold]Mapping disk usage from [cyan]{target}[/cyan]...[/]\n")
    console.print("[dim]This scans actual disk usage — may take a minute for large directories.[/]\n")

    # Determine depth based on target
    if target == Path("/"):
        depth = 2
    elif target == HOME:
        depth = 3
    else:
        depth = 3

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task(f"Scanning {target}...", total=None)
        root = map_disk(target, depth=depth, min_size_mb=min_size)
        progress.update(task, description="Scan complete!")

    if not root.children:
        console.print("[yellow]No directories found above the size threshold.[/]")
        console.print(f"[dim]Total size: {format_size(root.size_bytes)}. Try --min-size with a smaller value.[/]")
        return

    # Show the results
    console.print(f"\n[bold]Disk Usage Map: [cyan]{target}[/cyan][/]")
    console.print(f"Total: [bold]{format_size(root.size_bytes)}[/]  |  "
                  f"Showing directories ≥ {min_size} MB\n")

    table = _render_node_table(root, disk_total)
    console.print(table)

    # Interactive drill-down
    if not args.no_interactive:
        _interactive_drill(root, disk_total, min_size)


def cmd_whereis(args):
    """Handle the 'whereis' command — system-wide overview of where space lives."""

    disk = get_disk_usage()
    disk_total = disk["total"]

    console.print("[bold]Full System Disk Breakdown[/]")
    console.print("[dim]Scanning all major system directories...[/]\n")

    from disk_cleanup.disk_map import get_full_disk_breakdown

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Scanning system...", total=None)

        def on_progress(label):
            progress.update(task, description=f"Scanning {label}...")

        breakdown = get_full_disk_breakdown(progress_callback=on_progress)
        progress.update(task, description="Scan complete!")

    # 1. System-level overview
    console.print(f"\n[bold cyan]═══ System-Level Breakdown ═══[/]\n")

    sys_table = Table(show_lines=False, border_style="dim", padding=(0, 1))
    sys_table.add_column("Size", justify="right", min_width=10, style="bold")
    sys_table.add_column("% Disk", min_width=32)
    sys_table.add_column("Directory", min_width=20)

    accounted = 0
    for node in breakdown["system_overview"]:
        bar = _size_bar(node.size_bytes, disk_total)
        sys_table.add_row(format_size(node.size_bytes), bar, str(node.path))
        accounted += node.size_bytes

    # Show unaccounted (firmware, VM, APFS snapshots, etc.)
    unaccounted = disk["used"] - accounted
    if unaccounted > 1024 * 1024 * 1024:
        bar = _size_bar(unaccounted, disk_total)
        sys_table.add_row(
            f"[dim]{format_size(unaccounted)}[/]", bar,
            "[dim](APFS snapshots, VM, firmware, purgeable)[/]",
        )

    console.print(sys_table)

    # 2. Home directory detail
    home_node = breakdown["home_detail"]
    if home_node and home_node.children:
        console.print(f"\n[bold cyan]═══ Home Directory Detail (~/) ═══[/]")
        console.print(f"Total: [bold]{format_size(home_node.size_bytes)}[/]\n")

        home_table = _render_node_table(home_node, disk_total)
        console.print(home_table)

    # 3. Offer interactive exploration
    console.print()
    if Confirm.ask("Explore a directory interactively?", default=True):
        path_input = Prompt.ask("Enter path to explore", default=str(HOME))
        target = Path(path_input).expanduser().resolve()
        if target.exists():
            node = drill_into(target, min_size_mb=50)
            if node.children:
                console.print(f"\n[bold]{target}[/] — {format_size(node.size_bytes)}\n")
                table = _render_node_table(node, disk_total)
                console.print(table)
                _interactive_drill(node, disk_total, 50)
            else:
                console.print(f"[yellow]Nothing above 50 MB in {target}[/]")
        else:
            console.print(f"[red]Path not found: {target}[/]")

    # 4. Generate Copilot prompt
    console.print()
    if Confirm.ask("Generate a Copilot Chat prompt with this disk breakdown?", default=True):
        prompt = _generate_map_copilot_prompt(breakdown, disk)
        console.print(Panel(prompt, title="Copy this into GitHub Copilot Chat", border_style="cyan"))


def _interactive_drill(node: DirNode, disk_total: int, min_size_mb: float):
    """Interactive drill-down into subdirectories."""
    current = node

    while True:
        console.print()
        console.print("[dim]Enter a number to drill into that directory, 'up' to go back, or 'q' to quit.[/]")
        choice = Prompt.ask("Drill into", default="q")

        if choice.lower() in ("q", "quit", "exit"):
            break

        if choice.lower() in ("up", "u", "back", "b", ".."):
            parent = current.path.parent
            if parent != current.path:
                with console.status(f"Scanning {parent}..."):
                    current = drill_into(parent, min_size_mb=min_size_mb)
                console.print(f"\n[bold]{current.display_path}[/] — {format_size(current.size_bytes)}\n")
                table = _render_node_table(current, disk_total)
                console.print(table)
            continue

        # Try as a number index
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(current.children):
                target = current.children[idx]
                if not target.path.is_dir():
                    console.print("[yellow]That's not a directory — can't drill deeper.[/]")
                    continue

                with console.status(f"Scanning {target.display_path}..."):
                    current = drill_into(target.path, min_size_mb=min_size_mb)

                if current.children:
                    console.print(f"\n[bold]{current.display_path}[/] — {format_size(current.size_bytes)}\n")
                    table = _render_node_table(current, disk_total)
                    console.print(table)
                else:
                    console.print(f"\n[bold]{current.display_path}[/] — {format_size(current.size_bytes)}")
                    console.print(f"[dim]No subdirectories above {min_size_mb} MB. This is the leaf.[/]")
                    # Show actual files
                    _show_files_in_dir(target.path)
            else:
                console.print(f"[red]Invalid number. Enter 1-{len(current.children)}.[/]")
        except ValueError:
            # Try as a path
            target = Path(choice).expanduser().resolve()
            if target.exists() and target.is_dir():
                with console.status(f"Scanning {target}..."):
                    current = drill_into(target, min_size_mb=min_size_mb)
                if current.children:
                    console.print(f"\n[bold]{current.display_path}[/] — {format_size(current.size_bytes)}\n")
                    table = _render_node_table(current, disk_total)
                    console.print(table)
                else:
                    console.print(f"[yellow]No subdirectories above {min_size_mb} MB in {target}[/]")
                    _show_files_in_dir(target)
            else:
                console.print(f"[red]Not a valid number or path: {choice}[/]")


def _show_files_in_dir(directory: Path, limit: int = 15):
    """Show the largest files in a directory."""
    files = []
    try:
        for entry in directory.iterdir():
            if entry.is_file() and not entry.is_symlink():
                try:
                    files.append((entry, entry.stat().st_size))
                except OSError:
                    pass
    except PermissionError:
        console.print("[dim]Permission denied[/]")
        return

    if not files:
        return

    files.sort(key=lambda x: x[1], reverse=True)

    console.print(f"\n  [dim]Largest files in this directory:[/]")
    for f, size in files[:limit]:
        console.print(f"  {format_size(size):>10}  {f.name}")
    if len(files) > limit:
        console.print(f"  [dim]... and {len(files) - limit} more files[/]")


def _generate_map_copilot_prompt(breakdown: dict, disk: dict) -> str:
    """Generate a Copilot prompt from the full disk map."""
    pct = f"{disk['percent_used']:.1f}%"
    free = format_size(disk['free'])
    total = format_size(disk['total'])

    prompt = f"""My Mac disk is {pct} full ({free} free of {total}). I need help figuring out what to clean up.

Here's where all my disk space is going:

**System-Level Breakdown:**
"""
    for node in breakdown["system_overview"]:
        ratio = node.size_bytes / disk["total"] * 100
        prompt += f"- {format_size(node.size_bytes)} ({ratio:.1f}%) — {node.path}\n"

    home = breakdown.get("home_detail")
    if home and home.children:
        prompt += f"\n**Home Directory Breakdown ({format_size(home.size_bytes)} total):**\n"
        for child in home.children[:20]:
            ratio = child.size_bytes / disk["total"] * 100
            prompt += f"- {format_size(child.size_bytes)} ({ratio:.1f}%) — {child.display_path}\n"

    prompt += """
I mainly use my Mac for coding and some PDFs. I don't game. Please:
1. Identify what's using unexpectedly large amounts of space
2. Tell me what's safe to delete vs what I should keep
3. Suggest specific commands or steps to free up the most space
4. Flag anything that looks like it could be OS bloat, old iOS backups, or app data I don't need
5. Identify if there are APFS snapshots or Time Machine local snapshots consuming space
"""
    return prompt

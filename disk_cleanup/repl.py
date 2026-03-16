"""Custom REPL — branded storage interface powered by Copilot CLI."""

import json
import os
import readline
import shutil
import subprocess
import sys
import threading
from argparse import Namespace
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from disk_cleanup.utils import format_size, get_disk_usage

console = Console()

STORAGE_DIR = Path.home() / ".storage"
PREFS_FILE = STORAGE_DIR / "prefs.json"

AVAILABLE_MODELS = [
    "claude-sonnet-4.6",
    "claude-sonnet-4.5",
    "claude-haiku-4.5",
    "claude-opus-4.6",
    "claude-opus-4.5",
    "gpt-5.4",
    "gpt-5.2",
    "gpt-4.1",
    "gemini-3-pro-preview",
]

DEFAULT_MODEL = "claude-sonnet-4.6"

# ── Preferences ──────────────────────────────────────────────────────────────

def _load_prefs() -> dict:
    if PREFS_FILE.exists():
        try:
            return json.loads(PREFS_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_prefs(prefs: dict) -> None:
    STORAGE_DIR.mkdir(exist_ok=True)
    PREFS_FILE.write_text(json.dumps(prefs, indent=2))


def _get_model(prefs: dict) -> str:
    return prefs.get("model", DEFAULT_MODEL)


# ── Banner ───────────────────────────────────────────────────────────────────

def _show_banner(model: str) -> None:
    disk = get_disk_usage()
    pct = disk["percent_used"]
    if pct > 90:
        color = "red"
    elif pct > 80:
        color = "dark_orange"
    else:
        color = "green"

    # Bar spans full panel interior: cols minus border(2) + padding(2*2) + " XX%" label
    try:
        cols = os.get_terminal_size().columns
    except OSError:
        cols = 80
    bar_w = max(10, cols - 12)
    filled = int(bar_w * pct / 100)
    bar = f"[{color}]{'█' * filled}[/][dim]{'░' * (bar_w - filled)}[/]"

    console.print()
    console.print(Panel(
        Text.from_markup(
            f"[bold cyan]Storage[/]  [dim]disk management for macOS[/]\n\n"
            f"{bar} [bold]{pct:.0f}%[/]\n"
            f"[bold]{format_size(disk['free'])}[/] free of {format_size(disk['total'])}\n\n"
            f"Model: [bold]{model}[/]  [dim]·[/]  [dim][bold]help[/bold] for commands[/]"
        ),
        border_style="cyan",
        padding=(1, 2),
        expand=True,
    ))
    console.print()


# ── Model picker ─────────────────────────────────────────────────────────────

def _pick_model(current: str) -> str:
    console.print()
    table = Table(title="Available Models", border_style="cyan", show_lines=False)
    table.add_column("#", style="bold cyan", width=3)
    table.add_column("Model", style="bold")
    table.add_column("", style="dim")

    for i, m in enumerate(AVAILABLE_MODELS, 1):
        marker = "← current" if m == current else ""
        table.add_row(str(i), m, marker)

    console.print(table)
    console.print()

    while True:
        try:
            choice = console.input("[cyan]Pick a model (number or name):[/] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            return current

        if not choice:
            return current

        # By number
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(AVAILABLE_MODELS):
                return AVAILABLE_MODELS[idx]
            console.print(f"[red]Pick 1–{len(AVAILABLE_MODELS)}[/]")
            continue

        # By name (partial match)
        matches = [m for m in AVAILABLE_MODELS if choice.lower() in m.lower()]
        if len(matches) == 1:
            return matches[0]
        elif len(matches) > 1:
            console.print(f"[yellow]Ambiguous — matches: {', '.join(matches)}[/]")
        else:
            console.print(f"[red]Unknown model. Pick a number or name from the list.[/]")


# ── Help ─────────────────────────────────────────────────────────────────────

def _show_help() -> None:
    console.print()
    help_table = Table(show_header=False, box=None, padding=(0, 2), expand=True)
    help_table.add_column("cmd", style="bold cyan", no_wrap=True, ratio=1)
    help_table.add_column("desc", style="dim", ratio=3)
    help_table.add_row("overview", "Disk usage bar")
    help_table.add_row("summary", "Category & risk breakdown")
    help_table.add_row("scan", "Run a fresh disk scan")
    help_table.add_row("map [path]", "Directory treemap")
    help_table.add_row("whereis", "Full system breakdown")
    help_table.add_row("query ...", "Filter scan data (--category, --risk, etc.)")
    help_table.add_row("info <path>", "Inspect a specific path")
    help_table.add_row("rm <path>", "Remove (dry-run default, --confirm to execute)")
    help_table.add_row("empty-trash", "Empty macOS Trash")
    help_table.add_row("model", "Change AI model")
    help_table.add_row("exit", "Quit")

    console.print(Panel(
        help_table,
        title="[bold cyan]Storage[/] Help",
        subtitle="[dim]Or just ask a question in plain English[/]",
        border_style="dim",
        padding=(1, 1),
        expand=True,
    ))
    console.print()


# ── Copilot call ─────────────────────────────────────────────────────────────

def _find_copilot() -> str:
    path = shutil.which("copilot")
    if not path:
        console.print("[red]Error:[/] GitHub Copilot CLI not found on PATH.")
        console.print("[dim]Install: https://docs.github.com/copilot[/]")
        sys.exit(1)
    return path


def _load_instructions(project_root: str) -> str:
    """Load system prompt from the package."""
    prompt_path = Path(__file__).parent / "system_prompt.md"
    try:
        return prompt_path.read_text()
    except OSError:
        return ""


def _render_agent_text(text: str) -> None:
    """Render agent output through Rich Markdown for beautiful formatting."""
    from rich.markdown import Markdown
    from rich.padding import Padding
    md = Markdown(text)
    console.print(Padding(md, (0, 2)))


class _Spinner:
    """Animated dot spinner: thinking... → thinking...... then resets."""

    _FRAMES = ["...", "....", ".....", "......"]
    _INTERVAL = 0.6

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._stop.clear()
        sys.stdout.write("\033[?25l")  # hide cursor
        sys.stdout.flush()
        self._thread = threading.Thread(target=self._animate, daemon=True)
        self._thread.start()

    def _animate(self) -> None:
        idx = 0
        while not self._stop.is_set():
            frame = self._FRAMES[idx % len(self._FRAMES)]
            label = f"\r\033[2K\033[90mThinking{frame}\033[0m"
            sys.stdout.write(label)
            sys.stdout.flush()
            idx += 1
            self._stop.wait(self._INTERVAL)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)
        # Clear spinner line and show cursor
        sys.stdout.write("\r\033[2K\033[?25h")
        sys.stdout.flush()


_NO_YOLO_GUARD = """
CRITICAL SAFETY RULE — you are in safe mode (--no-yolo):
- NEVER pass --confirm or --permanent to any storage command without asking the user first.
- You may scan, query, info, overview, map, whereis, and dry-run rm freely — no permission needed.
- For deletions: dry-run first, show what would be deleted, then ASK "Do you want me to go ahead?"
- Only if the user confirms (yes/y/go ahead/do it), THEN run the command with --confirm.
- If the user says no, stop. Do not ask again.
"""


def _run_copilot(copilot_bin: str, model: str, prompt: str, project_root: str,
                 instructions: str = "", *, yolo: bool = True) -> None:
    """Run Copilot CLI with live-streamed output to the terminal.

    Uses --output-format json to parse events and render:
    - Animated spinner while thinking
    - Minimal tool call indicators (● running... → ✓ done)
    - Streamed assistant text in near-white
    """
    preamble = instructions
    if not yolo:
        preamble = f"{preamble}\n{_NO_YOLO_GUARD}" if preamble else _NO_YOLO_GUARD

    full_prompt = f"{preamble}\n\nUser request: {prompt}" if preamble else prompt

    cmd = [
        copilot_bin,
        "--model", model,
        "--no-custom-instructions",
        "--allow-all",
        "--autopilot",
        "--available-tools=bash,task_complete",
        "--output-format", "json",
        "-p", full_prompt,
    ]

    # Near-white for assistant text, dim gray for tool indicators
    _CLR = "\033[38;5;253m"
    _DIM = "\033[90m"
    _GRN = "\033[32m"
    _RST = "\033[0m"

    spinner = _Spinner()
    spinner.start()
    spinner_active = True
    in_text = False  # True once we've started printing assistant text
    text_buf: list[str] = []  # accumulate assistant text for Rich rendering
    active_tools: dict[str, str] = {}  # toolCallId -> display label

    def _tool_label(name: str, args: dict) -> str:
        """Short human-friendly label for a bash tool call."""
        if name != "bash":
            return ""  # only show bash (shell) commands
        # Prefer the description (always clean); fall back to first storage command
        desc = args.get("description", "")
        if desc:
            return desc
        cmd_str = args.get("command", "")
        for part in cmd_str.split("&&"):
            part = part.strip()
            if part.startswith("storage"):
                return part
        return cmd_str[:50] or "running"

    def _stop_spinner():
        nonlocal spinner_active
        if spinner_active:
            spinner.stop()
            sys.stdout.write("\033[?25l")  # keep cursor hidden
            sys.stdout.flush()
            spinner_active = False

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=project_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

        try:
            for raw_line in proc.stdout:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue

                try:
                    evt = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue

                etype = evt.get("type", "")
                data = evt.get("data", {})

                # ── Tool started ─────────────────────────────────
                if etype == "tool.execution_start":
                    label = _tool_label(data.get("toolName", ""), data.get("arguments", {}))
                    if label:
                        _stop_spinner()
                        tid = data.get("toolCallId", "")
                        active_tools[tid] = label
                        sys.stdout.write(f"\r\033[2K  {_DIM}● {label}…{_RST}")
                        sys.stdout.flush()

                # ── Tool finished ────────────────────────────────
                elif etype == "tool.execution_complete":
                    tid = data.get("toolCallId", "")
                    label = active_tools.pop(tid, "")
                    if label:
                        ok = data.get("success", True)
                        mark = f"{_GRN}✓{_RST}" if ok else f"\033[31m✗{_RST}"
                        sys.stdout.write(f"\r\033[2K  {mark} {_DIM}{label}{_RST}\n")
                        sys.stdout.flush()

                # ── Assistant text (accumulate for Rich rendering) ─
                elif etype == "assistant.message_delta":
                    _stop_spinner()
                    delta = data.get("deltaContent", "")
                    if delta:
                        if not in_text:
                            in_text = True
                        text_buf.append(delta)

                # Skip everything else (reasoning, session, user echo, etc.)

        except KeyboardInterrupt:
            proc.kill()

        proc.wait(timeout=600)

        # Render accumulated assistant text through Rich Markdown
        sys.stdout.write("\033[?25h")  # show cursor
        sys.stdout.flush()
        full_text = "".join(text_buf).strip()
        if full_text:
            console.print()
            _render_agent_text(full_text)
        console.print()

    except subprocess.TimeoutExpired:
        proc.kill()
        if spinner_active:
            spinner.stop()
        sys.stdout.write(f"{_RST}\033[?25h")
        sys.stdout.flush()
        console.print("\n[red]Request timed out.[/]\n")
    except KeyboardInterrupt:
        if spinner_active:
            spinner.stop()
        sys.stdout.write(f"{_RST}\033[?25h")
        sys.stdout.flush()
        console.print()
    except OSError as e:
        if spinner_active:
            spinner.stop()
        sys.stdout.write(f"{_RST}\033[?25h")
        sys.stdout.flush()
        console.print(f"\n[red]Failed to run Copilot:[/] {e}\n")


# ── Visual commands (run natively for Rich output) ──────────────────────────

def _show_freshness() -> None:
    """Display a one-line freshness indicator under query/summary results."""
    from disk_cleanup.cache import scan_freshness
    info = scan_freshness()
    if not info["has_cache"]:
        console.print("  [yellow]⚠ No scan data — run [bold]scan[/bold] for accurate results[/]")
        return
    stale_n = info["stale_count"]
    age = info["age_display"]
    if stale_n == 0:
        console.print(f"  [dim]\u2713 Scanned {age}[/]")
    else:
        total = info["total_categories"]
        console.print(
            f"  [yellow]\u26a0 {stale_n}/{total} categories stale ({age})[/]  "
            f"[dim]run [bold]scan[/bold] to refresh[/]"
        )


def _run_overview() -> None:
    from disk_cleanup.cli import show_disk_overview
    console.print()
    show_disk_overview()

def _run_query_rich(category: str | None = None, risk: str | None = None,
                    limit: int = 50, larger_than_mb: float | None = None,
                    older_than_days: float | None = None,
                    path_contains: str | None = None) -> None:
    """Run a query and render results as a Rich table."""
    from disk_cleanup.query import query_scan, load_scan, _resolve_category, _file_age_days
    from disk_cleanup.scanners import CATEGORY_LABELS, RiskLevel
    from pathlib import Path

    result, _age = load_scan()
    if result is None:
        console.print("[yellow]No scan data. Run [bold]scan[/bold] first.[/]")
        console.print()
        return

    items = list(result.items)

    # Apply filters
    if category:
        cat_match = _resolve_category(category)
        if cat_match is None:
            console.print(f"[red]Unknown category: {category}[/]")
            return
        items = [i for i in items if i.category == cat_match]
        cat_label = CATEGORY_LABELS.get(cat_match, cat_match.value)
    else:
        cat_label = "All Categories"

    if risk:
        from disk_cleanup.query import _resolve_risk
        risk_match = _resolve_risk(risk)
        if risk_match:
            items = [i for i in items if i.risk == risk_match]

    if larger_than_mb is not None:
        threshold = int(larger_than_mb * 1024 * 1024)
        items = [i for i in items if i.size_bytes >= threshold]

    if older_than_days is not None:
        items = [i for i in items if _file_age_days(i.path) > older_than_days]

    if path_contains:
        needle = path_contains.lower()
        items = [i for i in items if needle in str(i.path).lower()]

    items.sort(key=lambda x: x.size_bytes, reverse=True)
    total_count = len(items)
    items = items[:limit]

    if not items:
        console.print(f"\n  [yellow]No items match the query.[/]\n")
        return

    total_size = sum(i.size_bytes for i in items)
    risk_colors = {"safe": "green", "low": "yellow", "medium": "dark_orange", "high": "red"}
    risk_icons = {"safe": "✓", "low": "○", "medium": "△", "high": "✗"}

    table = Table(
        title=f"{cat_label} — {total_count} items, {format_size(total_size)}",
        border_style="dim", show_lines=False, padding=(0, 1),
    )
    table.add_column("Size", justify="right", width=10, style="bold", no_wrap=True)
    table.add_column("Risk", width=10, no_wrap=True)
    table.add_column("Path", no_wrap=True, overflow="ellipsis", max_width=60)
    table.add_column("Reason", style="dim", no_wrap=True, overflow="ellipsis", max_width=40)

    home = Path.home()
    for item in items:
        try:
            display_path = f"~/{item.path.relative_to(home)}"
        except ValueError:
            display_path = str(item.path)
        c = risk_colors.get(item.risk.value, "white")
        icon = risk_icons.get(item.risk.value, "?")
        table.add_row(
            format_size(item.size_bytes),
            f"[{c}]{icon} {item.risk.value}[/]",
            display_path,
            item.reason,
        )

    if total_count > limit:
        table.add_row("[dim]…[/]", "", f"[dim]and {total_count - limit} more items[/]", "")

    console.print()
    console.print(table)
    _show_freshness()
    console.print()


def _run_summary() -> None:
    from disk_cleanup.query import load_scan
    from disk_cleanup.scanners import CATEGORY_LABELS, RiskLevel

    result, _age = load_scan()
    if result is None:
        console.print("[yellow]No scan data. Run [bold]scan[/bold] first.[/]")
        console.print()
        return

    by_cat = result.by_category()
    by_risk = result.by_risk()

    console.print(f"  [bold]{len(result.items)}[/] items totaling "
                  f"[bold cyan]{format_size(result.total_size)}[/] reclaimable\n")

    # Category table
    cat_table = Table(title="By Category", border_style="dim", show_lines=False, padding=(0, 1))
    cat_table.add_column("Size", justify="right", width=10, style="bold", no_wrap=True)
    cat_table.add_column("Bar", width=22, no_wrap=True)
    cat_table.add_column("Category", no_wrap=True, overflow="ellipsis")
    cat_table.add_column("Items", justify="right", width=6, style="dim", no_wrap=True)

    max_cat_size = max((sum(i.size_bytes for i in items) for items in by_cat.values()), default=1)
    for cat, items in sorted(by_cat.items(), key=lambda x: sum(i.size_bytes for i in x[1]), reverse=True):
        total = sum(i.size_bytes for i in items)
        label = CATEGORY_LABELS.get(cat, cat.value)
        bar_w = 18
        filled = max(1, int(bar_w * total / max_cat_size)) if max_cat_size else 0
        bar = f"[cyan]{'█' * filled}[/][dim]{'░' * (bar_w - filled)}[/]"
        cat_table.add_row(format_size(total), bar, label, str(len(items)))

    console.print(cat_table)
    console.print()

    # Risk table
    risk_colors = {"safe": "green", "low": "yellow", "medium": "dark_orange", "high": "red"}
    risk_table = Table(title="By Risk Level", border_style="dim", show_lines=False, padding=(0, 1))
    risk_table.add_column("Size", justify="right", width=10, style="bold", no_wrap=True)
    risk_table.add_column("Risk", no_wrap=True)
    risk_table.add_column("Items", justify="right", width=6, style="dim", no_wrap=True)

    for risk in RiskLevel:
        items = by_risk.get(risk, [])
        if items:
            total = sum(i.size_bytes for i in items)
            c = risk_colors.get(risk.value, "white")
            risk_table.add_row(format_size(total), f"[{c}]{risk.value}[/]", str(len(items)))

    console.print(risk_table)
    _show_freshness()
    console.print()

def _run_scan() -> None:
    from disk_cleanup.cli import run_incremental_scan_with_progress, show_scan_results
    from disk_cleanup.config import Config
    console.print()
    config = Config.load()
    result = run_incremental_scan_with_progress(config)
    show_scan_results(result)
    console.print()

def _run_map(path: str | None = None) -> None:
    from disk_cleanup.cli import cmd_map
    console.print()
    cmd_map(Namespace(path=path, min_size=100, no_interactive=False))
    console.print()

def _run_whereis() -> None:
    from disk_cleanup.cli import cmd_whereis
    console.print()
    cmd_whereis(Namespace())
    console.print()


def _run_empty_trash() -> None:
    """Empty the macOS Trash with a confirmation prompt."""
    from disk_cleanup.cache import invalidate_scan
    from disk_cleanup.utils import empty_trash, format_size
    console.print()
    ok, freed = empty_trash()
    if ok and freed > 0:
        console.print(f"  [green]OK[/]  Trash emptied. [bold]{format_size(freed)}[/] freed.")
        invalidate_scan()
    elif ok:
        console.print("  Trash is already empty.")
    else:
        console.print("  [red]FAIL[/]  Could not empty Trash.")
    console.print()


def _run_rm(parts: list[str]) -> None:
    """Run rm in-process and offer to empty Trash if items were moved there."""
    from disk_cleanup.actions import remove_paths
    from disk_cleanup.config import Config

    # Parse flags from parts (skip 'rm' itself)
    args = parts[1:]
    confirm = "--confirm" in args
    permanent = "--permanent" in args
    paths = [a for a in args if not a.startswith("--")]

    if not paths:
        console.print("\n  [red]No paths specified.[/] Usage: rm <path> [--confirm] [--permanent]\n")
        return

    config = Config.load()
    result = remove_paths(paths, config, confirm=confirm, permanent=permanent)

    console.print()
    console.print(result.output)
    console.print()

    # If items were moved to Trash, offer to empty it
    if result.moved_to_trash:
        try:
            answer = console.input(
                "[yellow]Empty Trash now? (y/n):[/] "
            ).strip().lower()
        except (EOFError, KeyboardInterrupt):
            console.print()
            return

        if answer in ("y", "yes"):
            _run_empty_trash()
        else:
            console.print("  [dim]Run [bold]empty-trash[/bold] later to reclaim space.[/]")
            console.print()


# ── Run a storage subcommand directly ────────────────────────────────────────

def _run_storage_cmd(argv_str: str) -> None:
    """Run `storage <argv_str>` as a subprocess — inherits terminal for Rich output."""
    cmd = [sys.executable, "-m", "disk_cleanup", *argv_str.split()]
    try:
        console.print()
        subprocess.run(cmd, timeout=120)
        console.print()
    except subprocess.TimeoutExpired:
        console.print("[red]Command timed out.[/]")
    except OSError as e:
        console.print(f"[red]Failed to run command:[/] {e}")


# ── Recognized storage commands ──────────────────────────────────────────────

# Commands that have native Rich handlers (run in-process)
_NATIVE_COMMANDS = {"overview", "scan", "summary", "map", "whereis", "empty-trash"}

# Commands that run in-process but need special handling (e.g. follow-up prompts)
_INTERACTIVE_COMMANDS = {"rm"}

# Commands that are CLI subcommands (run as `storage <cmd> ...` subprocess)
_CLI_COMMANDS = {"query", "info", "clean", "analyze", "quick"}

# All command words
_ALL_COMMANDS = _NATIVE_COMMANDS | _INTERACTIVE_COMMANDS | _CLI_COMMANDS


def _looks_like_cli_args(parts: list[str]) -> bool:
    """Return True if the args after the command look like CLI flags/paths, not natural language."""
    if len(parts) <= 1:
        return True  # bare command with no args — always a direct command
    # If any arg starts with a flag or path prefix, it's CLI usage
    return any(a.startswith(("-", "~", "/", ".")) for a in parts[1:])


# ── Natural language → native command interception ───────────────────────────

import re as _re

_NL_OVERVIEW_PATTERNS = [
    _re.compile(r"\b(overview|disk\s*usage|how.s\s+my\s+disk|disk\s+(status|health|space))\b", _re.I),
    _re.compile(r"^(show|give|get)\s+(me\s+)?(an?\s+)?overview\b", _re.I),
    _re.compile(r"^how\s+(much|is)\s+(space|disk|storage)\b", _re.I),
]

_NL_SUMMARY_PATTERNS = [
    _re.compile(r"^(show|give|get)\s+(me\s+)?(a\s+)?summary\b", _re.I),
    _re.compile(r"\bsummary\s+of\s+(my\s+)?disk\b", _re.I),
]

# NL → query with category extraction
_NL_CATEGORY_PATTERNS = [
    _re.compile(r"what.?s\s+in\s+(the\s+)?(?P<cat>[\w/]+\s*(\&|and)?\s*[\w/]*?)\s*(category|section)", _re.I),
    _re.compile(r"(show|list|give|get)\s+(me\s+)?(all\s+|the\s+)?(?P<cat>[\w]+)\s*(files|items|stuff|things)?", _re.I),
    _re.compile(r"(breakdown|details?)\s+(of|for|on)\s+(the\s+)?(?P<cat>[\w/]+\s*(\&|and)?\s*[\w/]*?)\s*(category|section)?", _re.I),
]

_NL_BIGGEST_PATTERNS = [
    _re.compile(r"(biggest|largest|top|heaviest)\s+(files|items|things)", _re.I),
    _re.compile(r"what.?s\s+(taking|using|eating)\s+(the\s+most|all)\s+(space|room|disk)", _re.I),
]

# Map natural language category names to scan category values
_CATEGORY_ALIASES = {
    "ai": "model_caches", "ml": "model_caches", "models": "model_caches",
    "model": "model_caches", "ai/ml": "model_caches", "heavy caches": "model_caches",
    "ai/ml models": "model_caches", "ai/ml models & heavy caches": "model_caches",
    "model caches": "model_caches",
    "cache": "caches", "caches": "caches", "application caches": "caches",
    "download": "downloads", "downloads": "downloads",
    "log": "logs", "logs": "logs",
    "screenshot": "screenshots", "screenshots": "screenshots",
    "dev": "dev_artifacts", "build": "dev_artifacts", "dev artifacts": "dev_artifacts",
    "developer": "dev_artifacts", "build artifacts": "dev_artifacts",
    "developer build artifacts": "dev_artifacts",
    "git": "git_repos", "repos": "git_repos", "git repos": "git_repos",
    "xcode": "xcode",
    "large": "large_files", "large files": "large_files",
    "duplicate": "duplicates", "duplicates": "duplicates",
    "trash": "trash",
    "brew": "brew", "homebrew": "brew",
    "app": "applications", "apps": "applications", "applications": "applications",
    "large applications": "applications",
    "app containers": "applications", "sandboxed": "applications",
    "app containers & sandboxed data": "applications",
    "save": "save_files", "saves": "save_files",
}


def _extract_nl_category(text: str) -> str | None:
    """Try to extract a category name from natural language text."""
    lower = text.lower()

    # First: try to match the full display labels from CATEGORY_LABELS directly
    # by checking if any known alias phrase appears in the text
    for alias, cat_val in sorted(_CATEGORY_ALIASES.items(), key=lambda x: -len(x[0])):
        if alias in lower:
            return cat_val

    # Then try regex patterns
    for pat in _NL_CATEGORY_PATTERNS:
        m = pat.search(lower)
        if m:
            raw = m.group("cat").strip().rstrip("s ").lower()
            if raw in _CATEGORY_ALIASES:
                return _CATEGORY_ALIASES[raw]
            for alias, cat_val in _CATEGORY_ALIASES.items():
                if alias in raw or raw in alias:
                    return cat_val
    return None


def _match_native_command(text: str) -> str | None:
    """If text is natural language that maps to a native Rich command, return command name."""
    lower = text.lower().strip()

    # Don't intercept deletion requests
    if any(kw in lower for kw in ["delete", "remove", "clean", "rm "]):
        return None

    # Overview
    for pat in _NL_OVERVIEW_PATTERNS:
        if pat.search(text):
            return "overview+summary"

    # Summary
    for pat in _NL_SUMMARY_PATTERNS:
        if pat.search(text):
            return "summary"

    # Biggest items
    for pat in _NL_BIGGEST_PATTERNS:
        if pat.search(text):
            return "biggest"

    # Category query
    cat = _extract_nl_category(text)
    if cat:
        return f"query:{cat}"

    return None


# ── REPL loop ────────────────────────────────────────────────────────────────

def run_repl(*, yolo: bool = True) -> None:
    """Main REPL entry point."""
    copilot_bin = _find_copilot()
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    prefs = _load_prefs()
    model = _get_model(prefs)
    instructions = _load_instructions(project_root)

    _show_banner(model)

    # Set up readline history
    history_file = STORAGE_DIR / "history"
    STORAGE_DIR.mkdir(exist_ok=True)
    try:
        readline.read_history_file(str(history_file))
    except (FileNotFoundError, PermissionError, OSError):
        pass
    readline.set_history_length(500)

    # macOS libedit doesn't support \001/\002 invisible markers —
    # they strip ANSI entirely, killing prompt color. Instead: print
    # the colored prompt manually, then call input("") so readline
    # sees a zero-width prompt. Backspace and cursor keys work
    # correctly because readline's width math stays accurate.
    _PS_RAW = (
        "\033[1;36mstorage\033[0m"        # bold cyan "storage"
        "\033[90m > \033[0m"              # dark gray " > "
    )
    _PS_VISIBLE_LEN = len("storage > ")   # true display width

    def _rewrite_prompt_line(text: str) -> None:
        total = _PS_VISIBLE_LEN + len(text)
        try:
            cols = os.get_terminal_size().columns
        except OSError:
            cols = 80
        lines = max(1, -(-total // cols))  # ceil division
        # Move up `lines` lines, clearing each
        sys.stdout.write("\033[A\033[2K" * lines)
        sys.stdout.write(
            "\033[1;36mstorage\033[0m"        # bold cyan "storage"
            "\033[90m > \033[0m"              # dark gray " > "
            f"\033[97m{text}\033[0m\n"        # bright white command text
        )
        sys.stdout.flush()

    try:
        while True:
            try:
                sys.stdout.write(_PS_RAW)
                sys.stdout.flush()
                user_input = input("").strip()
            except EOFError:
                break

            if not user_input:
                continue

            _rewrite_prompt_line(user_input)

            # Strip leading / for backwards compat (e.g. /overview still works)
            clean = user_input.lstrip("/")
            parts = clean.split()
            cmd = parts[0].lower()

            # Normalize "empty trash" → "empty-trash"
            if cmd == "empty" and len(parts) > 1 and parts[1].lower() == "trash":
                cmd = "empty-trash"
                parts = ["empty-trash"] + parts[2:]

            # ── Direct commands ──────────────────────────────────────
            if cmd in ("exit", "quit", "q"):
                break

            if cmd == "help":
                _show_help()
                continue

            if cmd == "model":
                new_model = _pick_model(model)
                if new_model != model:
                    model = new_model
                    prefs["model"] = model
                    _save_prefs(prefs)
                    console.print(f"  [green]Model set to [bold]{model}[/][/]")
                console.print()
                continue

            # ── Native Rich commands ─────────────────────────────────
            if cmd in _NATIVE_COMMANDS and _looks_like_cli_args(parts):
                if cmd == "overview":
                    _run_overview()
                elif cmd == "scan":
                    _run_scan()
                elif cmd == "summary":
                    _run_summary()
                elif cmd == "map":
                    _run_map(parts[1] if len(parts) > 1 else None)
                elif cmd == "whereis":
                    _run_whereis()
                elif cmd == "empty-trash":
                    _run_empty_trash()
                continue

            # ── Interactive commands (in-process with follow-ups) ────
            if cmd in _INTERACTIVE_COMMANDS and _looks_like_cli_args(parts):
                if cmd == "rm":
                    _run_rm(parts)
                continue

            # ── CLI subcommands (run as subprocess) ──────────────────
            if cmd in _CLI_COMMANDS and _looks_like_cli_args(parts):
                _run_storage_cmd(clean)
                continue

            # ── Natural language → native Rich shortcut ──────────────
            # Intercept common requests so they get beautiful Rich output
            # instead of plain-text via Copilot.
            native_match = _match_native_command(user_input)
            if native_match:
                if native_match == "overview+summary":
                    _run_overview()
                    _run_summary()
                elif native_match == "summary":
                    _run_summary()
                elif native_match == "biggest":
                    _run_query_rich(limit=20)
                elif native_match.startswith("query:"):
                    cat = native_match.split(":", 1)[1]
                    _run_query_rich(category=cat, limit=50)
                continue

            # ── Natural language → Copilot ───────────────────────────
            _run_copilot(copilot_bin, model, user_input, project_root, instructions, yolo=yolo)

    except KeyboardInterrupt:
        console.print()

    finally:
        try:
            readline.write_history_file(str(history_file))
        except OSError:
            pass
        console.print("[dim]Have a good one![/]")

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
        color, status = "red", "critical"
    elif pct > 80:
        color, status = "dark_orange", "warning"
    else:
        color, status = "green", "healthy"

    bar_w = 30
    filled = int(bar_w * pct / 100)
    bar = f"[{color}]{'█' * filled}[/][dim]{'░' * (bar_w - filled)}[/]"

    console.print()
    console.print(Panel(
        Text.from_markup(
            f"[bold cyan]storage[/]  [dim]disk management for macOS[/]\n\n"
            f"  {bar}  [bold]{pct:.0f}%[/] used  —  "
            f"[bold]{format_size(disk['free'])}[/] free of {format_size(disk['total'])}\n"
            f"  model: [bold]{model}[/]   "
            f"[dim]type model to change  •  help for commands[/]"
        ),
        border_style="cyan",
        padding=(1, 2),
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
    console.print(Panel(
        Text.from_markup(
            "[bold cyan]Commands[/]\n\n"
            "  [bold]overview[/]    Disk usage bar\n"
            "  [bold]summary[/]    Category & risk breakdown\n"
            "  [bold]map[/] [dim][path][/]  Directory treemap\n"
            "  [bold]whereis[/]    Full system space breakdown\n"
            "  [bold]scan[/]       Run a fresh disk scan\n"
            "  [bold]query[/] [dim]...[/]  Query scan data (filters: --category, --risk, etc.)\n"
            "  [bold]info[/] [dim]<path>[/] Inspect a path in detail\n"
            "  [bold]rm[/] [dim]<path>[/]   Remove a path (dry-run by default, --confirm to execute)\n"
            "  [bold]empty-trash[/] Empty the macOS Trash to reclaim disk space\n"
            "  [bold]model[/]      Change the AI model\n"
            "  [bold]help[/]       Show this help\n"
            "  [bold]exit[/]       Quit\n\n"
            "[dim]Or just type a question in plain English.[/]\n"
            "[dim]Examples:[/]\n"
            '  [italic]what\'s using the most space?[/]\n'
            '  [italic]show me caches older than 6 months[/]\n'
            '  [italic]delete the pip cache[/]'
        ),
        title="storage help",
        border_style="dim",
        padding=(1, 2),
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


def _run_copilot(copilot_bin: str, model: str, prompt: str, project_root: str,
                 instructions: str = "") -> None:
    """Run Copilot CLI with live-streamed output to the terminal.

    - Shows animated spinner until the first byte of output.
    - Hides the cursor during processing.
    - Wraps Copilot's own text in dark gray.
    """
    full_prompt = prompt
    if instructions:
        full_prompt = f"{instructions}\n\nUser request: {prompt}"

    cmd = [
        copilot_bin,
        "--model", model,
        "--silent",
        "--no-custom-instructions",
        "--deny-tool=shell(rm)",
        "--deny-tool=shell(rmdir)",
        "--allow-tool=shell(storage)",
        "-p", full_prompt,
    ]

    # Near-white color for copilot output (color 253 ≈ #dadada)
    _CLR = "\033[38;5;253m"
    _RST = "\033[0m"

    spinner = _Spinner()
    spinner.start()

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=project_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

        first_line = True
        try:
            for raw_line in proc.stdout:
                if first_line:
                    spinner.stop()
                    sys.stdout.write(f"\033[?25l{_CLR}")  # hide cursor, set color
                    sys.stdout.flush()
                    first_line = False
                sys.stdout.buffer.write(raw_line)
                sys.stdout.buffer.flush()
        except KeyboardInterrupt:
            proc.kill()

        if first_line:
            spinner.stop()  # no output received

        proc.wait(timeout=5)

        # Reset color and show cursor
        sys.stdout.write(f"{_RST}\033[?25h")
        sys.stdout.flush()
        console.print()

    except subprocess.TimeoutExpired:
        proc.kill()
        spinner.stop()
        sys.stdout.write(f"{_RST}\033[?25h")
        sys.stdout.flush()
        console.print("\n[red]Request timed out.[/]\n")
    except KeyboardInterrupt:
        spinner.stop()
        sys.stdout.write(f"{_RST}\033[?25h")
        sys.stdout.flush()
        console.print()
    except OSError as e:
        spinner.stop()
        sys.stdout.write(f"{_RST}\033[?25h")
        sys.stdout.flush()
        console.print(f"\n[red]Failed to run Copilot:[/] {e}\n")


# ── Visual commands (run natively for Rich output) ──────────────────────────

def _run_overview() -> None:
    from disk_cleanup.cli import show_disk_overview
    console.print()
    show_disk_overview()

def _run_summary() -> None:
    from disk_cleanup.query import query_summary
    console.print(query_summary())
    console.print()

def _run_scan() -> None:
    from disk_cleanup.cli import cmd_scan
    console.print()
    cmd_scan(Namespace(category=None))
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
                "[yellow]Items were moved to Trash. Empty Trash now to free disk space? (y/n):[/] "
            ).strip().lower()
        except (EOFError, KeyboardInterrupt):
            console.print()
            return

        if answer in ("y", "yes"):
            _run_empty_trash()
        else:
            console.print("  [dim]Kept in Trash. Run [bold]empty-trash[/bold] later to free the space.[/]")
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


# ── REPL loop ────────────────────────────────────────────────────────────────

def run_repl() -> None:
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

    # macOS ships libedit (not GNU readline). libedit doesn't honor
    # \001/\002 invisible markers — they render as visible junk.
    # Raw ANSI in the prompt causes readline to over-count width by the
    # escape bytes, but the rewrite-on-enter below compensates, and it
    # only matters if input is within ~20 chars of the terminal edge.
    _PS = (
        "\033[1;36mstorage\033[0m"        # bold cyan "storage"
        "\033[90m > \033[0m"              # dark gray " > "
    )
    _PS_VISIBLE_LEN = len("storage > ")   # true display width

    # After Enter, rewrite the prompt line(s): command text in white.
    # Must clear *all* display lines the prompt+input occupied.
    # readline over-counts the prompt width by the escape byte overhead,
    # so we use that inflated width for line-count calculation to be safe.
    _PS_READLINE_LEN = len(_PS)           # what readline thinks the width is

    def _rewrite_prompt_line(text: str) -> None:
        # Use readline's inflated count to match how many lines it rendered
        total = _PS_READLINE_LEN + len(text)
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
                user_input = input(_PS).strip()
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

            # ── Natural language → Copilot ───────────────────────────
            _run_copilot(copilot_bin, model, user_input, project_root, instructions)

    except KeyboardInterrupt:
        console.print()

    finally:
        try:
            readline.write_history_file(str(history_file))
        except OSError:
            pass
        console.print("[dim]Have a good one![/]")

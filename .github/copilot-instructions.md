# Storage — Development Agent Instructions

You are the development agent for the `storage` macOS disk management CLI tool.
This is a Python project that provides a branded REPL (`storage` command) powered
by the Copilot CLI for natural language interactions.

## Project Architecture

```
disk_cleanup/
  __main__.py        # Entry point: dispatches CLI subcommands or launches REPL
  cli.py             # argparse CLI: scan, query, info, rm, map, overview, etc.
  repl.py            # Interactive REPL with Copilot CLI integration
  scanner.py         # Orchestrates scan modules
  scanners/          # Individual scan modules (caches, downloads, logs, etc.)
  query.py           # Query engine over cached scan results
  actions.py         # rm / delete logic (dry-run default, Trash integration)
  cache.py           # Scan result caching (~/.storage/scan.json)
  config.py          # User config (~/.storage/config.json)
  utils.py           # Shared helpers: format_size, get_disk_usage, etc.
  disk_map.py        # Directory treemap / system breakdown
  interactive.py     # TUI components (interactive map, etc.)
  ai_advisor.py      # AI analysis integration
  cleaner.py         # Batch cleanup operations
  system_prompt.md   # System prompt for the REPL's Copilot CLI calls
```

## Key Conventions

- **Entry point**: `storage` command is installed via `pyproject.toml` console_scripts
- **REPL**: The bare `storage` command launches `repl.py:run_repl()` — a branded
  prompt that routes commands in-process or delegates natural language to Copilot CLI
- **Copilot CLI integration**: Natural language queries are sent to `copilot -p`
  with `system_prompt.md` prepended as instructions. Output is streamed in real-time.
- **Rich**: Used for all formatted terminal output (panels, tables, progress)
- **Safety**: All deletions are dry-run by default. `--confirm` required to execute.
  Items go to Trash unless `--permanent` is specified.
- **Scan cache**: Scans are expensive; results are cached in `~/.storage/scan.json`

## When Editing

- The REPL prompt uses raw ANSI codes (not Rich) for readline compatibility on macOS libedit
- `system_prompt.md` contains instructions for the **end-user-facing AI** inside the REPL,
  not for this development agent. Don't confuse the two.
- Scanner modules in `scanners/` follow a consistent pattern: each exports a `scan()` function
- Test changes to the REPL by running `storage` in the terminal
- The project uses no test framework yet — verify manually

## Style

- Python 3.11+ (type hints, match statements OK)
- Minimal dependencies: `rich` is the only required external package
- Keep it simple. No over-engineering. This is a single-user CLI tool.

"""Command-line entry point for Asco."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from .beads import Beads, BeadsError
from .runner import Runner
from .tui import AscoTui


def root_directory() -> Path:
    result = subprocess.run(["git", "rev-parse", "--show-toplevel"], text=True, capture_output=True)
    return Path(result.stdout.strip()) if not result.returncode else Path.cwd()


def command_run(args: argparse.Namespace, root: Path, beads: Beads) -> int:
    runner = Runner(root, beads, root / "prompts" / "engineer.md")
    if args.once:
        with runner.lock():
            return 0 if runner.run_one() else 1
    runner.run_forever(args.interval)
    return 0


def command_tui(root: Path, beads: Beads) -> int:
    AscoTui(root, beads).run()
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Asco dispatches native Beads work to Codex.")
    subparsers = result.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="Run the local one-worker dispatcher.")
    run.add_argument("--once", action="store_true")
    run.add_argument("--interval", type=float, default=5.0)
    subparsers.add_parser("tui", help="Open the CEO terminal interface.")
    return result


def main() -> int:
    args = parser().parse_args()
    root = root_directory()
    beads = Beads(root)
    try:
        if args.command == "run": return command_run(args, root, beads)
        if args.command == "tui": return command_tui(root, beads)
    except (BeadsError, RuntimeError) as error:
        print(f"asco: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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


def command_tui(args: argparse.Namespace, root: Path, beads: Beads) -> int:
    AscoTui(root, beads, args.tmux_session).run()
    return 0


def command_respond(args: argparse.Namespace, beads: Beads) -> int:
    issue = beads.show(args.issue)
    if issue.get("status") == "closed":
        raise RuntimeError(f"Escalation {args.issue} is already closed.")
    if issue.get("issue_type", issue.get("type")) != "escalation":
        raise RuntimeError(f"Bead {args.issue} is not an escalation.")
    try:
        response = args.message or input("CEO response: ").strip()
    except EOFError as error:
        raise RuntimeError("An escalation response is required.") from error
    if not response:
        raise RuntimeError("An escalation response is required.")
    beads.comment(args.issue, f"CEO decision: {response}")
    beads.close(args.issue, "CEO resolved the escalation.")
    metadata = issue.get("metadata") or {}
    blocked_task = metadata.get("asco", {}).get("blocked_task")
    if blocked_task:
        beads.update(str(blocked_task), "--status", "open", "--assignee", "")
        beads.comment(str(blocked_task), f"CEO answer from {args.issue}: {response}")
    print(f"Asco recorded the response for {args.issue}.")
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Asco dispatches native Beads work to Codex.")
    subparsers = result.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="Run the local one-worker dispatcher.")
    run.add_argument("--once", action="store_true")
    run.add_argument("--interval", type=float, default=5.0)
    tui = subparsers.add_parser("tui", help="Open the CEO terminal interface.")
    tui.add_argument("--tmux-session", default="asco-company", help="The tmux session that owns the logs pane.")
    respond = subparsers.add_parser("respond", help="Record a CEO response to an escalation.")
    respond.add_argument("issue", help="The escalation Bead ID.")
    respond.add_argument("--message", help="The CEO decision. If omitted, Asco prompts for it.")
    return result


def main() -> int:
    args = parser().parse_args()
    root = root_directory()
    beads = Beads(root)
    try:
        if args.command == "run": return command_run(args, root, beads)
        if args.command == "tui": return command_tui(args, root, beads)
        if args.command == "respond": return command_respond(args, beads)
    except (BeadsError, RuntimeError) as error:
        print(f"asco: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

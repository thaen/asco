"""Command-line entry point for Asco."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from .beads import Beads, BeadsError
from .runner import Runner
from .tui import AscoTui


def root_directory() -> Path:
    result = subprocess.run(["git", "rev-parse", "--show-toplevel"], text=True, capture_output=True)
    return Path(result.stdout.strip()) if not result.returncode else Path.cwd()


def submit_pair(beads: Beads, title: str, objective: str, parent: str | None, priority: str) -> tuple[str, str]:
    engineering = beads.create(title, objective, "engineering", priority, parent, {"asco": {"role": "engineering"}})
    audit = beads.create(
        f"Audit: {title}",
        f"Audit the result of {engineering}. Read its task record, comments, repository evidence, tests, and pull request. Pass with evidence, escalate, or create correction tasks and a successor audit.",
        "audit", priority, engineering, {"asco": {"role": "audit", "audit_of": engineering, "round": 1}},
    )
    beads.comment(engineering, f"Asco created dependent audit task {audit}.")
    return engineering, audit


def command_submit(args: argparse.Namespace, beads: Beads) -> int:
    engineering, audit = submit_pair(beads, args.title, args.objective, args.parent, args.priority)
    print(json.dumps({"engineering": engineering, "audit": audit}))
    return 0


def command_comment(args: argparse.Namespace, beads: Beads) -> int:
    beads.comment(args.task, args.message)
    return 0


def command_complete(args: argparse.Namespace, beads: Beads) -> int:
    beads.comment(args.task, f"Completion evidence: {args.evidence}")
    beads.close(args.task, args.evidence)
    return 0


def command_block(args: argparse.Namespace, beads: Beads) -> int:
    beads.add_dependency(args.task, args.blocker)
    beads.update(args.task, "--status", "open")
    beads.comment(args.task, f"Blocked by {args.blocker}: {args.reason}")
    return 0


def command_escalate(args: argparse.Namespace, beads: Beads) -> int:
    escalation = beads.create(f"CEO decision needed: {args.title}", args.question, "escalation", "1", metadata={"asco": {"blocked_task": args.task}})
    beads.add_dependency(args.task, escalation)
    beads.update(args.task, "--status", "open")
    beads.comment(args.task, f"Escalated to the CEO as {escalation}: {args.question}")
    print(escalation)
    return 0


def command_resolve(args: argparse.Namespace, beads: Beads) -> int:
    escalation = beads.show(args.escalation)
    metadata = escalation.get("metadata") or {}
    blocked_task = metadata.get("asco", {}).get("blocked_task")
    beads.comment(args.escalation, f"CEO decision: {args.answer}")
    beads.close(args.escalation, "CEO resolved the escalation.")
    if blocked_task:
        beads.update(str(blocked_task), "--status", "open")
        beads.comment(str(blocked_task), f"CEO answer from {args.escalation}: {args.answer}")
    return 0


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
    result = argparse.ArgumentParser(description="Asco is a small Beads-backed software company.")
    subparsers = result.add_subparsers(dest="command", required=True)
    submit = subparsers.add_parser("submit", help="Submit an Engineer-and-Audit task pair.")
    submit.add_argument("title")
    submit.add_argument("objective")
    submit.add_argument("--parent")
    submit.add_argument("--priority", default="2")
    comment = subparsers.add_parser("comment", help="Add a task status comment.")
    comment.add_argument("task")
    comment.add_argument("message")
    complete = subparsers.add_parser("complete", help="Close a task with completion evidence.")
    complete.add_argument("task")
    complete.add_argument("evidence")
    block = subparsers.add_parser("block", help="Block a task on another Bead.")
    block.add_argument("task")
    block.add_argument("blocker")
    block.add_argument("reason")
    escalate = subparsers.add_parser("escalate", help="Escalate a decision to the CEO.")
    escalate.add_argument("task")
    escalate.add_argument("title")
    escalate.add_argument("question")
    resolve = subparsers.add_parser("resolve", help="Resolve a CEO escalation.")
    resolve.add_argument("escalation")
    resolve.add_argument("answer")
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
        if args.command == "submit": return command_submit(args, beads)
        if args.command == "comment": return command_comment(args, beads)
        if args.command == "complete": return command_complete(args, beads)
        if args.command == "block": return command_block(args, beads)
        if args.command == "escalate": return command_escalate(args, beads)
        if args.command == "resolve": return command_resolve(args, beads)
        if args.command == "run": return command_run(args, root, beads)
        if args.command == "tui": return command_tui(root, beads)
    except (BeadsError, RuntimeError) as error:
        print(f"asco: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

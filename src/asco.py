#!/usr/bin/env python3
"""A local Beads dispatcher for one Git repository."""

import argparse
import curses
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


POLL_SECONDS = 3
METADATA_PREFIX = "asco_"


class CommandError(RuntimeError):
    pass


def stamp():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def command(root, args, check=True, **kwargs):
    result = subprocess.run(args, cwd=root, text=True, capture_output=True, **kwargs)
    if check and result.returncode:
        raise CommandError("%s: %s" % (" ".join(args), result.stderr.strip()))
    return result


class Beads:
    def __init__(self, root):
        self.root = Path(root).resolve()

    def run(self, *args, check=True):
        return command(self.root, ["bd", *args], check=check)

    def json(self, *args):
        output = self.run(*args, "--json").stdout
        value = json.loads(output or "[]")
        if isinstance(value, dict):
            return value.get("issues", value.get("items", [value]))
        return value

    def all(self):
        return self.json("list", "--all", "--limit", "0")

    def ready(self):
        return self.json("ready", "--limit", "0")

    def show(self, issue_id):
        value = self.json("show", issue_id)
        return value[0] if isinstance(value, list) and value else value

    def update_metadata(self, issue_id, **values):
        args = ["update", issue_id]
        for key, value in values.items():
            args.extend(["--set-metadata", "%s%s=%s" % (METADATA_PREFIX, key, value)])
        self.run(*args)

    def comment(self, issue_id, body):
        self.run("comment", issue_id, body)


def issue_id(issue):
    return issue.get("id") or issue.get("issue_id")


def issue_type(issue):
    return issue.get("issue_type") or issue.get("type")


def is_escalation(issue):
    labels = issue.get("labels") or []
    return issue_type(issue) == "escalation" or "escalation" in labels


def metadata(issue):
    value = issue.get("metadata") or {}
    if not isinstance(value, dict):
        return {}
    result = dict(value)
    legacy = value.get("asco")
    if isinstance(legacy, dict):
        for key, item in legacy.items():
            result.setdefault("asco_" + key, item)
    return result


def parent_id(issue):
    parent = issue.get("parent") or issue.get("parent_id")
    if isinstance(parent, dict):
        return issue_id(parent)
    return parent


def process_alive(pid):
    try:
        os.kill(int(pid), 0)
    except (OSError, ValueError, TypeError):
        return False
    return True


def blocking_ids(issue):
    values = issue.get("blocked_by") or issue.get("blockers") or []
    if isinstance(values, str):
        return [values]
    result = []
    for value in values:
        if isinstance(value, str):
            result.append(value)
        elif isinstance(value, dict):
            candidate = value.get("id") or value.get("issue_id") or value.get("depends_on_id")
            if candidate:
                result.append(candidate)
    return result


def dispatcher_processes(root):
    result = subprocess.run(["ps", "-axo", "pid=,etime=,command="], text=True,
                            capture_output=True, check=False)
    marker = str(Path(__file__).resolve())
    root = str(Path(root).resolve())
    return [line.strip() for line in result.stdout.splitlines()
            if marker in line and "_serve" in line and root in line]


def default_branch(root):
    remote = command(root, ["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"], check=False)
    if remote.returncode == 0:
        return remote.stdout.strip().rsplit("/", 1)[-1]
    return command(root, ["git", "branch", "--show-current"]).stdout.strip()


def task_paths(root, task_id, epic_id=None):
    state = Path(root) / ".asco"
    safe = task_id.replace("/", "-")
    kind = "epics" if epic_id is None else "tasks"
    worktree = state / "worktrees" / kind / safe
    branch = "asco/%s%s" % ("epic-" if epic_id is None else "task-", safe)
    return worktree, branch, state / "logs" / (safe + ".log")


def make_worktree(root, worktree, branch, base):
    if worktree.exists():
        return
    worktree.parent.mkdir(parents=True, exist_ok=True)
    exists = command(root, ["git", "show-ref", "--verify", "--quiet", "refs/heads/%s" % branch], check=False)
    args = ["git", "worktree", "add"]
    if exists.returncode:
        args.extend(["-b", branch])
    args.extend([str(worktree), base])
    command(root, args)


def engineer_prompt(issue, epic, root, worktree, branch, log_path):
    task = issue_id(issue)
    parent = issue_id(epic) if epic else None
    original = (epic or issue).get("description", "")
    return f"""You are the Engineer assigned to Beads task {task}.

You work in {worktree} on branch {branch}. The repository root is {root}. The task is:

{issue.get('title', '')}

{issue.get('description', '')}

The feature Epic is {parent or task}. Its original request is:

{original}

Use Beads for durable communication. Read comments before work and add a concise handoff comment when the task is complete. Make only the changes this task requires. Commit every repository change on this branch before closing the task. Do not merge your branch into the Epic integration branch; Asco performs that merge before dependent work begins. Do not close a task with uncommitted changes.

If a decision needs a user answer, create a blocked Beads task of type escalation under Epic {parent or task}; state the question, options, and the consequence of each option. If a new task belongs to this feature, create it with --parent {parent or task}, add required blocks dependencies, and describe its relationship in a comment.

For an Epic decomposition task, create child tasks for high-level tests, implementation, test, audit, and merge as the work requires. Use blocks dependencies in the required order. The merge task must block on every task whose branch it integrates. Include this original request in the audit task. The Epic remains in progress while its children run.

For a merge task, acquire `bd merge-slot acquire` before merging the Epic integration branch into {default_branch(root)}, then build and test. Release the slot in a finally-style cleanup step. Remove task worktrees only after the merge succeeds. Leave the integration worktree for Asco to remove after it closes the Epic.

Worker output is recorded at {log_path.relative_to(root)}. Beads metadata contains this worker's process record.
"""


class Runner:
    def __init__(self, root, parallel):
        self.root = Path(root).resolve()
        self.parallel = parallel
        self.bd = Beads(self.root)
        self.workers = {}
        self.last_queue_summary = None

    def ensure_escalation_type(self):
        configured = self.bd.run("config", "get", "types.custom", check=False)
        types = [item.strip() for item in configured.stdout.strip().split(",") if item.strip()]
        if "escalation" not in types:
            types.append("escalation")
            self.bd.run("config", "set", "types.custom", ",".join(types))

    def worker_count(self):
        count = 0
        for issue in self.bd.all():
            record = metadata(issue)
            if not record.get("asco_exited_at") and process_alive(record.get("asco_pid")):
                count += 1
        return count

    def epic_for(self, issue, issues):
        parent = parent_id(issue)
        by_id = {issue_id(item): item for item in issues}
        candidate = by_id.get(parent)
        return candidate if candidate and issue_type(candidate) == "epic" else None

    def start(self, issue, issues):
        task = issue_id(issue)
        epic = self.epic_for(issue, issues)
        if issue_type(issue) == "epic":
            worktree, branch, log_path = task_paths(self.root, task)
            make_worktree(self.root, worktree, branch, default_branch(self.root))
        else:
            if not epic:
                return False
            integration = metadata(epic).get("asco_worktree")
            integration_branch = metadata(epic).get("asco_branch")
            if not integration or not integration_branch:
                self.escalate(task, "The parent Epic has no integration worktree record.")
                return False
            worktree, branch, log_path = task_paths(self.root, task, issue_id(epic))
            make_worktree(self.root, worktree, branch, integration_branch)

        claimed = self.bd.run("update", task, "--claim", check=False)
        if claimed.returncode:
            return False
        log_path.parent.mkdir(parents=True, exist_ok=True)
        prompt = engineer_prompt(issue, epic, self.root, worktree, branch, log_path)
        log = open(log_path, "a", encoding="utf-8")
        child = subprocess.Popen(
            ["codex", "exec", "--dangerously-bypass-approvals-and-sandbox", "-"],
            cwd=worktree, stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT,
            text=True, start_new_session=True,
            env={**os.environ, "BEADS_DIR": str(self.root / ".beads")},
        )
        child.stdin.write(prompt)
        child.stdin.close()
        log.close()
        self.workers[task] = child
        self.bd.update_metadata(task, agent="engineer", pid=child.pid, started_at=stamp(),
                                branch=branch, worktree=worktree, log=log_path.relative_to(self.root))
        self.bd.comment(task, "Asco started Engineer PID %s in %s." % (child.pid, branch))
        return True

    def escalate(self, source, reason):
        title = "Asco escalation for %s" % source
        for issue in self.bd.all():
            if issue.get("status") == "closed":
                continue
            record = metadata(issue)
            if record.get("asco_source") == source or issue.get("title") == title:
                return
        result = self.bd.run("create", title, "--type", "escalation",
                             "--description", reason, "--silent", check=False)
        if result.returncode:
            result = self.bd.run("create", title, "--type", "task",
                                 "--add-label", "escalation", "--description", reason, "--silent")
        escalation = result.stdout.strip()
        self.bd.run("update", escalation, "--status", "blocked")
        self.bd.update_metadata(escalation, source=source)
        self.bd.comment(source, "Asco escalated: %s" % reason)

    def reap(self, issues):
        for issue in issues:
            record = metadata(issue)
            pid = record.get("asco_pid")
            task = issue_id(issue)
            worker = self.workers.get(task)
            exit_code = worker.poll() if worker else None
            if worker and exit_code is not None:
                self.workers.pop(task, None)
                self.bd.update_metadata(task, exited_at=stamp(), exit_state="exited", exit_code=exit_code)
                if issue.get("status") == "in_progress":
                    self.bd.comment(issue_id(issue), "Engineer process exited while the task remains in progress. Review %s." % record.get("asco_log", "the worker log"))
            elif pid and not process_alive(pid) and not record.get("asco_exited_at"):
                self.bd.update_metadata(task, exited_at=stamp(), exit_state="unknown-after-restart")
                if issue.get("status") == "in_progress":
                    self.bd.comment(task, "Engineer process is no longer present while the task remains in progress. Review %s." % record.get("asco_log", "the worker log"))

    def integrate(self, issues):
        for issue in issues:
            if issue.get("status") != "closed" or issue_type(issue) == "epic":
                continue
            record = metadata(issue)
            if record.get("asco_merged") == "true":
                continue
            epic = self.epic_for(issue, issues)
            if not epic:
                continue
            source = record.get("asco_branch")
            target = metadata(epic).get("asco_worktree")
            if not source or not target:
                continue
            result = command(self.root, ["git", "-C", target, "merge", "--no-ff", "--no-edit", source], check=False)
            if result.returncode:
                self.escalate(issue_id(issue), "Asco could not merge %s into %s: %s" % (source, target, result.stderr.strip()))
                continue
            self.bd.update_metadata(issue_id(issue), merged="true", merged_at=stamp())
            self.bd.comment(issue_id(issue), "Asco merged %s into the Epic integration branch." % source)

    def close_epics(self, issues):
        for epic in issues:
            if issue_type(epic) != "epic" or epic.get("status") != "in_progress":
                continue
            children = [item for item in issues if parent_id(item) == issue_id(epic)]
            if children and all(item.get("status") == "closed" for item in children):
                self.bd.run("close", issue_id(epic))

    def cleanup_closed_epics(self, issues):
        for epic in issues:
            if issue_type(epic) != "epic" or epic.get("status") != "closed":
                continue
            record = metadata(epic)
            if record.get("asco_cleaned") == "true":
                continue
            children = [item for item in issues if parent_id(item) == issue_id(epic)]
            if any(process_alive(metadata(item).get("asco_pid")) for item in children):
                continue
            paths = [metadata(item).get("asco_worktree") for item in children]
            paths.append(record.get("asco_worktree"))
            for path in filter(None, paths):
                path = Path(path)
                expected = self.root / ".asco" / "worktrees"
                if expected not in path.parents:
                    self.escalate(issue_id(epic), "Asco refused to remove worktree outside .asco: %s" % path)
                    break
                result = command(self.root, ["git", "worktree", "remove", str(path)], check=False)
                if result.returncode:
                    self.escalate(issue_id(epic), "Asco could not remove closed worktree %s: %s" % (path, result.stderr.strip()))
                    break
            else:
                self.bd.update_metadata(issue_id(epic), cleaned="true", cleaned_at=stamp())

    def cycle(self):
        issues = self.bd.all()
        self.reap(issues)
        self.integrate(issues)
        refreshed = self.bd.all()
        self.close_epics(refreshed)
        self.cleanup_closed_epics(self.bd.all())
        capacity = self.parallel - self.worker_count()
        if capacity <= 0:
            return
        current = self.bd.all()
        ready = self.bd.ready()
        dispatchable = [issue for issue in ready if issue_type(issue) == "epic" or self.epic_for(issue, current)]
        summary = "ready=%s dispatchable=%s active_workers=%s" % (len(ready), len(dispatchable), self.worker_count())
        if summary != self.last_queue_summary:
            print("%s asco: %s" % (stamp(), summary), flush=True)
            self.last_queue_summary = summary
        for issue in dispatchable:
            if capacity <= 0:
                break
            if self.start(issue, current):
                capacity -= 1

    def serve(self):
        self.ensure_escalation_type()
        while True:
            try:
                self.cycle()
            except CommandError as error:
                print("%s asco: %s" % (stamp(), error), file=sys.stderr, flush=True)
            time.sleep(POLL_SECONDS)


def visible_issues(root, all_closed=False):
    bd = Beads(root)
    issues = bd.all()
    blocked = {issue_id(item): item for item in bd.json("blocked")}
    open_issues = [item for item in issues if item.get("status") != "closed"]
    closed = sorted((item for item in issues if item.get("status") == "closed"), key=lambda item: item.get("closed_at") or "", reverse=True)
    return open_issues + (closed if all_closed else closed[:10]), blocked


def render_status(root, all_closed=False):
    issues, blocked = visible_issues(root, all_closed)
    dispatchers = dispatcher_processes(root)
    dispatcher = "running: " + "; ".join(dispatchers) if dispatchers else "not running"
    lines = ["ASCO task status", "Dispatcher: " + dispatcher, ""]
    waiting = [issue for issue in issues if issue.get("status") == "blocked" and is_escalation(issue)]
    if waiting:
        lines.extend(["Escalations waiting for you:"])
        for issue in waiting:
            lines.append("%s: %s" % (issue_id(issue), issue.get("title", "")))
            lines.append("  " + issue.get("description", ""))
        lines.append("")
    lines.append("Task             Status                Assigned             Worker       Title")
    for issue in issues:
        task = issue_id(issue)
        status = "Done" if issue.get("status") == "closed" else issue.get("status", "unknown")
        if task in blocked and issue.get("status") == "open":
            status = "dependency-blocked"
        record = metadata(issue)
        assigned = issue.get("assignee") or issue.get("owner") or "unassigned"
        worker = "running pid %s" % record.get("asco_pid") if process_alive(record.get("asco_pid")) else ""
        blockers = ", ".join(blocking_ids(blocked.get(task, issue)))
        suffix = " [blocked by %s]" % blockers if blockers else ""
        lines.append("%-16s %-21s %-20.20s %-12s %s%s" %
                     (task, status, assigned, worker, issue.get("title", ""), suffix))
    lines.extend(["", "Use `asco answer ESCALATION_ID \"answer\"` to answer an escalation."])
    return "\n".join(lines)


def log_tail(root, count=5):
    path = Path(root) / ".asco" / "logs" / "runner.log"
    try:
        with path.open(encoding="utf-8") as log:
            return log.read().splitlines()[-count:]
    except FileNotFoundError:
        return []


def dashboard(root):
    show_all = [False]
    def draw(screen):
        curses.curs_set(0)
        screen.nodelay(True)
        while True:
            screen.erase()
            split = max(45, int(curses.COLS * 0.62))
            for row, line in enumerate(render_status(root, show_all[0]).splitlines()):
                if row < curses.LINES - 1:
                    screen.addnstr(row, 0, line, split - 1)
            if split < curses.COLS - 15:
                screen.vline(0, split, curses.ACS_VLINE, curses.LINES - 1)
                screen.addnstr(0, split + 2, "Dispatcher log", curses.COLS - split - 3)
                lines = log_tail(root)
                if not lines:
                    lines = ["No dispatcher output yet."]
                for row, line in enumerate(lines, start=2):
                    if row < curses.LINES - 1:
                        screen.addnstr(row, split + 2, line, curses.COLS - split - 3)
            screen.addnstr(curses.LINES - 1, 0, "q: quit   c: toggle all closed", curses.COLS - 1)
            screen.refresh()
            key = screen.getch()
            if key in (ord("q"), 27):
                return
            if key == ord("c"):
                show_all[0] = not show_all[0]
            time.sleep(0.25)
    curses.wrapper(draw)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="asco")
    parser.add_argument("--root", default=os.getcwd(), help="Git repository root; default: current directory")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="start the nonblocking dispatcher")
    run.add_argument("--parallel", type=int, required=True)
    commands.add_parser("_serve", help=argparse.SUPPRESS).add_argument("--parallel", type=int, required=True)
    status = commands.add_parser("status", help="print Beads task status")
    status.add_argument("--all-closed", action="store_true")
    commands.add_parser("dashboard", help="open the terminal dashboard")
    answer = commands.add_parser("answer", help="record an answer on an escalation")
    answer.add_argument("issue")
    answer.add_argument("text")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    if not (root / ".git").exists():
        parser.error("--root must name a Git repository")
    if args.command == "run":
        if args.parallel < 1:
            parser.error("--parallel must be at least one")
        Beads(root).run("info")
        log_dir = root / ".asco" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log = open(log_dir / "runner.log", "a", encoding="utf-8")
        worker = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--root", str(root), "_serve", "--parallel", str(args.parallel)], stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        print("Asco dispatcher started with PID %s." % worker.pid)
    elif args.command == "_serve":
        Runner(root, args.parallel).serve()
    elif args.command == "status":
        print(render_status(root, args.all_closed))
    elif args.command == "dashboard":
        dashboard(root)
    else:
        Beads(root).comment(args.issue, "User answer: %s" % args.text)
        Beads(root).run("update", args.issue, "--status", "open")
        print("The answer was recorded and the escalation is open for an Engineer.")


if __name__ == "__main__":
    try:
        main()
    except CommandError as error:
        print("asco: %s" % error, file=sys.stderr)
        sys.exit(1)

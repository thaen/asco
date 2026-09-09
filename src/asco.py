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


def has_label(issue, label):
    return label in (issue.get("labels") or [])


def is_integration(issue):
    return has_label(issue, "asco:integration")


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


def metadata_true(record, key):
    return record.get(key) is True or record.get(key) == "true"


def needs_input(issue):
    return issue.get("status") == "blocked" and metadata_true(metadata(issue), "asco_needs_input")


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


def task_paths(root, task_id):
    state = Path(root) / ".asco"
    safe = task_id.replace("/", "-")
    worktree = state / "worktrees" / "tasks" / safe
    branch = "asco/task-" + safe
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


def registered_worktrees(root):
    result = command(root, ["git", "worktree", "list", "--porcelain"])
    return {Path(line[len("worktree "):]).resolve()
            for line in result.stdout.splitlines() if line.startswith("worktree ")}


def prompt_name(issue):
    return metadata(issue).get("asco_prompt", "engineer")


def prompt_text(root, name):
    if not name.replace("-", "").isalnum():
        raise CommandError("invalid prompt name: %s" % name)
    prompts = (Path(root) / "prompts").resolve()
    path = (prompts / (name + ".md")).resolve()
    if prompts not in path.parents or not path.is_file():
        raise CommandError("prompt framework does not exist: %s" % name)
    return path.read_text(encoding="utf-8")


def engineer_prompt(issue, root, worktree, branch, log_path):
    task = issue_id(issue)
    framework = prompt_name(issue)
    return f"""You are the Engineer assigned to Beads task {task}.

You work in {worktree} on branch {branch}. The repository root is {root}. The task is:

{issue.get('title', '')}

{issue.get('description', '')}

The selected prompt framework is `{framework}`:

{prompt_text(root, 'engineer')}

{'' if framework == 'engineer' else prompt_text(root, framework)}

Worker output is recorded at {log_path.relative_to(root)}. Beads metadata contains this worker's process record.
"""


class Runner:
    def __init__(self, root, parallel):
        self.root = Path(root).resolve()
        self.parallel = parallel
        self.bd = Beads(self.root)
        self.workers = {}
        self.last_queue_summary = None

    def log(self, message):
        print("%s asco: %s" % (stamp(), message), file=sys.stderr, flush=True)

    def worker_count(self):
        count = 0
        for issue in self.bd.all():
            record = metadata(issue)
            if not record.get("asco_exited_at") and process_alive(record.get("asco_pid")):
                count += 1
        return count

    def clear_exit_metadata(self, task):
        self.bd.run("update", task,
                    "--unset-metadata", "asco_exited_at",
                    "--unset-metadata", "asco_exit_state",
                    "--unset-metadata", "asco_exit_code")

    def dispatchable_tasks(self, ready, issues):
        active_integration = any(issue.get("status") == "in_progress" and is_integration(issue)
                                 for issue in issues)
        return [issue for issue in ready
                if not is_escalation(issue) and not has_label(issue, "gt:slot") and
                (not is_integration(issue) or not active_integration)]

    def start(self, issue, issues):
        task = issue_id(issue)
        worktree, branch, log_path = task_paths(self.root, task)
        make_worktree(self.root, worktree, branch, default_branch(self.root))

        claimed = self.bd.run("update", task, "--claim", check=False)
        if claimed.returncode:
            return False
        self.clear_exit_metadata(task)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        prompt = engineer_prompt(issue, self.root, worktree, branch, log_path)
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
        attempts = int(metadata(issue).get("asco_attempts", 0)) + 1
        self.bd.update_metadata(task, agent="engineer", pid=child.pid, started_at=stamp(), attempts=attempts,
                                branch=branch, worktree=worktree, log=log_path.relative_to(self.root))
        self.bd.comment(task, "Asco started Engineer PID %s in %s." % (child.pid, branch))
        return True

    def recover_unfinished_task(self, task, record):
        attempts = int(record.get("asco_attempts", 1))
        log = record.get("asco_log", "the worker log")
        if attempts < 2:
            self.bd.run("update", task, "--status", "open")
            self.bd.comment(task, "Engineer exited before completion. Asco will retry once; see %s." % log)
            return
        self.bd.run("update", task, "--status", "blocked", "--set-metadata", "asco_needs_input=true")
        self.bd.comment(task, "Engineer exited twice before completion. Review %s and run `asco answer %s \"...\"`." % (log, task))

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
                if self.bd.show(task).get("status") == "in_progress":
                    self.recover_unfinished_task(task, record)
            elif pid and not process_alive(pid) and not record.get("asco_exited_at"):
                self.bd.update_metadata(task, exited_at=stamp(), exit_state="unknown-after-restart")
                if self.bd.show(task).get("status") == "in_progress":
                    self.recover_unfinished_task(task, record)

    def cleanup_closed_tasks(self, issues):
        try:
            registered = registered_worktrees(self.root)
        except CommandError as error:
            self.log("could not list worktrees: %s" % error)
            return
        expected = (self.root / ".asco" / "worktrees").resolve()
        for issue in issues:
            record = metadata(issue)
            raw_path = record.get("asco_worktree")
            if issue.get("status") != "closed" or not raw_path or metadata_true(record, "asco_cleaned"):
                continue
            if process_alive(record.get("asco_pid")):
                continue
            path = Path(raw_path).resolve()
            if expected not in path.parents:
                self.log("refused to remove out-of-scope worktree for %s: %s" % (issue_id(issue), path))
                continue
            if path in registered:
                result = command(self.root, ["git", "worktree", "remove", str(path)], check=False)
                if result.returncode:
                    self.log("could not remove closed worktree for %s: %s: %s" %
                             (issue_id(issue), path, result.stderr.strip()))
                    continue
                registered.remove(path)
            else:
                self.log("worktree already removed for %s: %s" % (issue_id(issue), path))
            self.bd.update_metadata(issue_id(issue), cleaned="true", cleaned_at=stamp())

    def cycle(self):
        issues = self.bd.all()
        self.reap(issues)
        self.cleanup_closed_tasks(self.bd.all())
        capacity = self.parallel - self.worker_count()
        if capacity <= 0:
            return
        current = self.bd.all()
        ready = self.bd.ready()
        dispatchable = self.dispatchable_tasks(ready, current)
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
        while True:
            try:
                self.cycle()
            except CommandError as error:
                print("%s asco: %s" % (stamp(), error), file=sys.stderr, flush=True)
            time.sleep(POLL_SECONDS)


def status_snapshot(root):
    bd = Beads(root)
    issues = bd.all()
    blocked = {issue_id(item): item for item in bd.json("blocked")}
    return issues, blocked


def visible_issues(snapshot, all_closed=False):
    issues, blocked = snapshot
    open_issues = [item for item in issues if item.get("status") != "closed"]
    closed = sorted((item for item in issues if item.get("status") == "closed"), key=lambda item: item.get("closed_at") or "", reverse=True)
    return open_issues + (closed if all_closed else closed[:10]), blocked


def render_status(snapshot, all_closed=False, root=None):
    issues, blocked = visible_issues(snapshot, all_closed)
    dispatchers = dispatcher_processes(root)
    dispatcher = "running: " + "; ".join(dispatchers) if dispatchers else "not running"
    lines = ["ASCO task status", "Dispatcher: " + dispatcher, ""]
    waiting = [issue for issue in issues if needs_input(issue)]
    if waiting:
        lines.extend(["Tasks waiting for you:"])
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
    lines.extend(["", "Use `asco answer TASK_ID \"answer\"` to resume a task waiting for you."])
    return "\n".join(lines)


def log_tail(root, count=5):
    path = Path(root) / ".asco" / "logs" / "runner.log"
    try:
        with path.open(encoding="utf-8") as log:
            return log.read().splitlines()[-count:]
    except FileNotFoundError:
        return []


def answer_task(root, issue_id_value, text):
    bd = Beads(root)
    task = bd.show(issue_id_value)
    if not needs_input(task):
        raise CommandError("%s is not waiting for user input" % issue_id_value)
    bd.comment(issue_id_value, "User answer: %s" % text)
    bd.run("update", issue_id_value, "--status", "open", "--unset-metadata", "asco_needs_input")


class DashboardController:
    def __init__(self, snapshot_reader, renderer, screen, delay):
        self.snapshot_reader = snapshot_reader
        self.renderer = renderer
        self.screen = screen
        self.delay = delay

    def run(self):
        show_all = False
        snapshot = self.snapshot_reader()
        while True:
            self.screen.draw(self.renderer(snapshot, show_all))
            key = self.screen.getch()
            if key == ord("q"):
                self.snapshot_reader()
                return
            if key == 27:
                return
            if key == ord("c"):
                show_all = not show_all
                snapshot = self.snapshot_reader()
                continue
            self.delay()


class CursesDashboardScreen:
    def __init__(self, screen, root):
        self.screen = screen
        self.root = root

    def draw(self, status):
        self.screen.erase()
        split = max(45, int(curses.COLS * 0.62))
        for row, line in enumerate(status.splitlines()):
            if row < curses.LINES - 1:
                self.screen.addnstr(row, 0, line, split - 1)
        if split < curses.COLS - 15:
            self.screen.vline(0, split, curses.ACS_VLINE, curses.LINES - 1)
            self.screen.addnstr(0, split + 2, "Dispatcher log", curses.COLS - split - 3)
            lines = log_tail(self.root)
            if not lines:
                lines = ["No dispatcher output yet."]
            for row, line in enumerate(lines, start=2):
                if row < curses.LINES - 1:
                    self.screen.addnstr(row, split + 2, line, curses.COLS - split - 3)
        self.screen.addnstr(curses.LINES - 1, 0, "q: quit   c: toggle all closed", curses.COLS - 1)
        self.screen.refresh()

    def getch(self):
        return self.screen.getch()


def dashboard(root):
    def draw(screen):
        curses.curs_set(0)
        screen.nodelay(True)
        controller = DashboardController(
            lambda: status_snapshot(root),
            lambda snapshot, show_all: render_status(snapshot, show_all, root),
            CursesDashboardScreen(screen, root),
            lambda: time.sleep(0.25),
        )
        controller.run()
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
    answer = commands.add_parser("answer", help="record an answer and resume a blocked task")
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
        print(render_status(status_snapshot(root), args.all_closed, root))
    elif args.command == "dashboard":
        dashboard(root)
    else:
        answer_task(root, args.issue, args.text)
        print("The answer was recorded and the task was reopened.")


if __name__ == "__main__":
    try:
        main()
    except CommandError as error:
        print("asco: %s" % error, file=sys.stderr)
        sys.exit(1)

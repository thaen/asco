# Asco: A Software Company

Asco is a small terminal dispatcher for one software-engineering agent. It stores work in [Beads](https://github.com/gastownhall/beads), starts short-lived Codex sessions for ready work, and presents the CEO with a terminal queue.

## The first loop

```text
CEO task → Engineer Bead → Audit Bead → corrections and successor audit → pull request
```

Every submitted task creates an Engineer Bead and a dependent Audit Bead. The Audit Bead is a child of its Engineer Bead, so Beads blocks it until its parent closes. The runner claims one ready Engineer or Audit Bead and gives it to Codex. The agent uses the `asco` commands to leave status, create child tasks, block on dependencies, escalate to the CEO, and record completion evidence.

The runner uses Codex automatic-approval mode inside a dedicated task worktree. It never marks a task complete merely because Codex exits.

## Installation

```sh
brew install beads
bd init --prefix asco
```

## Commands

```sh
python3 -m asco.cli submit "Describe the task" "Estimate and break this request into deliverable work."
python3 -m asco.cli run
python3 -m asco.cli tui
```

The TUI uses `r` to start the runner, `s` to stop the runner, `e` to resolve the first CEO escalation, and `q` to quit.

The first pilot intentionally runs one Engineer subprocess at a time.

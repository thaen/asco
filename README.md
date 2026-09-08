# Asco: A Software Company

Asco is a small terminal dispatcher for one software-engineering agent. It stores work in [Beads](https://github.com/gastownhall/beads), starts short-lived Codex sessions for ready work, and presents the CEO with a terminal queue.

## The first loop

```text
CEO task → Engineer Bead → Audit Bead → corrections and successor audit → pull request
```

Every engineering task has an Audit child Bead. The Audit Bead is a child of its Engineer Bead, so Beads blocks it until its parent closes. The runner claims one ready Engineer or Audit Bead and gives it to Codex. The agent uses native `bd` commands to leave status, create child tasks, block on dependencies, escalate to the CEO, and record completion evidence.

The runner gives Codex workspace-write access to the task worktree and the shared `.beads` directory. It never marks a task complete merely because Codex exits.

## Installation

```sh
brew install beads
bd init --prefix asco
```

## Commands

```sh
bd create "Describe the task" --type engineering --description "Estimate and break this request into deliverable work."
python3 -m asco.cli run
python3 -m asco.cli tui
```

The TUI is a read-only status view that refreshes every second. Run the dispatcher and native `bd` commands from another terminal, and press `q` to quit the TUI.

The first pilot intentionally runs one Engineer subprocess at a time.

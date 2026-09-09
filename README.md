# Asco: A Software Company

Asco is a small terminal dispatcher for one software-engineering agent. It stores work in [Beads](https://github.com/gastownhall/beads), starts short-lived Codex sessions for ready work, and presents the CEO with a terminal queue.

## The first loop

```text
CEO task → Engineer Bead → Audit Bead → corrections and successor audit → pull request
```

Every engineering task has an Audit child Bead. The Audit Bead is a child of its Engineer Bead, so Beads blocks it until its parent closes. The runner gives an Audit the audited Engineer worktree before merge. When an Audit finds a defect, it creates correction work and a successor Audit beneath the already-closed Engineer Bead, then closes; it never waits for work that it created. The runner claims one ready Engineer or Audit Bead and gives it to Codex. The agent uses native `bd` commands to leave status, create child tasks, block on dependencies, escalate to the CEO, and record completion evidence.

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
python3 -m asco.cli respond asco-123
```

The native `bd create` command is the submission path. Create an `engineering` Bead for a new CEO request, and the assigned Engineer creates its Audit child after it has examined the work.

The `respond` command prompts for a CEO decision, records it on the escalation, closes the escalation, and reopens its blocked task. You can also supply `--message "The decision"` for a scripted response.

## The tmux workspace

Run `scripts/asco-tmux` from the project root to start a session named `asco-company`. Its four equal panes are recorded by role: the upper-left pane runs the dispatcher, the upper-right pane runs the TUI, the lower-left pane is a normal shell for `bd` commands, and the lower-right pane is the log pager. Pass a different session name as the first argument when needed.

Run `scripts/asco-restart` to replace the dispatcher and TUI panes with processes from the current worktree, without creating a new tmux session. Pass the session name as its first argument when needed.

The TUI redraws when terminal input arrives, when its background reader replaces the cached Beads snapshot, or when its terminal pane changes size. The background reader refreshes that snapshot once per second. Use the up and down arrows to select an issue, press `l` or `L` to open its output with `less` in the logs pane, and press `q` to quit the TUI.

## Tests

Install the test tools with `python3 -m pip install -e '.[test]'`, then run `PYTHONPATH=. python3 -m unittest discover -s tests -v`. The suite includes pseudo-terminal tests that use `pexpect` and a terminal emulator to verify real arrow-key, log-key, resize, and quit behavior.

The first pilot intentionally runs one Engineer subprocess at a time.

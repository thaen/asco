# Asco

Asco is a local dispatcher for Beads tasks in one Git repository. It claims ready work tasks of any ordinary Beads type, gives each task an isolated Git worktree, and starts an Engineer through `codex exec`.

Run the dispatcher from the repository that has the Beads database.

```sh
./asco run --parallel 2
```

The command returns after it starts the dispatcher. Worker process records are Beads metadata with keys that start with `asco_`; worker output is in `.asco/logs/<bead-id>.log`.

Use the status view for a plain terminal report or the dashboard for a refreshing terminal interface.

```sh
./asco status
./asco dashboard
./asco answer ASCO-42 "Use the first option because it preserves the API."
```

The dashboard watches `src/asco.py` while it runs. A source update closes the
current curses screen and replaces the dashboard process with the updated code,
so the dashboard returns without a separate terminal restart.

The dashboard selects its first visible task by default. You can use the arrow
keys or `j` and `k` to move the selection, `Enter` or `d` to open task details,
and `l` to open the selected task's worker log. The detail view shows the full
description and blocker IDs, and `Escape` returns to the preceding view.

The editable worker frameworks are `prompts/engineer.md` and `prompts/audit.md`. Set `asco_prompt=audit` on an ordinary task to append the Audit framework to its Engineer prompt. Label an ordinary task `asco:integration` when its Engineer must merge named work branches into the default branch; Asco runs one Integration task at a time.

The dispatcher needs an initialized Beads database, Git worktree support, and an authenticated Codex CLI. The test suite uses only the Python standard library.

```sh
python3 -m unittest discover -s tests -v
```

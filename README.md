# Asco

Asco is a local dispatcher for Beads tasks in one Git repository. It claims open Epics and ready child tasks, gives each task an isolated Git worktree, and starts an Engineer through `codex exec`.

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

The dispatcher needs an initialized Beads database, Git worktree support, and an authenticated Codex CLI. The test suite uses only the Python standard library.

```sh
python3 -m unittest discover -s tests -v
```

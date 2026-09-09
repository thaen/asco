ASCO, short for "A Software Company", is a system allowing a user to create tasks intended for AI agents to execute, view status on those agents' execution, and respond to escalations. I intend this to be like Yegge's Gastown, but with fewer moving parts and fewer (in my opinion) unnecessary opinions.

We are implementing several of the coordination patterns from https://github.com/gastownhall/beads/blob/main/docs/multi-agent/coordination.md, read that before beginning. 

## Implementation discretion

This document defines product behavior and workflow invariants. The Engineer may choose ordinary implementation details, including programming language, UI library, polling interval, log format, process-record format, and testing structure, when those choices preserve the behavior described here.

The Engineer must raise a question only when a choice would change user-visible behavior, task lifecycle semantics, data ownership, security boundaries, or the stated workflow.

## Usage: Task submission

Start `asco` in a directory of your choice. `asco run` is nonblocking:

```
mkdir all-projects
cd all-projects
asco run --parallel X
```

Users submit tasks by creating beads in the "ready" state in a specific directory:

```
cd all-projects
bd create "Title" --type epic --description "Read file X and do what it says, or other detailed task description."
```

`asco` polls the ready queue and starts agents using `codex -p` with instructions to work on specific tasks, using the coordinator patterns from the Beads coordination document linked above.. It starts new tasks until there are X running tasks, then waits for one to finish before starting the next one.

Spawned agents have their own worktrees. 

When spawned, Beads is updated with metadata about the Engineer that is working on the task. For instance, its pid (via `--set-metadata pid=12345`). 

## Usage: Status reporting

A UI allows a user to view the state of all beads tasks and their status. The built-in statuses are open, in_progress, blocked, deferred, closed, pinned, and hooked. Blocked tasks have their blocking task IDs shown. The UI shows the most recent 10 Done tasks with an option to view All tasks in that state.

It can be a Terminal UI built with Python, tested with Pyte and Pexpect, or it can be a WebUI with no back-end (TamperMonkey is OK if needed). The initial Engineer is empowered to make the implementation decision based on which UI is easier and faster to test, which I suspect is a Terminal UI.

## Beads interaction

Shelling out the `bd` tool should rarely be necessary for reads. We query Beads with SQL wherever possible via `bd` or directly against the database.

For writes, we should prefer shelling out to `bd` unless there exists a better interface. For instance, the `--claim` operation should probably be done with `bd`, not directly via the database.

## Worker prompting: Persona

To start with, there is only one persona: "Engineer". The Engineer is an expert software development engineer. As a tenured engineer, he understands that all software has tremendous cost in maintenance and testing. He minimizes the code he writes through judicious re-use of existing software.

The Engineer understands that they do not work alone. They escalate important decisions. They delegate tasks to other Engineers, such as test authoring, investigation and research, and more. They share their findings in the company Wiki. 

## Worker prompting: SDLC

Users submit Epics by creating Beads with type `epic`. Epics are considered to
be software engineering work.

When Asco claims an Epic, it starts an Engineer to break down the work. The
Epic becomes `in_progress` when it is claimed and remains `in_progress` while
its child tasks execute. An in-progress Epic represents work that is underway,
even when no Engineer is currently decomposing it.

The Engineer creates as many child tasks as the work requires in these
categories. Every child task uses `--parent EPIC_ID`.

1. High-level tests: Write a reasonable number of end-to-end tests for the requested feature. They should be failing when this task is completed.
2. Implementation: Implement the feature. The tests should be passing when this task is completed. 
3. Test: More thoroughly test the feature, especially focused on details, logic, and failure paths. The build should pass when this step is completed.
4. Audit: Compare the original task that the user submitted (its text is included in this task) and what was implemented. Make sure the build succeeds. Compare the tests to what the user requested. Tests should exist for the features that the user requested. If this step fails, file another task identical to this one, then file blocking correction tasks based on the audit results.
5. Merge: When finished, an Engineer is tasked with merging the worktree back to main, building, running tests, and finally deleting the worktree when finished. Only one merge can happen at a time.

For a given Epic, child tasks use `blocks` dependencies to express their
required order. Implementation tasks must not start before their required
high-level test tasks have closed. Test tasks must not start before their
required implementation tasks have closed. The Merge task must wait for every
task whose work it integrates.

Child tasks do not block their Epic. The `parent-child` relationship keeps the
work grouped under the Epic, and Beads prevents the Epic from closing while it
has open child tasks.

Asco dispatches open Epics for decomposition and ready non-Epic tasks for
ordinary work. An in-progress Epic does not appear in the ready queue, while
its ready child tasks do appear.

After a child task closes, Asco checks its parent Epic. When every child task
has closed, including the Merge task, Asco closes that Epic by ID.

Engineers may create further tasks as they learn about the work. A task that
belongs to the current feature is created as another child of the current Epic
with any required `blocks` dependencies. An independent follow-up task uses a
`discovered-from` dependency. A large independent effort may be created as a
new Epic and follows the same lifecycle.

## Engineer communication

Engineers communicate between tasks by using comments on tasks and referencing task IDs when needed. For instance, when decomposing the original user-requested task, the Engineer may file the tasks in reverse order, so that it can add instructions "leave comments on the next tickets as you learn important things".

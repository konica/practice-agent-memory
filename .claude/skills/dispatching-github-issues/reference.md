# Dispatching tickets

`dispatch-issues.sh` hands open GitHub issues to headless Claude Code
agents — one agent, one worktree, one branch, one PR per ticket — running as
many of them at once as the dependency graph allows.

Nothing about the ticket set is baked into the script. It reads the issues from
GitHub every wave, so tickets added after it was written are picked up with no
edit, and no issue number is ever written down anywhere.

Run it from inside the checkout you want the work to happen in. `SKILL` below
stands for this skill's directory.

## What makes a ticket ready

A ticket is dispatchable when all of these hold:

- it is **open**, and carries every `--label` you asked for;
- none of its **blockers are still open**;
- it is **unassigned** (an assignee means a human took it — use `--force` to
  override);
- **no branch exists** for it yet, locally or on `origin`.

Blockers come from two places:

1. **The issue body** — any line containing `Depends on #3, #4` or
   `Blocked by: #3`, up to the sentence's end. The recognised phrases default to
   `depends on|blocked by|requires|needs` and are configurable with
   `--dep-words`, since trackers word this differently.
2. **GitHub's native issue dependencies**, when set. A ticket whose
   `issue_dependencies_summary.blocked_by` is non-zero is held back. Turn this
   off with `--no-native-deps`.

## How parallelism and stacking work

The script dispatches in **waves**. Wave 1 is every ready ticket; they run
concurrently, capped by `--jobs`. When the wave finishes, readiness is
recomputed and the next wave goes out, until nothing is left.

A blocker counts as cleared once its agent has **succeeded in this run** — not
only when the issue is closed. Waiting for a merge would stall the whole run
behind code review.

That raises an obvious problem: ticket #2's code depends on #1's code, but #1
is not on `main` yet. So dependent work is **stacked**. #2's worktree is branched
from `agent/issue-1`, not from `main`, and its PR targets `agent/issue-1` too,
keeping the diff honest. With several blockers, the first is the base and the
rest are merged in; a merge conflict fails that ticket rather than guessing.
`--no-stack` turns this off and branches everything from `--base`.

Check the shape of a run before committing to it:

```bash
"$SKILL/dispatch-issues.sh" --dry-run
```

## Running it

```bash
# The whole open backlog, three agents at a time
"$SKILL/dispatch-issues.sh" --jobs 3

# Only tickets your triage marks as agent-ready
"$SKILL/dispatch-issues.sh" --label ready-for-agent

# Just the currently-unblocked tickets, then stop and look
"$SKILL/dispatch-issues.sh" --one-wave

# Specific tickets
"$SKILL/dispatch-issues.sh" 41 42
```

`--help` lists every flag. The ones that matter most:

| Flag                 | Why                                                       |
| -------------------- | --------------------------------------------------------- |
| `-j, --jobs N`       | concurrent agents (default 3)                              |
| `-n, --dry-run`      | print the wave plan, change nothing                        |
| `--one-wave`         | dispatch what's ready now, then stop                       |
| `-m, --model NAME`   | model for the agents                                       |
| `--timeout SECS`     | kill an agent that won't finish                            |
| `--no-pr`            | push branches, skip pull requests                          |
| `--no-push`          | keep everything local (implies `--no-pr`)                  |
| `--agent-cmd CMD`    | run CMD instead of `claude`; the prompt arrives on stdin   |
| `-r, --repo O/N`     | target repository, when `gh` cannot infer it               |
| `--dep-words RE`     | phrases that introduce a blocker in an issue body          |
| `--worktree-root D`  | put worktrees somewhere other than the repo                |

## What each agent gets

A prompt built from the issue title and body, plus a working agreement: read
whatever agent instructions the repository carries (`CLAUDE.md`, `AGENTS.md`,
`CONTRIBUTING.md`, a `docs/` guide), implement only this ticket, write and run
the tests the ticket names, commit on the branch, and **do not push, open a PR,
or switch branches** — the dispatcher does all of that. Agents are told to stop
and report rather than guess when a ticket is under-specified, which is what a
ticket that gates other work needs them to do.

Override the template with `--prompt-file`; placeholders are `{{NUMBER}}`,
`{{TITLE}}`, `{{BODY}}`, `{{BRANCH}}`, `{{BASE}}`, `{{REPO}}`.

## After a ticket

On success the script commits anything the agent left uncommitted, pushes
`agent/issue-<n>`, opens a PR whose body says `Closes #<n>`, comments the PR
link on the issue, and assigns the issue to you. A ticket that fails, produces
no commits, or cannot push is reported and leaves its dependents unattempted —
they show as `NOT REACHED` in the summary.

Everything lands under `.dispatch/<timestamp>/`: `logs/issue-<n>.log` has the
full agent transcript, `prompts/issue-<n>.md` the exact prompt sent. The
directory is gitignored. Worktrees stay at
`.claude/worktrees/dispatch/issue-<n>` in the target repo unless you pass
`--cleanup`.

The exit code is 0 only when every runnable ticket succeeded.

## Known environment quirk: worktrees on a mounted filesystem

On some mounted paths (a `/c/...` host mount in a sandbox, network shares), a
process that writes a file
into its own working directory can no longer read that directory back:
`getcwd()` fails and git reports

```
fatal: Unable to read current working directory
```

Agents run `write a file, then git add` constantly, so this matters. The script
probes the worktree root at startup and warns when it lands on such a
filesystem. The fix is to put the worktrees on a local path:

```bash
"$SKILL/dispatch-issues.sh" --worktree-root "$HOME/dispatch-worktrees"
```

The dispatcher's own git calls are unaffected — they all use `git -C <path>`,
which never consults the working directory.

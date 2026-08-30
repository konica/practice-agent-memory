---
name: dispatching-github-issues
description: Use when a GitHub backlog should be worked by coding agents rather than by hand — handing tickets to agents, working several issues at once, or working out which tickets can proceed in parallel right now.
---

# Dispatching GitHub Issues

## Overview

One ticket, one agent, one worktree, one branch, one PR. `dispatch-issues.sh`
(next to this file) reads the backlog from GitHub at run time and derives the
order from the tickets themselves, so no issue number is ever written down and
tickets added later are picked up on the next run.

## When to use

- Several specified tickets are waiting and a human would work them one by one.
- Someone asks to "dispatch the tickets" or "run the backlog in parallel".
- You need to know which tickets *could* proceed in parallel right now.

**Not for:** a single ticket (just work it); tickets too vague to hand off
(triage first); work needing a human to decide things mid-task.

Requires `gh` (authenticated), `git`, `jq`, `claude`. Run it from inside the
target checkout.

## Quick start

`$SKILL` is this skill's directory.

```bash
bash "$SKILL/dispatch-issues.sh" --dry-run     # ALWAYS read the plan first
bash "$SKILL/dispatch-issues.sh" --jobs 3      # then dispatch
```

| Flag | Use |
| --- | --- |
| `-n, --dry-run` | print the wave plan, change nothing |
| `-j, --jobs N` | agents at once (default 3) |
| `--one-wave` | dispatch what's ready now, then stop and look |
| `-l, --label L` | restrict to triaged tickets, e.g. `ready-for-agent` |
| `--worktree-root D` | worktrees on a local filesystem (see Mistakes) |
| `--agent-cmd CMD` | run CMD instead of `claude` — how you rehearse a run |
| `--dep-words RE` | phrases introducing a blocker, if the tracker differs |

`--help` for the rest; `reference.md` for the full guide.

## How the ordering works

Dispatchable = **open**, has any labels you asked for, **unassigned**, **no
branch yet**, **no open blockers**. Blockers come from the issue body
(`Depends on #3, #4`, `Blocked by: #3`) and from GitHub's native dependencies.
Everything unblocked goes out at once; when the wave ends, readiness is
recomputed and the next wave goes.

Two consequences to know before running it:

- A blocker clears when its **agent succeeds**, not when the issue closes —
  waiting for a merge would stall the run behind code review.
- So dependent work is **stacked**: #4 branches from `agent/issue-3` and its PR
  targets that branch, because #3 isn't on the trunk yet. Merge the stack
  bottom-up. `--no-stack` opts out.

A dispatchable ticket has one deliverable, names its tests, declares blockers
on a `Depends on #N` line, and says what to do when an assumption fails ("if X
fails, stop and report"). Without that, agents guess.

## Common mistakes

| Mistake | What happens |
| --- | --- |
| Skipping `--dry-run` | You learn the graph was wrong after 12 agents ran |
| Dispatching untriaged tickets | Agents invent the spec; use `--label` |
| Expecting wave 2 to wait for merge | It doesn't — you get a stack of PRs to merge in order |
| Worktrees on a mounted path (`/c/...`, network shares) | Agents hit `fatal: Unable to read current working directory`; pass `--worktree-root "$HOME/dispatch-worktrees"`. The script probes and warns |
| `--force` on assigned tickets | You dispatch on top of a human's work |
| Trusting the exit code alone | Read `.dispatch/<timestamp>/logs/`; an agent that stopped and reported is a useful result, not a failure |

Per-ticket status is `ok`, `failed`, `no-changes`, or `NOT REACHED` (its blocker
failed). The transcript is in `.dispatch/<timestamp>/logs/issue-<n>.log` and the
prompt sent in `prompts/issue-<n>.md` — read both before trusting a PR.

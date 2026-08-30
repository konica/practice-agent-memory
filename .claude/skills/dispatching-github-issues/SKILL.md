---
name: dispatching-github-issues
description: Use when a GitHub backlog should be worked by coding agents rather than by hand — handing tickets to agents, working several issues at once, steering an agent that is mid-ticket, or working out which tickets can proceed in parallel right now.
---

# Dispatching GitHub Issues

## Overview

One ticket, one agent, one worktree, one branch, one PR. `dispatch-issues.sh`
(next to this file) reads the backlog from GitHub at run time and derives the
order from the tickets themselves, so no issue number is ever written down and
tickets added later are picked up on the next run.

Each ticket gets its **own attachable session**, so you can watch an agent work
and steer it toward a goal the ticket doesn't spell out.

## When to use

- Several specified tickets are waiting and a human would work them one by one.
- Someone asks to "dispatch the tickets" or "run the backlog in parallel".
- You need to know which tickets *could* proceed in parallel right now.

**Not for:** a single ticket (just work it); tickets too vague to hand off
(triage first).

Requires `gh` (authenticated), `git`, `jq`, `claude`. Run it from inside the
target checkout.

## The loop

`$SKILL` is this skill's directory.

```bash
bash "$SKILL/dispatch-issues.sh" --dry-run   # 1. read the plan first
bash "$SKILL/dispatch-issues.sh"             # 2. launch a session per ticket
claude attach <id>                           # 3. steer any of them, any time
bash "$SKILL/dispatch-issues.sh" --status    # 4. who is working, who is done
bash "$SKILL/dispatch-issues.sh" --land      # 5. push branches, open PRs
bash "$SKILL/dispatch-issues.sh"             # 6. again for the next wave
```

Step 2 prints the attach command for every session it started. Sessions stay
alive after finishing a turn, so you can attach, redirect, and let the agent
keep going — then land it.

| Flag | Use |
| --- | --- |
| `-n, --dry-run` | print the wave plan, change nothing |
| `--status` | each dispatched ticket: session id, state, commits |
| `--land` | land finished sessions (push + PR); refuses ones mid-turn |
| `-j, --jobs N` | how many tickets to launch at once (default 3) |
| `-l, --label L` | restrict to triaged tickets, e.g. `ready-for-agent` |
| `--worktree-root D` | worktrees on a local filesystem (see Mistakes) |
| `--mode print` | headless agents instead: run to completion, land, chain waves |
| `--dep-words RE` | phrases introducing a blocker, if the tracker differs |

`--help` for the rest; `reference.md` for the full guide.

## How the ordering works

Dispatchable = **open**, has any labels you asked for, **unassigned**, **no
branch yet**, **no open blockers**. Blockers come from the issue body
(`Depends on #3, #4`, `Blocked by: #3`) and from GitHub's native dependencies.

A blocker clears once its **branch carries work** — not when the issue closes.
Waiting for a merge would stall everything behind code review. So dependent
work is **stacked**: #4 branches from `agent/issue-3` and its PR targets that
branch, because #3 isn't on the trunk yet. Merge the stack bottom-up.
`--no-stack` opts out.

Session mode launches **one wave per invocation** — the previous wave's agents
are still alive, so there is nothing to chain. Run it again once you've landed
a wave and the next one is ready.

A dispatchable ticket has one deliverable, names its tests, declares blockers
on a `Depends on #N` line, and says what to do when an assumption fails ("if X
fails, stop and report"). Without that, agents guess.

## Common mistakes

| Mistake | What happens |
| --- | --- |
| Skipping `--dry-run` | You learn the graph was wrong after 12 agents ran |
| Waiting for PRs to appear on their own | Session mode never opens one until you `--land`; that is the point |
| Dispatching untriaged tickets | Agents invent the spec; use `--label` |
| Expecting the next wave to wait for merge | It doesn't — you get a stack of PRs to merge in order |
| Worktrees on a mounted path (`/c/...`, network shares) | Agents hit `fatal: Unable to read current working directory`; pass `--worktree-root "$HOME/dispatch-worktrees"`. The script probes and warns |
| `--force` on assigned tickets | You dispatch on top of a human's work |
| Judging a ticket without reading its session | An agent that stopped and reported is a useful result, not a failure |

`--status` reports `working`, `done`, or `gone`; after `--land`, a ticket reads
`landed`, `no-changes`, or `still working`. Per-run logs and the exact prompt
sent live in `.dispatch/<timestamp>/`.

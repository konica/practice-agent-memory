# practice-agent-memory

## Agent skills

### Issue tracker

Issues live in GitHub Issues on `konica/practice-agent-memory`, via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

The five canonical triage roles, each label string equal to its name. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `CONTEXT.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.

### Dispatching tickets

Working the backlog with agents is the `dispatching-github-issues` skill
(`.claude/skills/dispatching-github-issues/`). Tickets declare their blockers
with a `Depends on #N` line, which is what the dispatcher orders them by.

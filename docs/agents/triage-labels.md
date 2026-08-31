# Triage Labels

The skills speak in terms of five canonical triage roles. This file maps those roles to the actual label strings used in this repo's issue tracker.

| Label in mattpocock/skills | Label in our tracker | Meaning                                  |
| -------------------------- | -------------------- | ---------------------------------------- |
| `needs-triage`             | `needs-triage`       | Maintainer needs to evaluate this issue  |
| `needs-info`               | `needs-info`         | Waiting on reporter for more information |
| `ready-for-agent`          | `ready-for-agent`    | Fully specified, ready for an AFK agent  |
| `ready-for-human`          | `ready-for-human`    | Requires human implementation            |
| `wontfix`                  | `wontfix`            | Will not be actioned                     |

When a skill mentions a role (e.g. "apply the AFK-ready triage label"), use the corresponding label string from this table.

Edit the right-hand column to match whatever vocabulary you actually use.

Every open issue also carries exactly one **category** label, `bug` or `enhancement`, alongside its state label. The domain labels (`backend`, `frontend`, `agent`, `security`) are additive and unrelated to triage.

## Blocking

`blocked` and `blocker` are opposites. Both are in use here, so keep them straight:

| Label     | Applied to                                            |
| --------- | ----------------------------------------------------- |
| `blocked` | A ticket that has at least one OPEN blocker            |
| `blocker` | A ticket that gates other work (e.g. a required gate)  |

A ticket is `ready-for-agent` when its **spec** is complete, and `blocked` when it **cannot start yet**. These are independent axes and a ticket routinely carries both. Dispatch only picks up a ticket that is `ready-for-agent`, unassigned, and has no open blocker.

Declaring a blocker in body text alone is not enough — it is invisible in list views and to the GitHub UI. Every blocking edge must **also** exist as a native GitHub dependency:

```bash
gh api --method POST repos/<owner>/<repo>/issues/<blocked>/dependencies/blocked_by \
  -F issue_id="$(gh api repos/<owner>/<repo>/issues/<blocker> --jq .id)"
```

`issue_id` is the blocker's numeric **database id**, not its `#number` and not its `node_id`.

## Keeping the board honest

A `blocked` label starts lying the moment its blocker merges, so reconcile it rather than trusting it:

```bash
~/.claude/skills/dispatching-github-issues/triage-lint.sh        # check only; exits 1 on violations
~/.claude/skills/dispatching-github-issues/triage-lint.sh --fix  # repair stale labels and missing native edges
```

It enforces only what can be computed: one state label and one category label per open issue, `blocked` matching the real graph, every text-declared blocker mirrored as a native dependency, and no dependency cycles. Whether something is `ready-for-agent` or `ready-for-human` remains a judgment call for the triager.

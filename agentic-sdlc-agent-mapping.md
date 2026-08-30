# Agentic SDLC — Agent & Workflow Mapping

Maps the 6 phases in `image.png` ("Agentic Workflow across the SDLC") to real, existing agents/plugins from five catalog repos, and to four development-workflow frameworks that structure *how* those agents get invoked.

## Source legend

**Agent/plugin catalogs**
| Code | Source | Format |
|---|---|---|
| VA | [VoltAgent/awesome-claude-code-subagents](https://github.com/VoltAgent/awesome-claude-code-subagents) | Persona/system-prompt `.md` files — adapt into a subagent yourself |
| AA | [msitarzewski/agency-agents](https://github.com/msitarzewski/agency-agents) | Persona/system-prompt files (270 agents, broad business scope) — adapt yourself |
| WS-A | [wshobson/agents](https://github.com/wshobson/agents) `docs/agents.md` | Persona/system-prompt files — adapt yourself |
| WS-P | [wshobson/agents](https://github.com/wshobson/agents) `docs/plugins.md` | **Installable Claude Code plugins** (agents+commands+hooks bundled) |
| AC | [anthropics/claude-code](https://github.com/anthropics/claude-code) `/plugins` | **Official, installable Claude Code plugins** |

**Workflow frameworks**
| Code | Source | Kind |
|---|---|---|
| SP | [obra/superpowers](https://github.com/obra/superpowers) | Skill library with enforced sequencing — **already active this session** |
| ECC | [affaan-m/ECC](https://github.com/affaan-m/ECC) | Full methodology + bundled 67-agent fleet |
| MP | [mattpocock/skills](https://github.com/mattpocock/skills) | Skill library (same genre as SP, different author) |
| SK | [github/spec-kit](https://github.com/github/spec-kit) | Slash-command spec-driven development toolkit |

"Usable now" = installable/runnable in Claude Code today with no rewriting. Persona-only entries need to be copied into a subagent `.md` file (frontmatter + system prompt) before use.

---

## Phase 1 — Requirements & Ideation

Diagram roles: *Architecture Agent, Research Agent*

| Agent/Plugin | Source | Usable now? | Fit |
|---|---|---|---|
| `brainstorming` skill | SP | ✅ Yes (active) | Structured clarify-then-design dialogue before any code — directly covers this phase |
| `/speckit.constitution`, `/speckit.specify`, `/speckit.clarify` | SK | ✅ Yes (installable CLI) | Formal requirements/governance artifacts; `clarify` explicitly resolves ambiguity before planning |
| `to-spec`, `grill-with-docs`/`grilling`, `research` skills | MP | ✅ Yes (Claude Code skill format) | `to-spec` turns conversation into a spec; `grilling` is an adversarial interview to sharpen it |
| `research-analyst`, `business-analyst`, `market-researcher`, `project-idea-validator` | VA (Research & Analysis / Business & Product) | ⚠️ Adapt as subagent | Good Research-Agent stand-ins once wrapped |
| `business-analyst` | WS-A | ⚠️ Adapt as subagent | Requirements-gathering persona |

**Recommendation**: `brainstorming` (already running) + spec-kit's `specify`/`clarify` for a written artifact trail.

---

## Phase 2 — Planning & Backlog

Diagram roles: *Backlog Agent, Planning Agent, Domain Expert Agent*

| Agent/Plugin | Source | Usable now? | Fit |
|---|---|---|---|
| `writing-plans`, `using-git-worktrees` | SP | ✅ Yes (active) | Decomposes spec into a step-by-step plan; isolates workspace |
| `wayfinder`, `to-tickets`, `triage`, `domain-modeling`/`codebase-design` | MP | ✅ Yes | **Patches a real gap** — superpowers has no ticket/backlog layer; `domain-modeling` is the closest thing to a "Domain Expert Agent" |
| `/speckit.plan`, `/speckit.tasks` | SK | ✅ Yes | Technical strategy + granular, parallelizable task list |
| Planner agent (stage 1 of Plan→Test→Implement→Review→Verify→Remember→Improve) | ECC | ⚠️ Bundled fleet install | Dedicated planning subagent |
| `backlog-grooming`, `product-manager`, `project-manager`, `scrum-master` | VA (Business & Product) | ⚠️ Adapt as subagent | Direct "Backlog Agent" analogs |
| `agent-teams`, `conductor` | WS-P | ✅ Yes (installable) | Multi-agent planning/coordination bundles |

**Recommendation**: keep `writing-plans`, add MP's `wayfinder`/`to-tickets`/`triage` for the missing backlog machinery.

---

## Phase 3 — Implementation

Diagram roles: *Dispatcher Agent, Coding Agents, Domain Expert Agents*

| Agent/Plugin | Source | Usable now? | Fit |
|---|---|---|---|
| `dispatching-parallel-agents`, `subagent-driven-development`, `executing-plans`, `test-driven-development`, `systematic-debugging` | SP | ✅ Yes (active) | `dispatching-parallel-agents` = **Dispatcher Agent**; the rest = Coding Agent loop (RED→GREEN→REFACTOR) |
| `implement`, `tdd`, `prototype`, `diagnosing-bugs`, `resolving-merge-conflicts` | MP | ✅ Yes | Same genre, adds prototyping and merge-conflict handling |
| Test/Implement stages + per-language reviewer subagents (Go/Python/TS/Java/Rust) | ECC | ⚠️ Bundled fleet install | Explicit per-language Domain Expert Agents |
| `/speckit.implement` | SK | ✅ Yes | Executes all tasks per spec/plan |
| `typescript-pro`, `python-pro`, `golang-pro`, `react-specialist`, `rust-engineer`, etc. (huge list) | VA (Language Specialists) | ⚠️ Adapt as subagent | Pick per your actual stack — best source for narrow **Domain Expert / Coding Agents** |
| `backend-development`, `frontend-mobile-development`, `python-development`, `javascript-typescript`, `jvm-languages`, `systems-programming` | WS-P | ✅ Yes (installable) | Stack-specific coding-agent plugin bundles |
| `ralph-wiggum` (autonomous iteration loops) | AC | ✅ Yes (official) | Alternative **Dispatcher Agent** for long-running autonomous loops |

**Recommendation**: SP's dispatch/TDD skills as backbone; pull 2-3 VA language-specialist personas matching your actual stack rather than installing broadly.

---

## Phase 4 — Code Review & Quality

Diagram roles: *Review Agents, Static Analysis Agent*

| Agent/Plugin | Source | Usable now? | Fit |
|---|---|---|---|
| `code-review` plugin (5 parallel Sonnet agents: compliance, bugs, historical context, PR history, comments) | AC | ✅ Yes (official) | **Standout pick** — real, maintained, directly installable |
| `pr-review-toolkit` (`comment-analyzer`, `pr-test-analyzer`, `silent-failure-hunter`, `type-design-analyzer`, `code-reviewer`, `code-simplifier`) | AC | ✅ Yes (official) | Deep multi-angle PR review, covers Static Analysis role well |
| `requesting-code-review`, `receiving-code-review` | SP | ✅ Yes (active) | Review-discipline skills, pairs with the above |
| `code-review` skill (two-axis: Standards vs. Spec, parallel subagents) | MP | ✅ Yes | Distinct dual-axis review pattern |
| `comprehensive-review`, `performance-testing-review`, `security-scanning`, `security-compliance`, `backend-api-security`, `frontend-mobile-security` | WS-P | ✅ Yes (installable) | Security/perf-focused Static Analysis coverage |
| `code-reviewer`, `security-auditor`, `qa-expert`, `test-automator`, `performance-engineer`, `architect-reviewer` | VA (Quality & Security) | ⚠️ Adapt as subagent | Broader persona bench if plugins aren't enough |
| Review + Verify stages | ECC | ⚠️ Bundled fleet install | Fresh-context reviewer + automated build/lint/coverage checks |

**Recommendation**: Anthropic's `code-review` + `pr-review-toolkit` are the highest-leverage pick here — official, real, install today.

---

## Phase 5 — CI/CD & Deployment

Diagram roles: *Release Orchestrator, Pipeline Agent, Failure Analysis Agent*

This is the **weakest-covered phase** across all four workflow frameworks — none has a strong native deployment stage. Rely on catalog plugins/personas directly.

| Agent/Plugin | Source | Usable now? | Fit |
|---|---|---|---|
| `deployment-strategies`, `deployment-validation`, `kubernetes-operations`, `cloud-infrastructure`, `cicd-automation` | WS-P | ✅ Yes (installable) | Direct Pipeline/Release Orchestrator coverage |
| `distributed-debugging`, `error-diagnostics` | WS-P | ✅ Yes (installable) | **Failure Analysis Agent** |
| `deployment-engineer`, `devops-engineer`, `cloud-architect`, `kubernetes-specialist`, `terraform-engineer`, `incident-responder` | VA (Infrastructure) | ⚠️ Adapt as subagent | Broader persona bench |
| `finishing-a-development-branch` | SP | ✅ Yes (active) | Merge/PR/cleanup decision — thin but real overlap |
| `commit-commands` (`/commit-push-pr`) | AC | ✅ Yes (official) | Mechanical git/PR step, not a full pipeline agent |
| Verify stage (automated build/lint/type/coverage) | ECC | ⚠️ Bundled fleet install | Adjacent, not deployment-specific |

**Recommendation**: WS-P's `cicd-automation` + `deployment-strategies` are your best off-the-shelf Release Orchestrator/Pipeline Agent stand-ins; pair with `error-diagnostics` for Failure Analysis.

---

## Phase 6 — Operations & Feedback

Diagram roles: *Observability Agent, Incident Triage Agent*

| Agent/Plugin | Source | Usable now? | Fit |
|---|---|---|---|
| `incident-response`, `observability-monitoring`, `error-diagnostics` | WS-P | ✅ Yes (installable) | Direct match for both named roles |
| `sre-engineer`, `incident-responder`, `devops-incident-responder`, `performance-monitor`, `error-coordinator` | VA (Infrastructure / Meta & Orchestration) | ⚠️ Adapt as subagent | Broader persona bench |
| **Remember → Improve** stages | ECC | ⚠️ Bundled fleet install | Distills session patterns into reusable skills — **maps directly onto the diagram's dashed "feedback loop for continuous learning" arrow back to Phase 1** |
| **`/speckit.converge`** | SK | ✅ Yes (installable) | Assesses completed code against artifacts, appends remaining work as new tasks — **closes the same feedback loop**, feeding back into Planning & Backlog |
| — | SP | ❌ None | Superpowers has **no ops/feedback stage** — process stops at branch completion |

**Recommendation**: this is the one phase where the workflow frameworks (not just the agent catalogs) matter — ECC's Remember/Improve and spec-kit's `converge` are the only two things in this entire research that explicitly implement the diagram's feedback loop. Worth adopting one of them even if you don't use the rest of that framework.

---

## Gaps — no strong off-the-shelf match

- **"Domain Expert Agent"** (appears in Planning and Implementation phases): inherently project-specific — no generic catalog agent covers it. Closest analogs: MP's `domain-modeling`/`codebase-design` skills (Planning) or a hand-picked VA language specialist (Implementation). You will likely need to author a project-specific subagent here regardless.
- **CI/CD & Deployment** generally: no workflow framework (SP/ECC/MP/SK) has a real native stage for this — it's covered only by catalog plugins (WS-P, VA), not by any of the four methodologies.
- **Release Orchestrator** specifically: nothing in any source is a dedicated "orchestrator" for release gating/promotion decisions — WS-P's `deployment-strategies`/`deployment-validation` are the closest, but they're pipeline mechanics, not a gating decision-maker.

---

## Workflow-alignment: how the 4 frameworks map to the 6 phases

| Phase | SP (superpowers) | ECC | MP (mattpocock/skills) | SK (spec-kit) |
|---|---|---|---|---|
| Requirements & Ideation | `brainstorming` | — | `to-spec`, `grilling`, `research` | `constitution`, `specify`, `clarify` |
| Planning & Backlog | `writing-plans`, `using-git-worktrees` | Plan | `wayfinder`, `to-tickets`, `triage`, `domain-modeling` | `plan`, `tasks` |
| Implementation | `subagent-driven-development`/`executing-plans`, `test-driven-development` | Test, Implement | `implement`, `tdd`, `prototype`, `diagnosing-bugs` | `implement` |
| Code Review & Quality | `systematic-debugging`, `verification-before-completion`, `requesting`/`receiving-code-review` | Review, Verify | `code-review` (dual-axis), `improve-codebase-architecture` | `analyze` (static consistency only — thin) |
| CI/CD & Deployment | `finishing-a-development-branch` (thin) | — | — | — |
| Operations & Feedback | — (no stage) | **Remember, Improve** | `handoff` (session continuity, not full ops) | **`converge`** (loops back to backlog) |

**Notes:**
- SP and MP are symmetric "skill library" frameworks, strongest on Implementation/Code-Review; MP additionally covers the ticket/backlog layer SP lacks.
- ECC is the only framework with an explicit Ops & Feedback loop (Remember/Improve) and the largest declared agent roster (67 agents), but requires installing its full bundled fleet.
- SK is the only framework with a formal requirements-governance artifact (`constitution`) and an explicit loop-closure (`converge`), but is thinnest on Code Review (`analyze` is a consistency check, not a quality/security review).
- Since this session already runs **superpowers as the backbone**, the highest-value patches are: adopt SK's `constitution`/`clarify` to strengthen Phase 1 governance, MP's `wayfinder`/`to-tickets`/`triage` to fill the Phase 2 backlog gap, and either ECC's Remember/Improve concept or SK's `converge` to close the Phase 6 feedback loop that SP doesn't have.

---

## Quick-start set (minimal, low-redundancy)

1. **superpowers** — already active; backbone for Phases 1-4.
2. **Anthropic `code-review` + `pr-review-toolkit` + `commit-commands`** — official, install today, cover Phase 4-5 review/merge mechanics at the highest quality bar available.
3. **spec-kit** — adds Phase 1 governance (`constitution`/`clarify`) and closes the Phase 6 loop (`converge`).
4. **mattpocock/skills: `wayfinder`, `to-tickets`, `triage`** — fills the Phase 2 backlog gap superpowers doesn't cover.
5. **wshobson plugins: `cicd-automation` + `deployment-strategies`** (Phase 5) and **`incident-response` + `observability-monitoring`** (Phase 6).
6. **2-3 VoltAgent language-specialist personas** matched to your actual stack (e.g. `typescript-pro` + `python-pro`) — adapt as subagent `.md` files for Phase 3 Domain Expert / Coding Agent coverage.

This covers all 6 phases with one framework as backbone, official plugins for the highest-stakes phase (review), and targeted patches for the two biggest gaps (backlog, ops loop) — without installing hundreds of overlapping personas.

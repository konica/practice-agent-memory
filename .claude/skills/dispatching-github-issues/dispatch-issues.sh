#!/usr/bin/env bash
#
# dispatch-issues.sh — dispatch GitHub issues to headless Claude Code agents,
# in parallel where the dependency graph allows it.
#
# Readiness comes from the issue bodies: a line containing "Depends on #3, #4"
# or "Blocked by: #3" makes #3 and #4 blockers. GitHub's native issue
# dependencies are consulted too, when present.
#
# Independent tickets run concurrently, each in its own git worktree and
# branch. A ticket whose blocker is being worked on in the same run is
# branched off that blocker's branch (stacked), so it sees the blocker's code.
#
# See reference.md next to this script for the full guide.

set -euo pipefail

SCRIPT_NAME=${0##*/}

# ---------------------------------------------------------------- defaults --

JOBS=3
BASE=""
MODEL=""
PERMISSION_MODE="bypassPermissions"
LIMIT=500
LABELS=()
EXPLICIT=()
DRY_RUN=0
ONE_WAVE=0
MAX_WAVES=20
DO_PUSH=1
DO_PR=1
DO_ASSIGN=1
DO_COMMENT=1
DO_STACK=1
NATIVE_DEPS=1
FORCE=0
CLEANUP=0
TIMEOUT=0
BRANCH_PREFIX="agent/issue-"
WORKTREE_ROOT_OPT=""
AGENT_CMD=""
PROMPT_TEMPLATE_FILE=""
MODE=session
ACTION=dispatch
REPO_OPT=""
DEP_WORDS="depends on|blocked by|requires|needs"

usage() {
  cat <<HELPTEXT
$SCRIPT_NAME — dispatch GitHub issues to parallel Claude Code agents

USAGE
  $SCRIPT_NAME [options] [issue-number ...]

With no issue numbers, every open issue is a candidate (narrow with --label).

SELECTION
  -r, --repo OWNER/NAME which repository (default: inferred from the checkout)
  -l, --label LABEL     only issues carrying LABEL (repeatable, AND-ed)
      --limit N         issues to fetch from GitHub (default $LIMIT)
  -f, --force           dispatch even if assigned or the branch already exists

WHAT TO DO
      --mode MODE       session (default): one attachable background session
                        per ticket, which you can steer with 'claude attach'.
                        print: headless agent that runs to completion.
      --land            land finished sessions: push the branch, open the PR
      --status          show each dispatched ticket, its session and its state

EXECUTION
  -j, --jobs N          max tickets launched at once (default $JOBS)
  -n, --dry-run         print the wave plan and exit; changes nothing
      --one-wave        dispatch only the currently-ready tickets, then stop
      --max-waves N     cap on dependency depth (default $MAX_WAVES)
  -b, --base REF        base branch for the first wave (default: repo default)
      --no-stack        always branch from --base, never from a blocker branch
      --timeout SECS    kill an agent after SECS (0 = no limit)

AGENT
  -m, --model NAME      --model passed to claude (e.g. opus, sonnet)
      --permission-mode MODE   default $PERMISSION_MODE
      --agent-cmd CMD   run CMD instead of claude; prompt arrives on stdin,
                        cwd is the ticket worktree (useful for testing)
      --prompt-file F   prompt template; placeholders {{NUMBER}} {{TITLE}}
                        {{BODY}} {{BRANCH}} {{BASE}} {{REPO}}
      --branch-prefix P branch naming, default $BRANCH_PREFIX<number>
      --dep-words RE    alternation of phrases that introduce a blocker
                        reference in an issue body
                        (default: $DEP_WORDS)
      --worktree-root D where ticket worktrees live
                        (default .claude/worktrees/dispatch in the repo)

AFTER A TICKET
      --no-push         leave the branch local (implies --no-pr)
      --no-pr           do not open a pull request
      --no-assign       do not assign the issue to @me
      --no-comment      do not comment the outcome on the issue
      --cleanup         remove the worktree when a ticket succeeds

  -h, --help            this text

EXAMPLES
  $SCRIPT_NAME --dry-run                    # show the wave plan
  $SCRIPT_NAME -j 4 --label ready-for-agent # dispatch, 4 at a time
  $SCRIPT_NAME --one-wave 41 42                # just these two, one wave
HELPTEXT
}

die() { printf '%s: %s\n' "$SCRIPT_NAME" "$*" >&2; exit 1; }
log() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*" >&2; }

# ------------------------------------------------------------------- args ---

while (($#)); do
  case $1 in
    --mode)         MODE=${2:?}; shift 2 ;;
    --land)         ACTION=land; shift ;;
    --status)       ACTION=status; shift ;;
    -r|--repo)      REPO_OPT=${2:?}; shift 2 ;;
    --dep-words)    DEP_WORDS=${2:?}; shift 2 ;;
    -l|--label)     LABELS+=("${2:?--label needs a value}"); shift 2 ;;
    --limit)        LIMIT=${2:?}; shift 2 ;;
    -f|--force)     FORCE=1; shift ;;
    -j|--jobs)      JOBS=${2:?}; shift 2 ;;
    -n|--dry-run|--plan) DRY_RUN=1; shift ;;
    --one-wave)     ONE_WAVE=1; shift ;;
    --max-waves)    MAX_WAVES=${2:?}; shift 2 ;;
    -b|--base)      BASE=${2:?}; shift 2 ;;
    --no-stack)     DO_STACK=0; shift ;;
    --timeout)      TIMEOUT=${2:?}; shift 2 ;;
    -m|--model)     MODEL=${2:?}; shift 2 ;;
    --permission-mode) PERMISSION_MODE=${2:?}; shift 2 ;;
    --agent-cmd)    AGENT_CMD=${2:?}; shift 2 ;;
    --prompt-file)  PROMPT_TEMPLATE_FILE=${2:?}; shift 2 ;;
    --branch-prefix) BRANCH_PREFIX=${2:?}; shift 2 ;;
    --worktree-root) WORKTREE_ROOT_OPT=${2:?}; shift 2 ;;
    --no-push)      DO_PUSH=0; DO_PR=0; shift ;;
    --no-pr)        DO_PR=0; shift ;;
    --no-assign)    DO_ASSIGN=0; shift ;;
    --no-comment)   DO_COMMENT=0; shift ;;
    --no-native-deps) NATIVE_DEPS=0; shift ;;
    --cleanup)      CLEANUP=1; shift ;;
    -h|--help)      usage; exit 0 ;;
    [0-9]*)         EXPLICIT+=("$1"); shift ;;
    \#[0-9]*)       EXPLICIT+=("${1#\#}"); shift ;;
    --)             shift; while (($#)); do EXPLICIT+=("${1#\#}"); shift; done ;;
    *)              die "unknown option: $1 (try --help)" ;;
  esac
done

[[ $JOBS =~ ^[0-9]+$ && $JOBS -ge 1 ]] || die "--jobs must be a positive integer"
[[ $MODE == session || $MODE == print ]] || die "--mode must be session or print"

for tool in gh jq git; do
  command -v "$tool" >/dev/null || die "$tool is required but not on PATH"
done
if ((!DRY_RUN)) && [[ -z $AGENT_CMD ]]; then
  command -v claude >/dev/null || die "claude is required (or pass --agent-cmd)"
fi
if [[ $MODE == session && -n $AGENT_CMD ]]; then
  die "--agent-cmd only applies to --mode print"
fi

git rev-parse --git-dir >/dev/null 2>&1 || die "not inside a git repository"
GIT_COMMON=$(git rev-parse --git-common-dir)
MAIN_ROOT=$(cd "$(dirname "$GIT_COMMON")" && pwd)
if [[ -n $REPO_OPT ]]; then
  REPO=$REPO_OPT
else
  REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner) \
    || die "could not resolve the GitHub repo (pass --repo, or check gh auth)"
fi
if [[ -z $BASE ]]; then
  BASE=$(gh repo view "$REPO" --json defaultBranchRef --jq .defaultBranchRef.name)
fi
# A base like `origin/main` is a valid branch point but not a valid PR base on
# GitHub, which wants the branch name. Keep both forms.
BASE_BRANCH=${BASE#refs/remotes/}
BASE_BRANCH=${BASE_BRANCH#origin/}

STATE_DIR=$MAIN_ROOT/.dispatch
WORKTREE_ROOT=${WORKTREE_ROOT_OPT:-$MAIN_ROOT/.claude/worktrees/dispatch}
RUN_ID=$(date +%Y%m%d-%H%M%S)
RUN_DIR=$STATE_DIR/$RUN_ID
mkdir -p "$RUN_DIR/logs" "$RUN_DIR/prompts" "$RUN_DIR/status"
# Session records outlive a single run: --land and --status read them back.
SESSION_DIR=$STATE_DIR/sessions
mkdir -p "$SESSION_DIR"
mkdir -p "$WORKTREE_ROOT"

# Some mounted filesystems (the /c host mount in a sandbox, for one) break
# getcwd() for a process that has just written into its own working directory.
# Agents run plenty of `write a file, then git add` commands, so warn loudly
# and point at the fix rather than letting every ticket log mystery failures.
check_worktree_root() {
  local probe=$WORKTREE_ROOT/.probe.$$
  rm -rf "$probe"
  mkdir -p "$probe" || return 0
  # A throwaway repo, so the probe is meaningful even when the worktree root
  # sits outside the project.
  git init -q "$probe" >/dev/null 2>&1 || { rm -rf "$probe"; return 0; }
  if ! ( cd -P "$probe" && bash -c 'echo x > probe.txt && git status --porcelain' ) \
       >/dev/null 2>&1; then
    log "WARNING: on $WORKTREE_ROOT a process cannot read its own working"
    log "         directory after writing to it, so agents will hit"
    log "         'fatal: Unable to read current working directory'."
    log "         Re-run with a worktree root on a local filesystem, e.g."
    log "         --worktree-root \"\$HOME/dispatch-worktrees\""
  fi
  rm -rf "$probe"
}
check_worktree_root

# ------------------------------------------------------------- issue cache --

declare -A I_TITLE I_BODY I_STATE I_ASSIGNEES I_LABELS
ALL_NUMBERS=()

load_issues() {
  I_TITLE=(); I_BODY=(); I_STATE=(); I_ASSIGNEES=(); I_LABELS=(); ALL_NUMBERS=()
  local num state assignees labels title_b64 body_b64
  # US (\x1f) separates fields: unlike tab it is not IFS whitespace, so an
  # empty column (no assignees, no labels) survives `read` instead of
  # collapsing and shifting every later field.
  while IFS=$'\x1f' read -r num state assignees labels title_b64 body_b64; do
    [[ -n $num ]] || continue
    ALL_NUMBERS+=("$num")
    I_STATE[$num]=$state
    I_ASSIGNEES[$num]=$assignees
    I_LABELS[$num]=$labels
    I_TITLE[$num]=$(printf '%s' "$title_b64" | base64 -d)
    I_BODY[$num]=$(printf '%s' "$body_b64" | base64 -d)
  done < <(
    gh issue list --repo "$REPO" --state all --limit "$LIMIT" \
      --json number,title,body,state,labels,assignees \
      --jq '.[] | [
              (.number | tostring),
              .state,
              ([.assignees[].login] | join(",")),
              ([.labels[].name] | join(",")),
              (.title | @base64),
              ((.body // "") | @base64)
            ] | join("\u001f")'
  )
  ((${#ALL_NUMBERS[@]})) || die "no issues returned by gh"
}

has_all_labels() {
  local n=$1 want
  local have=",${I_LABELS[$n]},"
  for want in "${LABELS[@]:-}"; do
    [[ -n $want ]] || continue
    [[ $have == *",$want,"* ]] || return 1
  done
  return 0
}

# Blockers declared in the body: "Depends on #3, #4" / "Blocked by: #3".
# Which phrases count is configurable with --dep-words, since trackers differ.
body_blockers() {
  local n=$1
  printf '%s\n' "${I_BODY[$n]:-}" \
    | grep -oiE "($DEP_WORDS)[^.]*" \
    | grep -oE '#[0-9]+' \
    | tr -d '#' \
    | sort -un || true          # no match is normal, not an error
}

declare -A NATIVE_BLOCKED
native_blocked() {
  local n=$1
  ((NATIVE_DEPS)) || { echo 0; return; }
  if [[ -z ${NATIVE_BLOCKED[$n]:-} ]]; then
    NATIVE_BLOCKED[$n]=$(gh api "repos/$REPO/issues/$n" \
      --jq '.issue_dependencies_summary.blocked_by // 0' 2>/dev/null || echo 0)
  fi
  echo "${NATIVE_BLOCKED[$n]}"
}

# Blockers that are still open (and therefore still gate the ticket).
open_blockers() {
  local n=$1 b
  for b in $(body_blockers "$n"); do
    [[ -n ${I_STATE[$b]:-} ]] || continue      # unknown number: ignore
    [[ ${I_STATE[$b]} == OPEN ]] || continue
    echo "$b"
  done
}

branch_of() { printf '%s%s' "$BRANCH_PREFIX" "$1"; }

branch_exists() {
  local br=$1
  git -C "$MAIN_ROOT" show-ref --verify --quiet "refs/heads/$br" && return 0
  git -C "$MAIN_ROOT" show-ref --verify --quiet "refs/remotes/origin/$br" && return 0
  return 1
}

# Prints why this ticket should be left alone, or nothing if it is dispatchable.
# Always exits 0 — a non-zero return here would trip `set -e` at the call site.
skip_reason() {
  local n=$1 br
  br=$(branch_of "$n")
  if [[ ${I_STATE[$n]:-} != OPEN ]]; then
    echo "not open"
  elif ((FORCE)); then
    :
  elif [[ -n ${I_ASSIGNEES[$n]} ]]; then
    echo "assigned to ${I_ASSIGNEES[$n]}"
  elif branch_exists "$br"; then
    echo "branch $br exists"
  fi
  return 0
}

# ------------------------------------------------------------------ prompt --

default_prompt_template() {
  # The FIRST LINE becomes the session's name in `claude agents`, so it has to
  # read as the ticket at a glance.
  cat <<'TPL'
#{{NUMBER}} {{TITLE}}

You are an engineer implementing this GitHub issue in {{REPO}}.

{{BODY}}

# Working agreement

- Your working directory is a dedicated git worktree on branch `{{BRANCH}}`,
  branched from `{{BASE}}`. Everything you need is here.
- Read this repository's agent instructions first — whichever of `CLAUDE.md`,
  `AGENTS.md`, `CONTRIBUTING.md`, `README.md`, or a `docs/` guide exist — and
  follow the conventions and vocabulary they set.
- Implement exactly what this issue asks. Do not start work that belongs to
  another ticket, and do not refactor unrelated code.
- Write the tests the issue names, and run them. Report real results — if a
  test fails, say so.
- Commit your work on `{{BRANCH}}` with a message referencing #{{NUMBER}}.
- Do NOT push, do NOT open a pull request, do NOT merge or switch branches.
  The dispatcher handles all of that.
- If the issue is under-specified or you hit a genuine blocker, stop and
  explain it in your final message instead of guessing. Issues that say
  "STOP and report" mean it.
- A human may attach to this session while you work, to steer the work toward
  a business goal the ticket does not spell out. Their instructions outrank
  the ticket text; ask them when the ticket and the goal seem to disagree.

Finish with a short summary: what you changed, what you ran, what you left out.
TPL
}

build_prompt() {
  local n=$1 branch=$2 base=$3 tpl
  if [[ -n $PROMPT_TEMPLATE_FILE ]]; then
    tpl=$(cat "$PROMPT_TEMPLATE_FILE")
  else
    tpl=$(default_prompt_template)
  fi
  tpl=${tpl//\{\{NUMBER\}\}/$n}
  tpl=${tpl//\{\{TITLE\}\}/${I_TITLE[$n]}}
  tpl=${tpl//\{\{BODY\}\}/${I_BODY[$n]}}
  tpl=${tpl//\{\{BRANCH\}\}/$branch}
  tpl=${tpl//\{\{BASE\}\}/$base}
  tpl=${tpl//\{\{REPO\}\}/$REPO}
  printf '%s\n' "$tpl"
}

# ------------------------------------------------------------ ticket runner --

set_status() { printf '%s\n' "$2" > "$RUN_DIR/status/$1"; }
get_status() { cat "$RUN_DIR/status/$1" 2>/dev/null || echo "unknown"; }

# Where this ticket's branch should start: on top of any blocker branch that
# this run created, so stacked work sees its dependency's code.
base_for() {
  local n=$1 b br primary="" extras=()
  if ((DO_STACK)); then
    for b in $(open_blockers "$n"); do
      br=$(branch_of "$b")
      [[ -n ${DISPATCHED[$b]:-} ]] || continue
      # In a real run the blocker's branch must already exist; when planning,
      # take it on faith so the plan shows the stack it would build.
      if ((!DRY_RUN)) && ! branch_exists "$br"; then continue; fi
      if [[ -z $primary ]]; then primary=$br; else extras+=("$br"); fi
    done
  fi
  printf '%s\t%s\n' "${primary:-$BASE}" "${extras[*]:-}"
}

# Everything a ticket needs before an agent touches it: a worktree on its own
# branch, any blocker branches merged in, and the prompt written out.
prepare_ticket() {
  local n=$1 base=$2 extras=$3
  local branch; branch=$(branch_of "$n")
  local wt=$WORKTREE_ROOT/issue-$n
  local logf=$RUN_DIR/logs/issue-$n.log
  local extra

  {
    echo "=== issue #$n: ${I_TITLE[$n]}"
    echo "=== branch $branch  base $base  extras ${extras:-none}"
    echo "=== prepared $(date -Is)"
  } >>"$logf"

  if [[ -e $wt ]]; then
    git -C "$MAIN_ROOT" worktree remove --force "$wt" >>"$logf" 2>&1 || true
  fi
  if ! git -C "$MAIN_ROOT" worktree add -b "$branch" "$wt" "$base" >>"$logf" 2>&1; then
    set_status "$n" "failed: could not create worktree"; return 1
  fi
  for extra in $extras; do
    if ! git -C "$wt" merge --no-edit "$extra" >>"$logf" 2>&1; then
      git -C "$wt" merge --abort >>"$logf" 2>&1 || true
      set_status "$n" "failed: conflict merging blocker branch $extra"; return 1
    fi
  done

  # On a mounted filesystem a freshly created worktree can take a moment to
  # become resolvable by a newly forked process: getcwd() fails and every git
  # command the agent runs dies with "Unable to read current working
  # directory". Wait for it to settle before handing it to the agent.
  local tries=0
  until ( cd -P "$wt" && git rev-parse --show-toplevel ) >/dev/null 2>&1; do
    tries=$((tries + 1))
    if ((tries > 30)); then
      set_status "$n" "failed: worktree $wt never became usable"; return 1
    fi
    sleep 1
  done

  build_prompt "$n" "$branch" "$base" > "$RUN_DIR/prompts/issue-$n.md"
  if ((DO_ASSIGN)); then
    gh issue edit "$n" --repo "$REPO" --add-assignee @me >>"$logf" 2>&1 || true
  fi
  return 0
}

# --- session mode: an attachable background session per ticket ---------------

# Records what a later --land or --status invocation needs to find the work.
remember_session() {
  printf '%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "$4" > "$SESSION_DIR/$5"
}

session_field() { # $1 = ticket, $2 = field (1 id, 2 branch, 3 worktree, 4 base)
  [[ -f $SESSION_DIR/$1 ]] || return 1
  cut -f"$2" "$SESSION_DIR/$1"
}

# working | done | gone. A ticket whose session was removed reads as gone,
# which lands the same way done does.
session_state() {
  local wt=$1 st
  st=$(claude agents --json --all --cwd "$wt" 2>/dev/null \
       | jq -r 'sort_by(.startedAt) | last | .state // "gone"' 2>/dev/null) || st=gone
  printf '%s' "${st:-gone}"
}

launch_session() {
  local n=$1 base=$2
  local branch; branch=$(branch_of "$n")
  local wt=$WORKTREE_ROOT/issue-$n
  local logf=$RUN_DIR/logs/issue-$n.log
  local promptf=$RUN_DIR/prompts/issue-$n.md

  local -a cmd=(claude --bg --permission-mode "$PERMISSION_MODE")
  if [[ -n $MODEL ]]; then cmd+=(--model "$MODEL"); fi

  if ! ( cd -P "$wt" && "${cmd[@]}" "$(cat "$promptf")" ) >>"$logf" 2>&1; then
    set_status "$n" "failed: could not start a background session (see $logf)"
    return 1
  fi

  # The short id is what `claude attach` takes. It reaches the registry a
  # moment after launch, so give it a few seconds.
  local id="" tries=0
  while [[ -z $id ]] && ((tries < 20)); do
    id=$(claude agents --json --all --cwd "$wt" 2>/dev/null \
         | jq -r 'sort_by(.startedAt) | last | .id // empty' 2>/dev/null) || id=""
    [[ -n $id ]] && break
    tries=$((tries + 1))
    sleep 1
  done

  remember_session "${id:-unknown}" "$branch" "$wt" "$base" "$n"
  set_status "$n" "session ${id:-unknown} on $branch"
  return 0
}

# --- landing: a finished session's work becomes a pushed branch and a PR -----

land_ticket() {
  local n=$1
  local id branch wt base
  id=$(session_field "$n" 1) || { set_status "$n" "not dispatched"; return 1; }
  branch=$(session_field "$n" 2)
  wt=$(session_field "$n" 3)
  base=$(session_field "$n" 4)
  local logf=$RUN_DIR/logs/issue-$n.log

  local st; st=$(session_state "$wt")
  if [[ $st == working || $st == busy ]]; then
    set_status "$n" "still working — session $id is mid-turn, not landing it"
    return 1
  fi
  if [[ ! -d $wt ]]; then
    set_status "$n" "failed: worktree $wt is gone"; return 1
  fi

  # Safety net: the agent is asked to commit, but don't lose work if it didn't.
  if [[ -n $(git -C "$wt" status --porcelain) ]]; then
    git -C "$wt" add -A >>"$logf" 2>&1
    git -C "$wt" commit -m "Uncommitted agent work for #$n" >>"$logf" 2>&1 || true
  fi
  if [[ $(git -C "$wt" rev-list --count "$base..HEAD" 2>/dev/null || echo 0) == 0 ]]; then
    set_status "$n" "no-changes: nothing committed on $branch yet"; return 1
  fi

  if ((DO_PUSH)); then
    if ! git -C "$wt" push -u origin "$branch" >>"$logf" 2>&1; then
      set_status "$n" "failed: push rejected (see $logf)"; return 1
    fi
  fi

  local pr_url=""
  if ((DO_PR)); then
    # Stacked tickets target their blocker's branch; first-wave tickets target
    # the trunk under the name GitHub knows it by.
    local pr_base=$base
    [[ $pr_base == "$BASE" ]] && pr_base=$BASE_BRANCH
    pr_url=$(gh pr view "$branch" --repo "$REPO" --json url --jq .url 2>/dev/null) || pr_url=""
    if [[ -z $pr_url ]]; then
      pr_url=$(gh pr create --repo "$REPO" \
        --head "$branch" --base "$pr_base" \
        --title "${I_TITLE[$n]} (#$n)" \
        --body "Closes #$n

Implemented by a dispatched Claude Code agent, session \`$id\`." 2>>"$logf") || pr_url=""
    fi
    if [[ -z $pr_url ]]; then
      set_status "$n" "partial: branch pushed, PR not created (see $logf)"; return 1
    fi
  fi

  if ((DO_COMMENT)); then
    gh issue comment "$n" --repo "$REPO" \
      --body "Dispatched agent finished on \`$branch\`${pr_url:+ - $pr_url}" \
      >>"$logf" 2>&1 || true
  fi
  if ((CLEANUP)); then
    git -C "$MAIN_ROOT" worktree remove --force "$wt" >>"$logf" 2>&1 || true
  fi

  set_status "$n" "landed: $branch${pr_url:+ $pr_url}"
  return 0
}

# --- print mode: headless agent, runs to completion, lands immediately -------

run_ticket_print() {
  local n=$1 base=$2 extras=$3
  local wt=$WORKTREE_ROOT/issue-$n
  local logf=$RUN_DIR/logs/issue-$n.log
  local branch; branch=$(branch_of "$n")
  local rc=0

  set_status "$n" "running"
  prepare_ticket "$n" "$base" "$extras" || return 1
  remember_session "print" "$branch" "$wt" "$base" "$n"

  local -a cmd
  if [[ -n $AGENT_CMD ]]; then
    cmd=(bash -c "$AGENT_CMD")
  else
    cmd=(claude -p --permission-mode "$PERMISSION_MODE" --output-format text)
    if [[ -n $MODEL ]]; then cmd+=(--model "$MODEL"); fi
  fi
  if ((TIMEOUT > 0)) && command -v timeout >/dev/null; then
    cmd=(timeout --signal=INT "$TIMEOUT" "${cmd[@]}")
  fi

  set +e
  ( cd -P "$wt" && "${cmd[@]}" < "$RUN_DIR/prompts/issue-$n.md" ) >>"$logf" 2>&1
  rc=$?
  set -e
  echo "=== agent exit $rc  $(date -Is)" >>"$logf"
  if ((rc != 0)); then
    set_status "$n" "failed: agent exited $rc (see $logf)"; return 1
  fi

  land_ticket "$n"
}

# --------------------------------------------------------------- the plan ----

load_issues

CANDIDATES=()
if ((${#EXPLICIT[@]})); then
  for n in "${EXPLICIT[@]}"; do
    [[ -n ${I_STATE[$n]:-} ]] || die "issue #$n not found"
    CANDIDATES+=("$n")
  done
else
  for n in "${ALL_NUMBERS[@]}"; do
    [[ ${I_STATE[$n]} == OPEN ]] || continue
    has_all_labels "$n" || continue
    CANDIDATES+=("$n")
  done
fi
((${#CANDIDATES[@]})) || die "no candidate issues"

RUNNABLE=()
declare -A SKIPPED=()
for n in "${CANDIDATES[@]}"; do
  reason=$(skip_reason "$n")
  if [[ -n $reason ]]; then SKIPPED[$n]=$reason; else RUNNABLE+=("$n"); fi
done

if ((${#RUNNABLE[@]})); then
  mapfile -t RUNNABLE < <(printf '%s\n' "${RUNNABLE[@]}" | sort -n)
fi

# --status and --land report on work already dispatched; the planning header
# and the skip list are noise there.
if [[ $ACTION == dispatch ]]; then
  echo "repo:  $REPO"
  echo "base:  $BASE_BRANCH"
  echo "run:   $RUN_DIR"
  echo "mode:  $MODE   jobs: $JOBS   stack: $DO_STACK   dry-run: $DRY_RUN"
  echo

  if ((${#SKIPPED[@]})); then
    echo "Skipped:"
    for n in $(printf '%s\n' "${!SKIPPED[@]}" | sort -n); do
      printf '  #%-4s %-55.55s  %s\n' "$n" "${I_TITLE[$n]}" "${SKIPPED[$n]}"
    done
    echo
  fi
  ((${#RUNNABLE[@]})) || die "nothing to dispatch"
fi

declare -A DISPATCHED=()   # issue -> 1 once its branch carries work
declare -A SCHEDULED=()    # issue -> wave number (planning + execution)

# A blocker is cleared once its branch carries work, not only once the issue
# closes — otherwise nothing downstream could start until PRs were merged, and
# a second invocation could never pick up where the first left off.
branch_has_work() {
  local br; br=$(branch_of "$1")
  branch_exists "$br" || return 1
  local ahead
  ahead=$(git -C "$MAIN_ROOT" rev-list --count "$BASE..$br" 2>/dev/null || echo 0)
  [[ $ahead != 0 ]]
}

ready_now() {
  local n b ready=() blocked
  for n in "${RUNNABLE[@]}"; do
    [[ -z ${SCHEDULED[$n]:-} ]] || continue
    blocked=0
    for b in $(open_blockers "$n"); do
      [[ -n ${DISPATCHED[$b]:-} ]] && continue
      branch_has_work "$b" && continue
      blocked=1; break
    done
    if ((!blocked)) && ((NATIVE_DEPS)) && [[ $(native_blocked "$n") != 0 ]]; then
      blocked=1
    fi
    ((blocked)) || ready+=("$n")
  done
  printf '%s\n' "${ready[@]:-}"
}

if ((DRY_RUN)); then
  wave=0
  while :; do
    mapfile -t batch < <(ready_now)
    [[ -n ${batch[0]:-} ]] || break
    wave=$((wave + 1))
    ((wave <= MAX_WAVES)) || break
    printf 'Wave %d — %d ticket(s), %d at a time:\n' "$wave" "${#batch[@]}" "$JOBS"
    for n in "${batch[@]}"; do
      IFS=$'\t' read -r tbase textras < <(base_for "$n")
      printf '  #%-4s %-55.55s  from %s%s\n' "$n" "${I_TITLE[$n]}" "$tbase" \
        "${textras:+ + $textras}"
      SCHEDULED[$n]=$wave
      DISPATCHED[$n]=1
    done
    echo
    ((ONE_WAVE)) && break
  done
  left=()
  for n in "${RUNNABLE[@]}"; do [[ -n ${SCHEDULED[$n]:-} ]] || left+=("$n"); done
  if ((${#left[@]})); then
    echo "Not reachable in this run (blocker outside the set, or a cycle):"
    for n in "${left[@]}"; do
      printf '  #%-4s %-55.55s  blocked by %s\n' "$n" "${I_TITLE[$n]}" \
        "$(open_blockers "$n" | tr '\n' ' ')"
    done
  fi
  exit 0
fi

# ------------------------------------------------------------- execution ----

dispatched_tickets() { ls "$SESSION_DIR" 2>/dev/null | grep -E '^[0-9]+$' | sort -n; }

if [[ $ACTION == status ]]; then
  found=0
  for n in $(dispatched_tickets); do
    if ((${#EXPLICIT[@]})) && ! printf '%s\n' "${EXPLICIT[@]}" | grep -qx "$n"; then
      continue
    fi
    found=1
    id=$(session_field "$n" 1); branch=$(session_field "$n" 2)
    wt=$(session_field "$n" 3); base=$(session_field "$n" 4)
    st=$(session_state "$wt")
    commits=$(git -C "$MAIN_ROOT" rev-list --count "$base..$branch" 2>/dev/null || echo '?')
    printf '  #%-4s %-40.40s  %-8s  %-9s  %s commit(s)\n' \
      "$n" "${I_TITLE[$n]:-}" "$id" "$st" "$commits"
    printf '        attach: claude attach %s\n' "$id"
  done
  ((found)) || echo "Nothing dispatched yet."
  exit 0
fi

if [[ $ACTION == land ]]; then
  landed=0; held=0
  for n in $(dispatched_tickets); do
    if ((${#EXPLICIT[@]})) && ! printf '%s\n' "${EXPLICIT[@]}" | grep -qx "$n"; then
      continue
    fi
    if land_ticket "$n"; then landed=$((landed + 1)); else held=$((held + 1)); fi
    printf '  #%-4s %s\n' "$n" "$(get_status "$n")"
  done
  echo
  echo "$landed landed, $held not landed."
  ((held == 0))
  exit $?
fi

PIDS=()
cleanup_children() {
  local p
  for p in "${PIDS[@]:-}"; do [[ -n $p ]] && kill "$p" 2>/dev/null || true; done
}
wait_for_slot() {
  while (( $(jobs -pr | wc -l) >= JOBS )); do sleep 2; done
}

# --- session mode: launch one attachable session per ready ticket, then stop --
if [[ $MODE == session ]]; then
  mapfile -t batch < <(ready_now)
  [[ -n ${batch[0]:-} ]] || die "nothing is ready to dispatch"
  if ((${#batch[@]} > JOBS)); then
    log "${#batch[@]} tickets are ready; launching the first $JOBS (raise --jobs for more)"
    batch=("${batch[@]:0:$JOBS}")
  fi

  launched=()
  for n in "${batch[@]}"; do
    SCHEDULED[$n]=1
    IFS=$'\t' read -r tbase textras < <(base_for "$n")
    log "preparing #$n (${I_TITLE[$n]}) from $tbase"
    if ! prepare_ticket "$n" "$tbase" "$textras"; then
      log "  #$n $(get_status "$n")"
      continue
    fi
    if launch_session "$n" "$tbase"; then
      launched+=("$n")
    else
      log "  #$n $(get_status "$n")"
    fi
  done

  ((${#launched[@]})) || die "no sessions started"

  echo
  echo "Sessions you can attach to and steer:"
  echo
  printf '  %-5s %-40.40s %-9s %s\n' TICKET TITLE SESSION BRANCH
  for n in "${launched[@]}"; do
    printf '  #%-4s %-40.40s %-9s %s\n' \
      "$n" "${I_TITLE[$n]}" "$(session_field "$n" 1)" "$(session_field "$n" 2)"
  done
  echo
  echo "  claude agents                 list them"
  for n in "${launched[@]}"; do
    printf '  claude attach %-9s     steer #%s\n' "$(session_field "$n" 1)" "$n"
  done
  echo
  echo "When a session is done, land its work:"
  echo "  $0 --land                     push branches and open PRs"
  echo "  $0 --status                   see what each session is doing"
  echo
  echo "Then run this again for the next wave — tickets whose blockers now have"
  echo "work are picked up automatically."
  exit 0
fi

# --- print mode: headless agents, waves chained in one invocation ------------
trap 'log "interrupted - stopping agents"; cleanup_children; exit 130' INT TERM

wave=0
while :; do
  wave=$((wave + 1))
  if ((wave > MAX_WAVES)); then log "hit --max-waves $MAX_WAVES"; break; fi

  load_issues
  mapfile -t batch < <(ready_now)
  [[ -n ${batch[0]:-} ]] || break

  log "wave $wave: dispatching ${#batch[@]} ticket(s) - ${batch[*]}"
  PIDS=()
  for n in "${batch[@]}"; do
    SCHEDULED[$n]=$wave
    IFS=$'\t' read -r tbase textras < <(base_for "$n")
    wait_for_slot
    log "  #$n ${I_TITLE[$n]} (from $tbase)"
    run_ticket_print "$n" "$tbase" "$textras" &
    PIDS+=($!)
  done
  wait || true

  for n in "${batch[@]}"; do
    st=$(get_status "$n")
    case $st in
      landed:*) DISPATCHED[$n]=1; log "  #$n OK - ${st#landed: }" ;;
      *)        log "  #$n $st" ;;
    esac
  done

  ((ONE_WAVE)) && break
done

trap - INT TERM

echo
echo "Summary  ($RUN_DIR)"
ok=0; bad=0; pending=0
for n in "${RUNNABLE[@]}"; do
  if [[ -z ${SCHEDULED[$n]:-} ]]; then
    printf '  #%-4s %-45.45s  NOT REACHED (blocked by %s)\n' "$n" "${I_TITLE[$n]}" \
      "$(open_blockers "$n" | tr '\n' ' ')"
    pending=$((pending + 1))
    continue
  fi
  st=$(get_status "$n")
  printf '  #%-4s %-45.45s  %s\n' "$n" "${I_TITLE[$n]}" "$st"
  case $st in landed:*) ok=$((ok + 1)) ;; *) bad=$((bad + 1)) ;; esac
done
echo
echo "$ok succeeded, $bad failed, $pending not reached. Logs: $RUN_DIR/logs/"
((bad == 0 && pending == 0)) || exit 1

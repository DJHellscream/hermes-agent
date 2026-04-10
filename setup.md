# Hermes task-tree accounting PoC setup

Goal: create a safe, repeatable local development workflow for proving exact task-tree token accounting without contaminating the live Hermes environment under `~/.hermes`.

This setup intentionally separates three things:
- live Hermes runtime and data: `~/.hermes`
- development code checkout: `~/projects/hermes-agent`
- disposable PoC runtime state: temporary `HERMES_HOME` directories under `/tmp`

Do not reintroduce a persistent `hermes-dev` wrapper or a standing alternate `HERMES_HOME`.

## 1. Repository layout

Primary checkout:
- `~/projects/hermes-agent`

Remotes:
- `origin` = `https://github.com/NousResearch/hermes-agent.git`
- `fork` = `git@github-hermes-agent:DJHellscream/hermes-agent.git`

Expected branch strategy:
- keep local `main` aligned to `origin/main`
- do all work on feature branches
- push feature branches to `fork`
- open PRs from `fork/<feature-branch>` into upstream `main`

## 2. Fork hygiene

The fork main branch was reset to upstream main. Going forward:
- do not commit experimental dev-wrapper artifacts to `main`
- do not use `main` as a scratch branch
- create a feature branch immediately after pulling upstream changes

Recommended branch creation:

```bash
cd ~/projects/hermes-agent
git fetch origin --prune
git checkout main
git reset --hard origin/main
git checkout -b feat/task-tree-accounting
```

## 3. Python environment

Use a repo-local virtual environment only.

Create/update the venv:

```bash
cd ~/projects/hermes-agent
python3 -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
pip install -e ".[dev]"
```

If upstream has moved and dependencies changed:

```bash
cd ~/projects/hermes-agent
source venv/bin/activate
pip install -e ".[dev]"
```

Never point this checkout at the live Hermes venv under `~/.hermes/hermes-agent/venv`.

## 4. Runtime isolation policy

For runtime smoke tests, always use a disposable `HERMES_HOME` under `/tmp`.

One-shot pattern:

```bash
export HERMES_HOME=$(mktemp -d /tmp/hermes-poc-XXXXXX)
echo "$HERMES_HOME"
```

After the test:

```bash
rm -rf "$HERMES_HOME"
unset HERMES_HOME
```

Inline single-command pattern:

```bash
HERMES_HOME=$(mktemp -d /tmp/hermes-poc-XXXXXX) python -m hermes_cli.main chat -Q -q "say ok"
```

Then manually remove the printed temp directory.

Rules:
- do not export `HERMES_HOME` in shell startup files
- do not create `~/.hermes-dev-*`
- do not create wrapper scripts in `~/.local/bin`
- do not run runtime PoC tests against live `~/.hermes`

## 5. What can safely use live `~/.hermes`

Allowed read-only inspection:
- reading current live code for comparison
- reading live session DB schemas
- reading prompt/spec files under `~/hermes-office`
- reading old transcripts under `~/.hermes/sessions`

Not allowed for PoC execution:
- writing new runtime data into `~/.hermes`
- modifying live runtime code under `~/.hermes/hermes-agent`
- testing feature branches against the live Hermes home

## 6. Test workflow

Use targeted tests first. Do not lead with the full suite.

Core targeted tests for this work:

```bash
cd ~/projects/hermes-agent
source venv/bin/activate
pytest tests/test_hermes_state.py -q
pytest tests/run_agent/test_token_persistence_non_cli.py -q
pytest tests/tools/test_delegate.py -q
```

If new tests are added for reporting helpers or rollups, run them directly first.

Examples:

```bash
pytest tests/test_hermes_state.py -k accounting -q
pytest tests/run_agent/test_token_persistence_non_cli.py -k compaction -q
pytest tests/run_agent/test_token_persistence_non_cli.py -k delegate -q
```

## 7. Runtime smoke-test workflow

Use the disposable home and run only narrowly scoped checks.

Examples:

### Basic startup sanity
```bash
cd ~/projects/hermes-agent
source venv/bin/activate
export HERMES_HOME=$(mktemp -d /tmp/hermes-poc-XXXXXX)
python -m hermes_cli.main chat -Q -q "say ok"
rm -rf "$HERMES_HOME"
unset HERMES_HOME
```

### Delegate-task ACP worker smoke test
This should only be run after the accounting feature changes are in place and should write artifacts only inside `/tmp`.

```bash
cd ~/projects/hermes-agent
source venv/bin/activate
export HERMES_HOME=$(mktemp -d /tmp/hermes-poc-XXXXXX)
# run a bounded smoke test that creates a small file in /tmp via delegate_task ACP profile worker
rm -rf "$HERMES_HOME"
unset HERMES_HOME
```

### Inspecting disposable DBs after a run
```bash
find "$HERMES_HOME" -maxdepth 2 -type f | sort
```

If needed:
```bash
python3 - <<'PY'
import sqlite3, os
home = os.environ['HERMES_HOME']
for path in [f"{home}/state.db", f"{home}/accounting.db"]:
    print("##", path)
    if not os.path.exists(path):
        print("missing")
        continue
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    for row in cur.execute("select name from sqlite_master where type='table' order by name"):
        print(row)
    conn.close()
PY
```

## 8. Safety checks before every push

Before pushing a feature branch:
- confirm current branch is not `main`
- confirm no dev-wrapper files are being introduced
- confirm no references to persistent `~/.hermes-dev-*` were added
- confirm docs do not instruct users to use a second persistent Hermes identity on the same machine

Recommended checks:

```bash
git status --short
git branch --show-current
git diff --stat origin/main...HEAD
rg -n "hermes-dev|\.hermes-dev-" .
```

## 9. Push workflow

```bash
cd ~/projects/hermes-agent
git checkout feat/task-tree-accounting
git push -u fork feat/task-tree-accounting
```

Then open PR:
- base: `NousResearch/hermes-agent:main`
- compare: `DJHellscream/hermes-agent:feat/task-tree-accounting`

## 10. Success criteria for setup

This setup is correct if all of the following are true:
- live Hermes still runs from `~/.hermes` and is untouched
- all development code changes happen only in `~/projects/hermes-agent`
- all runtime PoC state is under disposable `/tmp/hermes-poc-*`
- no wrapper scripts or persistent alternate homes are created
- feature work can be tested, committed, and pushed without contaminating live Hermes

# Task-tree accounting implementation plan

> For Hermes: implement this on a feature branch off `origin/main` in `~/projects/hermes-agent`, using targeted tests first and disposable `HERMES_HOME` runtime tests only.

Goal: make Hermes able to answer exact token-cost questions for a single task tree: manager-only, worker-only, total task subtree, and grouped by provider / base URL / model, without relying on transcript reconstruction or profile-local `state.db` rows as canonical truth.

Architecture:
- Keep `state.db` for local session summaries and transcript-oriented UX.
- Use a separate `accounting.db` as the canonical exact run-tree ledger.
- Keep `delegate_task` as the manager-facing tool. Do not introduce a separate `delegate_profile_task` in this scope.
- Use `delegate_task(..., acp_command='hermes', acp_args=['--profile', '<worker-profile>', 'acp', '--stdio'])` as the profile-backed worker path.
- Treat `run_id` / `root_run_id` as durable accounting identity; treat `session_id` as local session-summary identity that may rotate during compaction.

Tech stack:
- Python
- SQLite
- pytest
- existing Hermes `AIAgent`, `SessionDB`, and `delegate_task` codepaths

Non-goals in this plan:
- no new `delegate_profile_task`
- no attempt to solve standalone `hermes -p ...` cross-process lineage in this first slice unless the current implementation already exposes a minimal deterministic hook
- no attempt to force `sessions` rows to become exact per-call accounting records
- no “subscription savings” reporting in core storage; that remains derived/reporting

---

## Problem statement

Current Hermes local session summaries are insufficient for the real question:
- how many tokens did the manager spend?
- how many tokens did workers spend?
- what was the total task cost across the task tree?
- how much usage was shifted from subscription-backed routes to local-model routes?

Why current `sessions` rows are insufficient:
- a session row stores one sticky model / provider / base URL label
- routing can change mid-session
- ACP/profile-worker local `state.db` rows may be incomplete or zeroed
- compaction can rotate `session_id`
- transcript JSON is not authoritative for exact token accounting

What the system must do instead:
- record exact usage as append-only events keyed to durable run lineage
- preserve the parent/root task tree
- separate summary UX from exact accounting

---

## Desired product behavior

For any root task, Hermes should be able to answer:

1. Manager only
- exact sum of usage events belonging to the root manager run

2. Worker only
- exact sum of usage events belonging to child runs in the same root tree

3. Total task
- exact sum of all usage events in the root tree

4. Grouped breakdown
- grouped by provider
- grouped by base URL
- grouped by model
- optionally grouped by profile/home and launch kind

5. Provenance and quality
- exact vs unknown usage status
- no fake precision when a provider/runtime returns no usage

Derived reporting that may come later:
- subscription-side vs local-model-side usage
- percentage shifted off subscription routes
- task efficiency summaries

---

## Data model

Canonical DB: `accounting.db`

### Table: `agent_runs`

Purpose:
- exact attributable run graph for one task tree

Core columns:
- `run_id`
- `parent_run_id`
- `root_run_id`
- `local_session_id`
- `home_id`
- `profile_name`
- `launch_kind`
- `transport_kind`
- `source`
- `model_hint`
- `provider_hint`
- `base_url_hint`
- `started_at`
- `ended_at`
- `metadata_json`

Interpretation:
- `run_id` is the durable accounting identity for one run boundary
- `root_run_id` groups all descendant work for a single task tree
- `local_session_id` is informational; it may rotate on compaction
- `launch_kind` should distinguish root vs delegated child runs
- `transport_kind` should distinguish direct vs ACP transport

### Table: `usage_events`

Purpose:
- append-only exact per-call usage accounting

Core columns:
- `event_id`
- `run_id`
- `root_run_id`
- `home_id`
- `profile_name`
- `local_session_id`
- `provider`
- `base_url`
- `model`
- `api_mode`
- `input_tokens`
- `output_tokens`
- `cache_read_tokens`
- `cache_write_tokens`
- `reasoning_tokens`
- `estimated_cost_usd`
- `usage_status`
- `recorded_at`
- `request_fingerprint`
- `metadata_json`

Interpretation:
- each provider/API call adds one `usage_events` row
- these rows are the source of truth for exact breakdowns
- provider/base URL/model changes mid-session are naturally represented by separate rows
- `usage_status` must distinguish `exact` from `unknown`

---

## Key design rules

1. `sessions` remains summary-only
- Keep `state.db` and `sessions` rows for local UX and browsing
- Do not make them solve exact per-call or cross-route accounting

2. `run_id` survives compaction
- Compaction may rotate `session_id`
- Compaction must not create a new root run for the same task tree
- Later usage after compaction should still append under the same logical root run tree

3. `delegate_task` is sufficient
- Do not add `delegate_profile_task` in this scope
- Continue to use `delegate_task` with ACP/profile override when needed

4. Canonical worker attribution should not depend on worker-local `state.db`
- A worker’s profile-local `state.db` can still be improved separately
- But the canonical answer to task-tree accounting must come from `accounting.db`

5. Unknown usage must remain unknown
- If ACP or another provider/runtime returns no usable usage payload, store an event with `usage_status='unknown'`
- Do not fabricate exact zero-token rows

---

## End-to-end accounting flow

### Case A: manager-only task
1. manager run starts
2. `agent_runs` row created for root run `R1`
3. every manager model call appends `usage_events` row keyed to `R1`
4. task report queries `root_run_id = R1`

### Case B: manager + delegated worker task
1. manager run starts as `R1`
2. manager delegates one bounded task via `delegate_task`
3. child run object is created as `R2` with:
   - `parent_run_id = R1`
   - `root_run_id = R1`
   - `launch_kind = delegate_task`
   - `transport_kind = acp` when ACP is used
4. worker model calls append `usage_events` rows under `R2`
5. report computes:
   - manager only = `run_id = R1`
   - worker only = `root_run_id = R1 AND run_id != R1`
   - total = `root_run_id = R1`

### Case C: compaction during manager task
1. manager run starts with session `S1`, run `R1`
2. compaction happens and session becomes `S2`
3. `run_id` remains `R1`
4. later usage events still append with `run_id = R1` and `root_run_id = R1`
5. `local_session_id` may differ across rows; reports still roll up by run tree

### Case D: model/provider/base URL changes mid-task
1. root run stays `R1`
2. model/provider/base URL change between calls
3. each usage event records the exact provider/base URL/model for that call
4. grouped reports use `usage_events`, not sticky session labels

---

## Scope boundaries

### In scope
- `accounting.db` as canonical exact ledger
- `agent_runs` and `usage_events`
- root run creation
- exact usage event writes for direct calls
- exact or unknown usage event writes for ACP calls
- delegate-task child lineage
- compaction preserving `run_id`
- grouped reporting queries over one task tree

### Out of scope for first PR
- a new manager-facing `delegate_profile_task` tool
- standalone external `hermes -p ...` process propagation unless there is a small, self-contained patch already adjacent to this work
- historical backfill of old sessions
- UI work beyond minimal query/report helpers
- policy decisions on subscription-savings displays in default UX

---

## Required tests

### A. Schema and storage tests
File:
- `tests/test_hermes_state.py`

Tests:
1. creates `agent_runs` and `usage_events`
2. create/get/end agent run round-trip
3. append/get usage event round-trip
4. index and filter behavior for `run_id` and `root_run_id`
5. metadata JSON round-trip

### B. Root run accounting tests
File:
- `tests/run_agent/test_token_persistence_non_cli.py`

Tests:
1. agent init creates root run in accounting DB
2. direct provider run appends exact usage event
3. usage event stores provider/base URL/model from the actual call path
4. manager-only report over one run returns expected totals

### C. Delegate child lineage tests
File:
- `tests/run_agent/test_token_persistence_non_cli.py`
- possibly `tests/tools/test_delegate.py`

Tests:
1. delegate child run gets `parent_run_id` and `root_run_id`
2. delegate child usage event is recorded in same ledger
3. report can separate root run totals from child totals
4. ACP-backed delegate child records `transport_kind='acp'`

### D. ACP unknown-usage behavior tests
File:
- `tests/run_agent/test_token_persistence_non_cli.py`

Tests:
1. ACP path with `usage=None` creates `usage_status='unknown'`
2. no fake exact zero is emitted
3. provider/base URL/model still populate as far as the runtime knows them

### E. Compaction tests
File:
- `tests/run_agent/test_token_persistence_non_cli.py`

Tests:
1. compaction changes `session_id`
2. compaction does not change `run_id`
3. later usage still belongs to same root run

### F. Reporting/query helper tests
Add or extend tests for helper/report layer.

Tests:
1. manager-only subtotal over root tree
2. worker-only subtotal over root tree
3. total subtree sum
4. grouped by `(provider, base_url, model)`
5. mixed exact/unknown usage is labeled correctly

---

## Runtime smoke tests

All runtime smoke tests must use a disposable `HERMES_HOME` under `/tmp`.
Never use live `~/.hermes` for the PoC.

### Smoke test 1: manager-only root run
Objective:
- prove one root run and exact usage event creation in a disposable home

Checks:
- `state.db` exists
- `accounting.db` exists
- one root `agent_runs` row exists
- one or more `usage_events` rows exist for that root run

### Smoke test 2: bounded ACP profile worker
Objective:
- prove `delegate_task(... acp_command='hermes', acp_args=['--profile','superbif-stateless','acp','--stdio'])` creates a child run and child usage in the same root tree

Checks:
- root manager run exists
- child run exists with `parent_run_id = root run`
- child usage event rows exist
- grouped query shows manager and worker usage in separate buckets

### Smoke test 3: compaction continuity
Objective:
- prove session rotation does not break root accounting identity

Checks:
- before compaction note root `run_id`
- after compaction confirm `session_id` changed but `run_id` stayed the same
- later usage event still lands under same root run

### Smoke test 4: route change grouping
Objective:
- prove provider/base URL/model breakdown is per event, not per session row

Checks:
- perform at least two calls across different routes if feasible
- confirm grouped report splits them by event metadata

---

## Reporting/query helpers to add

Suggested helper surface should be stronger than a minimal proof and should be robust enough to justify an upstream PR.

### 1. `get_task_usage_summary(root_run_id)`
Returns top-line exact totals for one root task tree:
- manager-only totals
- worker-only totals
- total subtree totals
- exact-event count
- unknown-usage-event count
- first/last event timestamps
- child run count

### 2. `get_task_usage_breakdown(root_run_id)`
Returns grouped rows suitable for CLI/TUI/reporting by:
- provider
- base_url
- model
- profile_name
- home_id
- launch_kind
- transport_kind
- run_id

Each grouped row should include:
- input_tokens
- output_tokens
- cache_read_tokens
- cache_write_tokens
- reasoning_tokens
- estimated_cost_usd
- exact-event count
- unknown-event count

### 3. `get_task_run_tree(root_run_id)`
Returns a deterministic execution tree view with:
- root run
- child runs
- parent/child links
- launch kinds
- transport kinds
- local session IDs
- source/platform
- model/provider/base_url hints
- started/ended timestamps

### 4. `get_task_session_links(root_run_id)`
Returns the mapping between durable run lineage and local session lineage:
- run_id
- root_run_id
- local_session_id
- parent_session_id if available
- note when compaction rotated session IDs under the same run

This is primarily for diagnostics and proving compaction continuity.

### 5. `get_task_provenance_warnings(root_run_id)`
Returns machine-readable warnings for imperfect data, such as:
- unknown ACP usage events
- worker-local state rows missing token values
- grouped totals based on mixed exact + unknown events
- missing local session links for some runs

These warnings are important so reports stay honest.

These helpers can live in `hermes_state.py` or a small adjacent reporting/helper module, depending on current code organization.

---

## Current upstream findings before implementation

Verified in the fresh checkout on branch `feat/task-tree-accounting`:
- upstream `main` is clean and currently does NOT contain the proposed accounting ledger
- baseline targeted tests pass in the clean checkout
- `delegate_task` already supports ACP overrides via `acp_command` and `acp_args`
- upstream does NOT yet contain:
  - `AccountingDB`
  - `agent_runs`
  - `usage_events`
  - `accounting_db` wiring in `AIAgent`
  - child run lineage for exact task-tree accounting
  - compaction continuity tests at the accounting layer
- upstream currently only persists session-summary token data to `state.db`
- therefore the implementation should start by adding the ledger model itself, not by adding report helpers on top of non-existent ledger data

## Implementation sequence

### Task 1: verify clean branch and baseline
Objective:
- ensure work starts from upstream main on a new feature branch

Status already verified:
- feature branch created from `origin/main`
- targeted baseline tests pass in the clean checkout

### Task 2: add failing ledger schema/helper tests first
Objective:
- encode the new exact-accounting model in tests before implementation

Files:
- modify `tests/test_hermes_state.py`

Tests to add first:
- creates `agent_runs` and `usage_events`
- create/get/end agent run
- append/get usage event
- filter usage events by `run_id` and `root_run_id`
- preserve structured metadata

### Task 3: implement `AccountingDB` and schema
Objective:
- add the canonical exact ledger to `hermes_state.py`

Files:
- modify `hermes_state.py`

Implementation notes:
- separate default DB path should be `get_hermes_home() / 'accounting.db'`
- keep `SessionDB` behavior intact
- `AccountingDB` should be append-oriented and thread-safe
- add indexes for `root_run_id`, `parent_run_id`, and grouped-report fields

### Task 4: add failing root-run accounting tests
Objective:
- prove root task runs are recorded in the ledger

Files:
- modify `tests/run_agent/test_token_persistence_non_cli.py`

Tests to add first:
- agent init creates root run in accounting DB
- direct provider run appends exact usage event
- usage event stores provider/base URL/model from actual call path

### Task 5: wire root-run creation and exact usage writes
Objective:
- make `AIAgent` create a root run and append usage events for direct calls

Files:
- modify `run_agent.py`
- possibly modify `hermes_state.py`

Implementation notes:
- root run should get `run_id = root_run_id`
- `local_session_id` should mirror current `session_id`
- exact usage writes must happen from canonical provider response handling, not transcript persistence

### Task 6: add failing delegate child lineage tests
Objective:
- encode manager/worker task-tree attribution using the existing `delegate_task` path

Files:
- modify `tests/run_agent/test_token_persistence_non_cli.py`
- modify `tests/tools/test_delegate.py` if needed

Tests to add first:
- child run gets `parent_run_id` and `root_run_id`
- child run shares the same accounting DB
- child usage appends under child run
- ACP-backed child gets `transport_kind='acp'`

### Task 7: wire delegate-task child lineage into the ledger
Objective:
- make delegated child runs write exact usage into the shared root-owned ledger

Files:
- modify `tools/delegate_tool.py`
- modify `run_agent.py`

Implementation notes:
- do not add a new manager-facing tool
- pass `accounting_db`, `parent_run_id`, `root_run_id`, `home_id`, and accounting semantics into child `AIAgent`
- rely on existing ACP override path rather than inventing `delegate_profile_task`

### Task 8: add failing ACP unknown-usage tests
Objective:
- preserve honesty when ACP/provider usage data is missing

Files:
- modify `tests/run_agent/test_token_persistence_non_cli.py`

Tests to add first:
- `usage=None` yields one event with `usage_status='unknown'`
- no fake exact-zero semantics
- provider/base URL/model still persist when known

### Task 9: implement ACP unknown-usage handling
Objective:
- write honest accounting rows for ACP calls even when exact usage is unavailable

Files:
- modify `run_agent.py`

### Task 10: add failing compaction continuity tests
Objective:
- make accounting continuity survive session rotation

Files:
- modify `tests/run_agent/test_token_persistence_non_cli.py`

Tests to add first:
- compaction changes `session_id`
- compaction keeps the same `run_id`
- later accounting still belongs to the same root run

### Task 11: implement compaction continuity
Objective:
- ensure accounting identity is run-based, not session-based

Files:
- modify `run_agent.py`
- possibly adjust helper methods in `hermes_state.py`

### Task 12: add failing reporting/query-helper tests
Objective:
- encode the actual reporting outcomes the user cares about

Files:
- modify `tests/test_hermes_state.py`
- add a dedicated reporting-helper test file if cleaner

Tests to write first:
- manager-only totals
- worker-only totals
- total subtree totals
- grouped breakdown by provider/base URL/model/profile/home/transport
- provenance warnings for mixed exact/unknown data
- session-link diagnostics for compaction chains

### Task 13: implement robust reporting/query helpers
Objective:
- provide a report surface strong enough for upstream use, not just a toy proof

Files:
- modify `hermes_state.py` or add a small adjacent reporting module

Helpers to implement:
- `get_task_usage_summary(root_run_id)`
- `get_task_usage_breakdown(root_run_id)`
- `get_task_run_tree(root_run_id)`
- `get_task_session_links(root_run_id)`
- `get_task_provenance_warnings(root_run_id)`

### Task 14: run targeted tests
Objective:
- prove the touched surfaces work

Run at minimum:
- `pytest tests/test_hermes_state.py -q`
- `pytest tests/run_agent/test_token_persistence_non_cli.py -q`
- `pytest tests/tools/test_delegate.py -q`

### Task 15: run disposable runtime smoke tests
Objective:
- verify behavior outside pure mocks

Required smoke tests:
- manager-only root run
- bounded ACP profile worker via `delegate_task`
- compaction continuity
- provider/base URL/model grouping when route changes occur

### Task 16: review for upstreamability
Objective:
- remove any dev-wrapper assumptions
- remove any references to persistent alternate homes
- ensure docs describe disposable `HERMES_HOME` only
- confirm no `delegate_profile_task` abstraction crept into the implementation

### Task 17: commit and push feature branch
Objective:
- produce a clean branch ready for PR

---

## Acceptance criteria

This feature is acceptable when all of the following are true:
- a single root task can be queried exactly by `root_run_id`
- manager-only totals are exact for recorded direct calls
- delegated child totals are exact for recorded child calls
- provider/base URL/model changes during a task are represented by separate usage events
- compaction does not break run-tree accounting identity
- ACP missing-usage cases are labeled `unknown`, not fake exact zeros
- the implementation uses existing `delegate_task` ACP override semantics rather than adding a new manager-facing tool
- runtime PoC tests work under a disposable `HERMES_HOME`
- no changes are made to live `~/.hermes` during development or testing

---

## PR framing

Suggested framing for upstream:
- This change does not redefine session summaries.
- It adds exact task-tree accounting needed to answer per-task manager vs worker usage questions.
- It keeps the existing `delegate_task` ACP override path rather than introducing a new delegation abstraction.
- It treats `accounting.db` as exact run/usage truth and `state.db` as local summary UX.

Suggested PR summary bullets:
- add exact task-tree usage queries over `agent_runs` + `usage_events`
- preserve accounting continuity across compaction by relying on `run_id` / `root_run_id`
- support accurate manager-only vs worker-only totals for delegated tasks
- group per-task usage by provider/base URL/model without relying on sticky session labels
- keep ACP missing-usage semantics honest via `usage_status='unknown'`

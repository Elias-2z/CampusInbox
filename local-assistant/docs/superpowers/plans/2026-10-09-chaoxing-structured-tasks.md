# Chaoxing Structured Tasks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add read-only structured Chaoxing task/exam intake, richer task fields, and explicit merge/conflict resolution.

**Architecture:** A browser-side DOM parser emits normalized `task_center` jobs through the existing authenticated Chaoxing intake. The domain layer validates and persists richer candidates, while proposal classification and confirmation implement merge/conflict semantics. The existing inbox UI exposes conflict resolution without changing the SQLite table layout.

**Tech Stack:** Python standard library, SQLite JSON records, vanilla JavaScript, browser extension content scripts, Node fixture tests.

**Spec:** `docs/superpowers/specs/2026-10-09-chaoxing-structured-tasks-design.md`

## Global Constraints

- Preserve existing data and legacy `link` proposals.
- Never write to the external platform or infer platform submission success.
- Accept only official Chaoxing HTTPS task URLs.
- Use TDD for every behavior change.
- Do not stage or commit unrelated existing user changes in this dirty checkout.

## Review Focus

- Malicious task URLs must be rejected before storage.
- Unknown or malformed deadlines must stay reviewable and never become invented times.
- Legacy tasks missing new fields must continue to render.
- Conflict confirmation without an explicit resolution must fail.
- Platform “submitted” status must remain evidence only, not local submission state.

---

### Task 1: Structured task-center intake

**Files:**
- Create: `browser-companion/tasks.js`
- Create: `browser-companion/task-content.js`
- Modify: `browser-companion/manifest.json`
- Modify: `browser-companion/background.js`
- Modify: `courseinbox/chaoxing.py`
- Test: `tests/test_chaoxing.py`
- Test: `scripts/test-chaoxing-tasks.cjs`

**Interfaces:**
- Produces: `normalize_web_message(body)` support for `kind=task_center` and a validated candidate.

- [x] Write and run failing Python and Node tests for one assignment and one exam.
- [x] Implement the minimal DOM parser, content capture, validation and intake.
- [x] Run the focused Python and Node tests green.

### Task 2: Rich task fields

**Files:**
- Modify: `courseinbox/core.py`
- Modify: `courseinbox/agent.py`
- Modify: `web/app.js`
- Test: `tests/test_core.py`
- Test: `tests/test_agent.py`

**Interfaces:**
- Consumes: Task 1 structured candidate fields.
- Produces: validated candidate and task fields `submission_method`, `teacher`, `source_url`, `platform_status`, `confidence`, `field_evidence`.

- [x] Write and run failing parsing, validation and persistence tests.
- [x] Implement compatible defaults, persistence and UI display/editing.
- [x] Run focused tests green.

### Task 3: Explicit merge and conflict workflow

**Files:**
- Modify: `courseinbox/core.py`
- Modify: `courseinbox/http.py`
- Modify: `web/app.js`
- Modify: `web/style.css`
- Test: `tests/test_core.py`
- Test: `tests/test_api.py`
- Test: `scripts/browser-check.cjs`

**Interfaces:**
- Consumes: Task 2 rich fields.
- Produces: proposal actions `merge` and `conflict`, plus `resolution=current|incoming` confirmation.

- [x] Write and run failing merge/conflict domain and API tests.
- [x] Implement proposal classification and confirmation semantics with legacy `link` compatibility.
- [x] Add conflict inbox tab and explicit resolution controls.
- [x] Run focused tests and browser checks green.

### Task 4: Documentation and full verification

**Files:**
- Modify: `README.md`
- Modify: `docs/API.md`

- [x] Document the structured task-center scope and real-account acceptance boundary.
- [x] Run the full Python and browser suite.
- [x] Restart the local service and verify existing data loads without migration.

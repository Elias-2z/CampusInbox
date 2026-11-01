# 统一新建任务与附件解析 Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task. Review the final adaptation independently before completion.

**Goal:** Integrate Elias-2z/CampusInbox commit `1d02252df342fe4c837b386e16c4b83a5d0cd3f4` task creation and hosted MinerU parsing into the existing Windows integration copy.

**Architecture:** Keep the current stdlib Python service, database, identity safeguards, Vue shell and complete legacy workflows. Adapt the upstream interaction and MinerU protocol rather than replace the backend. Add an authenticated attachment workspace alongside existing tables; preserve originals under the active database directory's `uploads/`.

**Tech Stack:** Python 3.10+ stdlib, SQLite, Vue 3 / TypeScript, existing Playwright fixtures.

**Spec:** User approved integration of the repository update; upstream `docs/task-import.md` at the pinned commit defines file limits, consent, recovery and review behavior.

## Global Constraints

- Work in the existing integration copy; original `D:\Doc\CampusInbox` is not changed. Existing uncommitted work must be preserved.
- Four creation modes: text, image, file, manual. Main creation entry belongs to My Tasks; home links there; inbox remains for review.
- TXT / MD decode locally (UTF-8 / GB18030). Supported image, PDF, Word and PowerPoint use MinerU only with explicit consent. Limit 20 MB per original.
- Read `MINERU_API_KEY` from process environment or root private `.env`; never return or log it. No live cloud requests during verification.
- Store originals, parse drafts, statuses and batch IDs persistently. Concurrent retry must submit once; old responses must not overwrite new generations. Temporary errors retain the current batch.
- Extraction does not create tasks. Correctable extracted text, actual message time and attachment ID enter the existing review workflow. Manual confirmation creates a task directly.
- Preserve date-only deadlines, done/submitted distinction, source evidence, original downloads and existing sync/reminder workflows.
- No automatic merge, push, destructive cleanup or production-data test writes.

## Review Focus

- Malformed files, filenames, content lengths, remote URLs and ZIP members are bounded and rejected safely.
- Closing a parsing window and restarting the service preserve recoverable drafts.
- Repeated/concurrent submission and crashes cannot create duplicate attachment messages or tasks.
- Delayed polls/retries cannot replace newer parse results; errors do not expose tokens or signed URLs.
- Long text, 320px layouts, unconfigured MinerU, stale selection and dialog closing remain usable.

### Task 1: Attachment workspace and hosted parser

- [x] Add failing unittest coverage for local decoding, persistence, consent, limits, authenticated routes, polling/retry concurrency, stale responses, submission idempotency and original download.
- [x] Adapt upstream hosted parser in `courseinbox/mineru.py` using stdlib networking and bounded in-memory ZIP reads.
- [x] Implement `courseinbox/file_imports.py`, authenticated routes in `courseinbox/http.py`, and direct manual creation with domain validation.
- [x] Link originals into message and task detail rendering without changing existing platform download behavior.
- [x] Run focused backend tests and relevant existing import/server/domain tests.

### Task 2: Unified Vue creation flow

- [x] Add a browser test against an isolated test database for text review, local file resume/download, four modes, manual creation, missing cloud config and narrow layouts.
- [x] Adapt `NewTaskModal.vue` to the current API; retain upstream mode selection, preview, consent, progress, saved uploads and editable text.
- [x] Add explicit manual fields and task submission without routing through automatic parsing.
- [x] Move global/home import actions into My Tasks with a single New Task entry, keep legacy details and review flows.
- [x] Update original browser checks to the unified entry and validate type checking, lint, formatting and build.

### Task 3: Integration verification and delivery

- [x] Document configuration, supported formats, recovery, backup requirements and upstream revision.
- [x] Run complete backend tests, frontend checks and isolated browser creation / dialog / business regressions.
- [x] Review final changes and fix material issues; report actual verification limits.

## Execution ledger

- Ruling: Use this existing integration copy rather than create a clean checkout: the required backend and previous UI fixes are uncommitted here; a fresh checkout would omit them. Upstream references are kept under ignored `artifacts/upstream-nov1/` for comparison.
- Ruling: Use stdlib networking instead of importing the upstream FastAPI/SQLModel/httpx stack, to preserve the existing project's dependency-free basic runtime.

- Verification: 678 backend unittests passed; 17 dedicated import tests are included. Frontend lint, formatting, five domain tests, TypeScript and production build passed. Unified creation (including mocked cloud consent/resume and unavailable randomUUID), existing business flow, nine-page dialogs and sample labels passed in isolated browser fixtures. Screenshots visually checked at 1440px and 390px.
- Review: independent reviewer found LAN HTTP randomUUID incompatibility; fixed with getRandomValues and reproduced/verified by browser regression. Additional same-named original-file download regression failed before the upload_id deduplication fix and passed afterward. No real cloud request or production-database test writes were made.

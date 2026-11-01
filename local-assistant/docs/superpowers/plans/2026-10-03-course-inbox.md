# CourseInbox Implementation Plan
Goal: runnable local student task inbox with reviewed message-to-task workflow, calendar and Android ingest.
Spec: docs/superpowers/specs/2026-10-03-course-inbox.md
Architecture: Python stdlib HTTP + SQLite + static HTML/CSS/JS; optional Windows OCR, pypdf, model HTTP; Java Android companion.
Global constraints: synthetic demo; no scraping; no direct model writes; manual approval; no invented time; versioned updates; token auth and package allowlist.

## Task 1: Domain and persistence
Files: courseinbox/core.py, tests/test_core.py.
Interfaces: Inbox(db_path), ingest, confirm(proposal_id, edits), patch_task, task_detail, dashboard, calendar.
Steps: behavior tests first; unittest RED missing module; implement extraction and scoped atomic confirmation; GREEN; record evidence and commit.
Tests: anchored relative/date-only/impossible/missing dates, new extension date, replay/class separation/cancel, stale confirmation, history, persistence, submission distinction, ICS.

## Task 2: Agent, OCR and API
Files: courseinbox/agent.py, files.py, http.py, server.py, scripts/windows-ocr.ps1, tests/test_agent.py, test_api.py.
Interfaces: docs/API.md; identical proposals in both modes; <=8 tool calls; token and same-origin for writes.
Steps: RED; implement providers, tools, file extraction, server; GREEN; generated screenshot OCR; record and commit.
Tests: scope enforcement, unknown/loop tools, offline mode, provenance; authorization, Origin/Host, limits/invalid input, package whitelist/replay, calendar.

## Task 3: UI and Android
Files: web/*, android/*, samples/*, scripts/browser-check.cjs.
Interfaces: Task 2 JSON; editable drafts/original visible; remote token; Java upload matches endpoint.
Steps: browser journey test RED missing page; responsive UI; verify import/approval/extension/status/reminder/calendar/phone event GREEN; Android build if SDK available; record and commit.

## Task 4: Delivery and review
Files: README.md, start.ps1/start.cmd, requirements.txt, Dockerfile, docker-compose.yml, docs/verification/acceptance.md.
Steps: complete unit/browser suites; verify launcher/persistence; fresh reviewer; Important findings RED then GREEN; launch and open; commit.
Review focus: bootstrap/Origin/Host, Android queue, concurrency/stale proposals, raw message injection, calendar, file upload/subprocess, unintended merges, UI claims.
No push/publish requested. New project itself supplies isolation.

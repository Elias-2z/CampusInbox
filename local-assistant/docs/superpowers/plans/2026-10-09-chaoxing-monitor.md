# Chaoxing Account Monitor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a one-click local workflow for logging into Chaoxing, monitoring the message bar, viewing source counts, and safely logging out.

**Architecture:** Extend the existing DesktopSync lifecycle with a destructive local logout that clears only encrypted account session artifacts. Reuse `/api/settings` for persisted message counts and add a dedicated launcher that opens the sync view.

**Tech Stack:** Python standard library HTTP server, SQLite JSON records, PowerShell/CMD launcher, vanilla JavaScript.

**Spec:** `docs/superpowers/specs/2026-10-09-chaoxing-monitor-design.md`

## Global Constraints

- Preserve all received messages, tasks, proposals and accepted message IDs on logout.
- Never expose cookies, credentials or raw session data through HTTP.
- Keep ordinary `start.cmd` behavior unchanged.
- Use TDD for backend and browser behavior.

## Review Focus

- Logout while a worker is running must stop stale worker events.
- Missing session files must make logout idempotent.
- Scoped browser-extension tokens must not call logout.
- Source counts must include task-center messages without changing old fields.
- Quick launcher must open only a fixed local hash route.

---

### Task 1: Account logout lifecycle

**Files:** `courseinbox/desktop_sync.py`, `courseinbox/http.py`, `tests/test_desktop_sync.py`

- [x] Write failing lifecycle and API authorization tests.
- [x] Implement `DesktopSync.logout()` and `POST /api/chaoxing/desktop/logout`.
- [x] Run focused tests green.

### Task 2: Monitoring statistics and UI

**Files:** `courseinbox/http.py`, `web/app.js`, `scripts/desktop-browser-check.cjs`, `tests/test_chaoxing.py`

- [x] Write failing count and browser UI tests.
- [x] Add chat/task/notice counts and account logout controls.
- [x] Run focused tests green.

### Task 3: One-click monitor launcher and verification

**Files:** `server.py`, `start.ps1`, `start-monitor.cmd`, `README.md`

- [x] Add fixed `sync` initial view support without changing normal startup.
- [x] Run syntax, launcher check, full Python and browser regression suites.
- [x] Restart the local service and verify formal data remains readable.

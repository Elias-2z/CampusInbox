# Desktop learning reliability implementation plan

> For agentic workers: use subagent-driven-development for the independent history, sync recovery and Windows notification tasks; the root worker integrates storage, HTTP and browser UI.

**Goal:** Make the locally deployed Windows application keep learning tasks current without old-message clutter or an open web page.

**Authorization:** The user explicitly requested direct implementation of the first four items in the preceding assessment. Work in the existing checkout and preserve its uncommitted changes. No commit or publish is requested.

**Architecture:** Keep SQLite as the source of truth. Automatic intake archives ineligible new task candidates while preserving source evidence. Message visibility and cleared fingerprints support safe cleanup. A persistent local alert service supplies the existing Windows tray application. Existing browser workers gain recovery and coverage diagnostics.

**Constraints:** Confirmed tasks and their source evidence survive cleanup. A source's published time sorts the inbox; unknown publication time uses receipt time. Old publication alone is not grounds for discarding an active task. No deadline is invented. Closed exams and answer/submission pages remain outside automatic navigation. Initial notification setup must not replay the existing backlog. Pausing or exiting must prevent worker resurrection.

## Tasks and checks

- [x] History: reproduce old-expired and ended task drafts; add explicit automatic intake policy; archive only unmatched create candidates; preserve updates and explicit manual re-extraction. Check source evidence and date-only boundaries.
- [x] Inbox: reproduce arrival-order sorting; migrate indexed ordering and visibility; add pagination, snapshot cleanup and cleared-message fingerprints. Check confirmed sources, pending drafts, concurrent incoming messages, export and replay.
- [x] Sync: reproduce process death and misleading detail completeness; add bounded recovery, freshness/coverage and manual recheck. Check pause, epoch, network failure, browser closure and restart using isolated fixtures.
- [x] Desktop alerts: add persistent first-run baseline, polling/ack/settings APIs; extend tray polling and safe click-through; check deadline changes, submission/cancellation, wakeup callback and restart deduplication. Physical Windows sleep and actual popup visibility remain live acceptance checks.
- [x] Integration: add browser flows for pagination/cleanup/archive/diagnostics/deep links; run Python, desktop and browser regression suites; build the EXE; back up the real database before applying migrations and load the verified service without synthetic production messages.

## Review focus

1. First sync of historical messages must not create an avalanche of task reminders.
2. Cleanup after confirmation must retain original task evidence and ignore messages that arrived while confirmation was open.
3. A worker's last historical success must not masquerade as current progress or complete detail coverage.
4. Only successful notification handoff is acknowledged; retry and restart must not produce repeated popups.
5. Date-only deadlines, missing deadlines, completed tasks and old-source updates need distinct behavior.

## Verification boundary

Controlled tests establish program behavior, not coverage of every school layout or guaranteed Windows/OEM notification delivery. Record actual commands, results, local deployment checks and remaining live-account checks in the verification document.

# Course notice sync implementation plan

> Execution: superpowers:executing-plans, inline. Preserve the existing checkout and uncommitted changes; do not commit or publish.

**Goal:** Synchronize the logged-in Chaoxing desktop notification inbox into CourseInbox without the Android companion.

**Architecture:** Extend the existing Manifest V3 component. Read actual notification list DOM and the same-origin detail URLs present on its rows; poll the list once per minute. Persist complete notification jobs in the existing offline queue; use the dedicated loopback token to store evidence and generate pending drafts. Keep chat support intact. Read task-instruction attachments when their filename indicates instructions; preserve the names/links and explicit extraction status for all attachments.

**Observed page contract:** `notice.chaoxing.com/pc/notice/myNotice`; `.dataBody_item[data-id]`, `.openNotice[data-url][data-sendtag="0"]`, `.notice_title`, `.notice_time`, `.receiverName`; detail `#title`, `.noticeContent`, `.createName`, `.createTime`, `.sendsUser`; encoded `iframe.attach-insertCloud[name]` attachment metadata. These were verified through the user's logged-in page, including the screenshot's exact notice. Never use private platform API endpoints or read browser credentials.

**Files:** Add `browser-companion/notices.js`; update manifest, content worker, background worker, options and packaging. Extend `courseinbox/chaoxing.py`, `http.py`, `files.py`, `web/app.js` and source connection UI. Tests in `scripts/test-chaoxing-web.cjs`, `tests/test_chaoxing.py`, `tests/test_files.py`; browser acceptance uses a sanitized fixture based on observed structure.

**Constraints:** Only explicit inbox/detail routes and known download links. Only inbound notices. Details fetched with the browser's same-origin login session, no credential export. Do not auto-confirm tasks, edit/delete/send platform content or crawl other accounts. Source timestamps are authoritative; unknown time remains unknown. Documents are bounded by 8 MiB and extracted text limits. Failed downloads/extraction never masquerade as success. Notification identity is namespaced and stable, separate from chat IDs.

## Tasks

- [x] 1. Write failing notice/time/attachment/parser and API tests. Verify RED. Implement bounded notification payload validation, source metadata and safe DOCX extraction; verify GREEN.
- [x] 2. Write failing queue/source/disabled sync tests. Implement DOM list/detail parser, periodic refresh, bounded attachment preparation and persistent retry. Verify GREEN and chat regressions.
- [x] 3. Provide a dedicated desktop sync page, upgrade component to 0.2.0, rebuild ZIP and update instructions. Verify in an isolated browser/database using the observed DOM contract.
- [x] 4. Run all tests and one fresh code review, repair significant issues. Restart the formal service with original settings; read one real course notice into a pending draft and verify replay, exact text, timestamp and attachment status. Record installation/continuous-sync acceptance separately from software/one-shot evidence.

## Review focus

- Disabled sync never fetches course details or attachments.
- Login/DOM changes and failed details preserve retry capability, not title-only fake success.
- Chat and notice identities do not collide; duplicates do not create extra messages/tasks.
- Unknown publication time never turns tomorrow into a receipt-time deadline.
- Original HTML/scripts and unsafe attachment URLs cannot escape the source allowlist or execute in the local UI.

## Decisions and evidence

User requested implementation after the desktop/browser route and its current limits were described. Continue authorized reversible work without another design approval gate. Real extension loading and broader browser permission acceptance remain a final user-controlled step.

Delivery evidence: docs/verification/course-notice-sync.md. User requested test stop after implementation and real import; no further tests performed. User-browser component installation and continuous sync acceptance remain explicitly separate.

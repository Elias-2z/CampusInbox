# Desktop capture, media and acceptance implementation plan

> For agentic workers: use superpowers:executing-plans inline and one scoped read-only review at the end. No per-task delegation.

**Goal:** Implement the proposed priority items 1, 2 and 4 in the current desktop checkout: recover official notice bodies, ingest image/scanned-PDF evidence locally, and make end-to-end acceptance executable and explicit.

**Authorization:** The user said “直接实现” after the PPT comparison recommended these three priorities. Existing project files, database records, rule-first extraction and manual task confirmation remain authoritative. No publication, teacher-message sending, commit, dependency download or synthetic production ingestion is authorized or needed.

**Architecture:** Extend existing notice parsing and worker recovery, using the official page's own identity-checked GET detail response when its DOM is incomplete. Treat supported inline images as bounded source attachments, retain unsupported media as a visible gap, and use Windows local OCR/PDF rendering. Add read-only acceptance snapshots plus an isolated task-lifecycle scenario; reports cannot label simulated fixtures or notification API calls as real publication/display acceptance.

**Tech stack:** Existing Python standard library, SQLite, Node/Playwright and native Windows OCR/PDF APIs. Optional pypdf remains supported for text PDFs; no mandatory new dependency.

## Constraints and review focus

- Work in D:\Doc\course-inbox and preserve the dirty checkout; no Git mutation or unrelated refactor.
- Official identity, bounded downloads, no redirect/cookie forwarding to arbitrary URLs; never print credentials or raw account URLs.
- Failed/partial OCR does not become complete coverage. New evidence can improve pending drafts, but cannot silently edit confirmed tasks or discard old sources.
- Limits: 8 MB per file/batch, existing 30 PDF page ceiling, bounded OCR rendering and subprocess timeout. Partial output is explicitly identified.
- Real school publication, student visibility, Windows notification display/click and sleep/reboot results remain pending until directly observed; do not fabricate a corpus or >=90% score.

## Tasks

- [x] Add failing parser/worker tests for image-only notices and official JSON detail fallback; implement safe image evidence, fallback and accurate media coverage. Include retry/restart preservation.
- [x] Add failing file/sync tests for scanned PDFs and partial OCR. Implement bounded Windows PDF OCR and propagate partial status; verify real local image/PDF extraction.
- [x] Add isolated end-to-end acceptance for create, duplicate, update, conflict, cancellation and desktop reminder queue; provide a read-only report command for real current-account intake/coverage and explicitly pending human observations.
- [x] Run focused and full domain/desktop/browser regression, attempt one scoped review, back up/restart the desktop and verify production data/health read-only. Document exact results and remaining real-platform acceptance. Review limitation is recorded below.

## Ledger

- Context: existing worker has bounded pagination/cursors, retries, encrypted sessions, source coverage and tray alerts. Preserve these; implementation targets missing paths rather than rebuilding them.
- Context: browser-companion/notices.js already has identity-checked extractData(), but worker only calls extractDetail(). Inline images are counted as gaps but never extracted; extract_file rejects scanned-only PDFs.
- Scope: the remaining PPT items (model comparison, 100–200 manually reviewed samples, other connectors and independent installer) are subsequent work; none will be claimed achieved by this plan.
- Review: scoped reviewer identified a separate-line OCR deadline omission, now fixed with a regression. Review then hit its usage limit before full completion; no clean independent review is claimed. Final inspection and automated regression continue inline.
- Compatibility: image extraction is an explicit desktop parser option, preserving existing mobile/extension behavior. No APK or extension ZIP was rebuilt in this desktop round.
- Preservation: backup includes the encrypted sync directory. All 11 production business tables matched the backup before restoring the desktop; visible tasks remain 7 and known duplicate pairs remain resolved.
- Validation: 250 Python tests, 47 Windows desktop tests, all 18 browser scripts and actual native Windows scanned-PDF OCR passed. Production health and the new report button were checked read-only. Actual source coverage remains partial/list-only in places; real release, notification display/click and sleep/reboot acceptance remain pending.

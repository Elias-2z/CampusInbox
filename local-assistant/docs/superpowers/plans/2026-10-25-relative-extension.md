# Relative deadline extension implementation plan

Goal: Turn a standalone published instruction such as 上次报告再延三天 into a versioned confirmation draft based on the original confirmed deadline.

Architecture: Recognize narrowly bounded affirmative duration instructions. Resolve inside the proposal transaction using course/class scoped tasks and deterministic date arithmetic. Preserve request, baseline deadline/version and an event receipt. Do not ask Qwen to invent an anchor. Existing optimistic confirmation and publication ordering remain authoritative.

- [x] Add failing sequence tests for single match, date precision, ambiguous scope, unknown deadline/publication, stale version and duplicate replay.
- [x] Add parser/request resolver module and integrate proposal creation, confirmation receipts, read-only views and re-extraction.
- [x] Add compact calculation/review UI and isolated browser acceptance.
- [x] Run full tests, activate with backup/hash preservation, document supported boundaries.

Constraints: No changes to existing tasks before human confirmation. No scan/receipt date as anchor. No fuzzy task choice or latest-task guess. No changes to real annotation answers or held-out partitions. Preserve original checklist and submission fields.

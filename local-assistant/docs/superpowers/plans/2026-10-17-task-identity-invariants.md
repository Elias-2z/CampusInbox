# Task identity invariants implementation plan

> For agentic workers: use executing-plans for this existing matching flow; requesting-code-review supplies the final read-only review.

**Goal:** Prevent another confirmed task for a known Chaoxing platform identity across display-label changes, source order, pending drafts, restarts and concurrent local writers. Preserve evidence and hold uncertain matches for review.

**Authorization:** The user explicitly requests thorough prevention of the duplicate-task issue after the first fix. Continue in the named checkout, preserve unrelated changes, and back up the local database before applying the new schema. No commit or publication is requested.

**Design:** Keep exact platform identity separate from display labels. A SQLite identity registry owns each complete course/class/type/task identifier and is updated in the same immediate transaction as task confirmation and source attachment. Validate conflicting URL identity fields instead of silently falling back to a name. Exact equivalent pending proposals share one draft and retain source links; material differences remain separate versions for review. Weak or similar name matches never auto-merge and cannot create a copy while a possible existing task needs disambiguation.

**Boundary:** Complete platform IDs permit a hard uniqueness invariant. Source text with no stable ID cannot establish universal semantic identity; preserve it and expose uncertainty rather than claim certainty or silently drop it. Different complete task/class/type IDs remain distinct. Missing deadline does not itself block a task.

## Tasks

- [x] Reproduce matchable-flag bypass, label-independent uniqueness, conflicting ID aliases, same-ID concurrent confirmation, and existing linked-ID conflicts. Add deterministic identity parsing and persistent claims with transaction guards.
- [x] Reproduce equivalent pending drafts from multiple sources; fold only equivalent versions, preserve all evidence through confirmation and cleanup, and keep deadline/material changes reviewable.
- [x] Reproduce weak-name/partial-ID duplicate creation; add explicit possible-duplicate review data, UI feedback and backend creation guards while preserving valid distinct tasks.
- [x] Run focused and complete domain regression plus relevant browser flows, obtain scoped read-only review, verify migration on a production copy, back up and restart the local desktop service, and verify task/source preservation and live UI.

Result: 237 Python tests, 47 desktop tests and relevant browser flows passed. Schema migration preserved all existing rows. A separately verified legacy BMP pair was repaired after its own backup and copy rehearsal; all original evidence and old links remain. See docs/verification/task-identity-invariants-2026-10-17.md for the precise guarantee boundary and local deployment evidence.

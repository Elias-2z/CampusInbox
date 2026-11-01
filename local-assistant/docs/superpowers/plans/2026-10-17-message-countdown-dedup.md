# Task-center countdown message deduplication

The user reported many repeated message cards. A read-only production check found 55 records each for two assignments; their task identities and material fields matched while countdown text and transport IDs changed.

Implement inline in the dirty checkout; do not create a worktree, delegate, publish, or modify task progress. Fix the parser's revision calculation and add server-side content-version keys constrained by complete course/class/task IDs. Ignore only live countdown fragments, retaining source snapshots and explicit deadlines, instructions, attachments and status changes. Weak or conflicting task IDs cannot collapse records.

Add an indexed nullable `messages.capture_key`. Back up before migration, populate legacy keys and hide redundant visible copies while retaining every raw row, source link and history. Preserve cleared-message tombstones across changed transport IDs. Test on temporary databases and rehearse migration on a production copy before restarting the desktop.

Validation: regression tests for countdown replays, genuine changes, class/task isolation, cleared replays and migration preservation; Python suite plus relevant parser, task-state and inbox browser tests; production backup, restart and read-only UI/data check. Native Windows notifications and unrelated source completeness are outside this fix.

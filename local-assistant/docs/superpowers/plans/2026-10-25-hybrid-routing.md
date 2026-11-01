# Conservative hybrid routing implementation plan

Goal: Reduce avoidable model changes on fully specified templates, clear provably misclassified dates when the deadline is explicitly unannounced, and expose automatic evaluation error types.

Architecture: A strict full-text template guard compares every supported field against the rule candidate. Any extra prose, condition, ambiguity, update, multiple task, or mismatch goes to the model. Date removal only changes a human-confirmed draft proposal, never an existing task, and preserves both values. Evaluation error types derive from measured matches, not generated answers.

Tech stack: Existing Python, unittest, browser JavaScript.

- [x] Add red tests for safe routing and refusal boundaries; implement a focused routing module.
- [x] Add red tests for explicitly unannounced deadlines and preserve structured platform values.
- [x] Add red tests for per-case evaluation error types; display them in existing case lists.
- [x] Run full regression, browser checks and actual Ollama diagnostic; activate with backup and table hash verification.

Constraints: Preserve all original evidence, prior annotations, routing traces, rule fallback, and human confirmation. Do not claim improved general accuracy without reviewed held-out evidence. Do not change existing dataset partitions.

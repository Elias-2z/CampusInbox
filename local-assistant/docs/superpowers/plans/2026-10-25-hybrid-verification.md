# Hybrid verification implementation plan

Goal: Evidence-checked model corrections and a prospective holdout evaluation workflow.

Architecture: Keep proposal confirmation and source-specific rule parsing. Let Qwen re-examine existing candidates. Record both values on conflicts; only prefer a model deadline when deterministic source semantics reject the rule date and verify the model date. Never modify confirmed tasks here. Human review is not model accuracy.

Tech stack: Existing Python unittest backend, vanilla JS UI, SQLite read-only evaluation.

- [x] Add failing tests for wrong publication-as-deadline correction, ambiguous conflict preservation and persisted audit evidence.
- [x] Implement bounded deadline arbitration and compact conflict display; retain fallback and human confirmation.
- [x] Add failing tests for reproducible grouped dataset partitioning, known-label exclusion and split selection.
- [x] Implement CLI and UI preparation and held-out evaluation, with unreviewed labels excluded and contamination limits explicit.
- [x] Run regression tests, isolated model probes, create local annotation work files, and activate with backup/hash checks.

Constraints: No automatic human labels. No claim that 10 samples establish superiority. No model-based broad similarity merges. Publication is distinct from deadline; receipt time is not a date anchor.

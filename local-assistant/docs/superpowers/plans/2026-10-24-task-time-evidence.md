# Task Time Evidence Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task in the current checkout.

**Goal:** Keep publication and deadline authoritative, excluding detection/receipt time from semantic decisions and UI.

**Architecture:** A shared publication helper and deadline evidence resolver serve draft review, confirmation, inbox presentation and inventory. Aggregate by stable task identity, permitting exact course/class/name fallback only when unambiguous. Resolve only unique explicit deadlines into reviewable drafts; preserve ignored decisions, conflicts, original evidence and optimistic version guards.

**Tech Stack:** Python/SQLite backend; existing JavaScript UI and Playwright tests.

- [x] Reproduce unknown publication with absolute deadline, scan versus publication ordering, both source arrival orders, ignored/conflicting/ambiguous evidence, and confirmed inventory resolution.
- [x] Implement shared time semantics, publication-independent absolute extraction and review-time source aggregation; attach adopted evidence only after confirmation.
- [x] Use publication for chronology, expose publication/deadline separately, and reconcile inventory against explicitly confirmed deadlines without hiding unread media.
- [x] Run focused Python and browser regression; back up and activate locally, verifying unchanged business data and current UI. Document measured and unverified boundaries.

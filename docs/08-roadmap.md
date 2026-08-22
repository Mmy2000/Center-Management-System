# 08 — Implementation Roadmap

12 phases. Each is shippable and independently verifiable. Phases 1–10 are the MVP; 11–12 harden and deploy. Estimates assume one focused developer (or an AI agent working task-by-task from [09-tasks.md](09-tasks.md)).

---

## Phase 1 — Project Foundation  ·  ~2 days  ·  TASK-001 → 006

**Objectives.** Turn the bare `django-admin startproject` skeleton into a structured, tested, lintable project without changing any behaviour yet.

**Tasks.** Split settings into `base/dev/prod/test`; `django-environ` + `.env.example`; `apps/` package layout with the ten app labels; `core` app with `TimeStampedModel`, `Setting`, `AuditLog`, policy defaults and the cached registry; base templates (RTL shell, Bootstrap 5 RTL, sidebar, toast/modal partials); `static/js/http.js` (CSRF-aware fetch wrapper) and the `@ajax` decorator + envelope; pytest/ruff/black config; `/healthz/`, `/readyz/`.

**Dependencies.** None.

**Deliverables.** Runnable project on the new settings layout; `core` migrations; first green CI run.

**Acceptance.** `manage.py check` clean on dev and prod settings; `pytest` green; `/healthz/` returns 200; an RTL page renders with the sidebar; `settings_registry.get("attendance.late_after_minutes")` returns 15 with zero DB rows present.

---

## Phase 2 — Authentication & RBAC  ·  ~2 days  ·  TASK-007 → 011

**Objectives.** Custom user, roles, permissions, login flow, audit plumbing. **Must land before any other model migration exists.**

**Tasks.** `accounts.User(AbstractUser)` + `AUTH_USER_MODEL`; `Role` choices + `seed_roles` (Django groups & permissions per 05 §G.5); login/logout/password-change views with throttling; `@require_perm` decorator + `PermissionRequiredMixin` conventions; `accounts.scoping.visible_groups(user)`; audit middleware (thread-local user/IP) + `core.audit.record()`.

**Dependencies.** Phase 1.

**Deliverables.** Working login, five seeded roles, a permission-matrix test skeleton.

**Acceptance.** Each seeded role can reach exactly the URLs in the matrix and no others; six failed logins in a minute are throttled; `record()` writes an `AuditLog` with actor and IP.

---

## Phase 3 — Educational Structure  ·  ~3 days  ·  TASK-012 → 019

**Objectives.** The configurable academic hierarchy: stage → grade → subject → offering → group → schedule.

**Tasks.** Models + migrations + constraints from 02 §3; admin registrations; CRUD pages and AJAX endpoints; dependent-select feeders; `seed_academics --demo`; instructor CRUD.

**Dependencies.** Phase 2.

**Deliverables.** Full academic CRUD UI; a demo hierarchy matching the brief's Physics/Math example.

**Acceptance.** Nothing about stages/grades/subjects/groups is hard-coded anywhere; creating a Group requires an existing offering; `UNIQUE(grade, subject)` and `UNIQUE(grade_subject, name)` are enforced by the DB; the dependent selects chain correctly.

---

## Phase 4 — Student Management  ·  ~3 days  ·  TASK-020 → 025

**Objectives.** The durable student record and its list/profile UI.

**Tasks.** `Student` model + `student_code` generator + photo handling; list with server-side pagination, filters, debounced search; profile shell with tabs; create/edit forms; status transitions with reason + audit; typeahead endpoint.

**Dependencies.** Phase 3.

**Deliverables.** Student CRUD, list, profile (tabs stubbed for later phases).

**Acceptance.** 2,000 seeded students paginate and search in < 300 ms; `student_code` is unique under concurrent creation; the model has no `group_id`, no `card_id`, no `is_paid`; status changes appear in the audit log.

---

## Phase 5 — Pre-Printed Card Management  ·  ~3 days  ·  TASK-026 → 033

**Objectives.** Card inventory, lifecycle and history — with no attendance yet.

**Tasks.** `StudentCard` + `CardAssignment` + constraints; `import_cards` / `generate_cards` commands; batch export (CSV + printable QR sheet); inventory UI with status tiles; lookup / assign / mark-lost / disable / replace services and endpoints (all locked + audited); card timeline on the student profile.

**Dependencies.** Phase 4.

**Deliverables.** A full card lifecycle usable without any lesson existing.

**Acceptance.** Importing a CSV with a duplicate token rolls back entirely; assigning an assigned/lost/disabled card returns the four distinct error codes; concurrent assignment of one card produces exactly one winner; replacement moves the student to the new card, closes the old history row, and leaves prior data untouched.

---

## Phase 6 — Groups & Student Assignments  ·  ~3 days  ·  TASK-034 → 039

**Objectives.** The assignment model that makes "usual group" meaningful and historical.

**Tasks.** `StudentGroupAssignment` + denormalized `grade_subject` + partial unique index; assign / end / transfer services; group roster UI; student "Groups" tab with active + history timeline; capacity display; multi-subject enrollment step in the wizard.

**Dependencies.** Phase 5 (wizard), Phase 3.

**Deliverables.** Students assigned to multiple subjects; transfers preserving history.

**Acceptance.** A second ACTIVE assignment for the same offering is rejected by the DB; a transfer produces two rows (one ENDED, one ACTIVE) and never mutates the old group id; a student can hold Physics-A, Math-C and Chemistry-B simultaneously.

---

## Phase 7 — Lessons  ·  ~3 days  ·  TASK-040 → 045

**Objectives.** Concrete sessions with attendance windows and a lifecycle.

**Tasks.** `Lesson` model + constraints; window/late defaults resolved from group → settings; `generate_lessons` (idempotent, holiday-aware); lessons list with tabs; lesson detail shell; open / complete / cancel actions (completion sweep stubbed until Phase 8).

**Dependencies.** Phase 6.

**Deliverables.** A month of lessons generated for every group in one action.

**Acceptance.** Re-running generation creates nothing new; DST-crossing weeks produce correct local times; `UNIQUE(group, scheduled_start)` holds; invalid state transitions are refused with a clear code.

---

## Phase 8 — QR Attendance  ·  ~5 days  ·  TASK-046 → 058  ·  **the core phase**

**Objectives.** The scan path, the state machine, and the scanner UI.

**Tasks.** `Attendance` + `AttendanceEvent` + all constraints/indexes; `result_codes.py`; eligibility classifier; scan service (gates 1–10) with idempotency; check-out and duplicate rules; manual correction + approval flows; lesson completion sweep (auto-checkout + absentee materialisation); scanner console (HID + camera, sound, colour, live feed); lesson dashboard; student attendance tab; `assertNumQueries` + concurrency tests.

**Dependencies.** Phase 7.

**Deliverables.** A lesson can be run end-to-end with physical cards.

**Acceptance.** Every row of 03 §D.9 behaves as specified; alternative-group scans never touch assignments; two threads scanning one card yield exactly one attendance row; a replayed `idempotency_key` returns the original response; the scan endpoint issues ≤ 6 queries and answers in < 120 ms locally.

---

## Phase 9 — Monthly Payments  ·  ~4 days  ·  TASK-059 → 068

**Objectives.** The financial ledger, independent of attendance.

**Tasks.** `MonthlyCharge` (+ generated `balance`) and `Payment` models; charge generation (command, dry-run preview, button, Beat job); `record_payment` with row lock + `F()`; refunds, waive, cancel, discount; receipt numbering + printable receipt; payments workspace UI; student financial tab; nightly `recalculate_charges` drift check; the optional payment gate wired into the scan service behind its setting.

**Dependencies.** Phase 8 (only for the gate; the ledger itself depends on Phase 6).

**Deliverables.** Monthly billing and cash collection in daily use.

**Acceptance.** Three partial payments walk the status correctly; re-running generation never double-bills; a fee rise does not alter issued charges; concurrent payments keep `total_paid` exact; refunds leave the original row intact; with the gate off, no payment query runs during a scan.

---

## Phase 10 — Dashboards & Reports  ·  ~4 days  ·  TASK-069 → 076

**Objectives.** Turn the data into the numbers the owner actually asks for.

**Tasks.** Dashboard aggregation service + tiles (§49); group and lesson dashboards (§36–37); report framework (filter bar → table → export); the ten attendance reports and nine financial reports of §48; alternative-group report (§35) as a first-class screen; CSV/XLSX/PDF exporters; async export for large sets.

**Dependencies.** Phases 8, 9.

**Deliverables.** The full reporting suite with export.

**Acceptance.** Every report supports the full filter set; totals reconcile exactly with the underlying tables (a test asserts report totals == ledger totals); the dashboard renders in < 500 ms with a year of demo data; exports open cleanly in Excel with Arabic intact (UTF-8 BOM for CSV).

---

## Phase 11 — Testing, Hardening & Security  ·  ~4 days  ·  TASK-077 → 082

**Objectives.** Close the gap between "works" and "trustworthy".

**Tasks.** Complete the 06 §K.2 case list; permission-matrix parametrized suite; concurrency suite on Postgres; rate limiting; CSP + security headers; Sentry scrubbing; `check --deploy` clean; load test of the scan endpoint; accessibility/RTL pass; Arabic translation catalogue completed; `django-otp` for super admins (optional).

**Dependencies.** Phase 10.

**Deliverables.** Green CI with coverage floors; a written security review.

**Acceptance.** Coverage ≥ 90% on services / ≥ 75% overall; no `qr_token` in any log, response or event row (asserted by test); 100 concurrent scans sustain p95 < 500 ms; every URL rejects anonymous access.

---

## Phase 12 — Production Deployment  ·  ~3 days  ·  TASK-083 → 088

**Objectives.** Run it for real, safely, with a way back.

**Tasks.** Dockerfile + compose (prod); nginx + TLS; Postgres tuning; Redis; Celery worker/beat; backup container + **restore drill**; monitoring/alerting; GitHub Actions deploy; `RUNBOOK.md`; go-live checklist (07 §L.8); staff training material in Arabic.

**Dependencies.** Phase 11.

**Deliverables.** A live system with backups, monitoring and a rollback path.

**Acceptance.** A backup has been restored into a scratch database and verified; a deploy and a rollback have both been executed successfully; alerts fire in a drill; the first real lesson is scanned with paper as a parallel fallback and the two agree.

---

## Sequencing notes

- Phases 1–2 are order-critical: **the custom user model must exist before the first migration of any other app.**
- Phase 9's ledger only needs Phase 6; if two people work in parallel, one can build payments while the other builds attendance — they meet only at the optional gate.
- Nothing before Phase 8 may write an `Attendance` row, and nothing in Phase 8 may write a `StudentGroupAssignment` row. These two rules keep the principles intact under schedule pressure.
- Do not start Celery before Phase 9; do not start offline sync at all in the MVP (the `idempotency_key` contract already reserves the seat).

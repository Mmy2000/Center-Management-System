# 01 — Requirements Analysis & Domain Model

## A. Product requirements analysis

### A.1 What this business actually is

An Egyptian *senter* (سنتر) is a retail service business wearing academic clothes. Three facts drive the whole design:

1. **The unit of sale is (student × subject × month), not (student × school year).** A student buys Physics from you and Chemistry from someone else. Every financial object must therefore hang off a *subject*, never off the student globally.
2. **Group membership is soft.** The student "belongs" to Group A because that is when they usually come. Attending Group B on a Wednesday is normal, frequent, and must not be treated as an exception that corrupts data. This is the requirement most school ERPs get wrong, and it is why `assigned_group` and `attended_group` are two separate columns on the attendance row.
3. **Cash arrives in fragments.** 200 today, 150 next week, the rest maybe never. A boolean `is_paid` cannot represent it; only a ledger can.

Everything else (cards, QR, dashboards) is operational plumbing around those three facts.

### A.2 Functional scope of the MVP

Confirmed in scope: academic structure (stage → grade → subject → group), student CRUD + profile, pre-printed card inventory + assignment + replacement, lessons and scheduling, QR check-in/check-out with normal / alternative / make-up detection, late detection, duplicate protection, manual correction with audit, monthly charges, multi-payment ledger, outstanding balances, the report set in §48 of the brief, and the operational dashboards.

Explicitly **out** of the MVP (architecture must not block them): parent/student mobile apps, SMS/WhatsApp/push, online payments, NFC/RFID/face recognition, teacher payroll, multi-branch/multi-tenant, offline scanning.

### A.3 Assumptions taken (each is a decision you can overturn — see "impact")

| # | Assumption | Why | Impact if wrong |
|---|-----------|-----|-----------------|
| A1 | **Billing granularity = (student, grade-subject, billing month)** — one charge per subject per month, flat monthly fee, no per-lesson pricing. | Matches how most centers price. | Per-lesson pricing = add `charge_basis` to `Group` plus a per-lesson line table; `MonthlyCharge` stays as the aggregation point. Contained change. |
| A2 | **No pro-rating.** A student joining on the 20th owes the full month unless an admin applies `discount_amount`. | Centers negotiate ad hoc, not algorithmically. | Add `proration_policy` to `GradeSubject` and compute `amount_due` at generation. Contained. |
| A3 | **The fee is snapshotted onto the charge at generation time.** Raising a group's fee never changes already-issued charges. | Principle 5 (history immutable). | None — required for audit regardless. |
| A4 | **One ACTIVE assignment per (student, subject-offering).** A student cannot be *permanently* in Physics Group A and Group B at once; the alternative / make-up mechanism covers temporary dual attendance. | Prevents double-billing and makes "usual group" a single indexed row on the hot path. | If a center genuinely double-enrolls, drop the partial unique index and let `is_default` break the tie in `resolve_assigned_group()`. One function changes. |
| A5 | **Student's stage is derived from their grade**, not stored twice. | Two writable columns guarantee drift. | None. Templates read `student.grade.stage`. |
| A6 | **Timezone `Africa/Cairo`, DST-aware, all datetimes tz-aware.** Lesson windows are stored as datetimes, not date+time pairs. | Egypt reinstated DST in 2023; naive `date + time` comparisons break twice a year. | None. |
| A7 | **Arabic-first RTL UI**, with `name` / `name_ar` on academic entities. | Front-desk staff. | None; `django.utils.translation` wired from day one. |
| A8 | **Single center, single active academic year.** Groups carry an optional `academic_year` label; charges carry `billing_month`, so year rollover needs no schema change. | Multi-branch is explicit future scope. | Multi-branch = nullable `branch_id` on Group/Student/Payment plus a scope filter in middleware. Designed for, not built. |
| A9 | **Cards may already be printed by a vendor** (import their token list) **or not yet printed** (system generates tokens, exports CSV/PDF for the printer). Both supported. | The brief says "pre-printed"; reality is often "about to be printed". | None. |
| A10 | **Payment policy defaults to NON-blocking** (`payments.enforce_on_attendance = false`). Unpaid students are warned on screen, never turned away by software. | Turning a paying customer away at the door because of a config default is a business-losing failure mode. | Flip one setting. |
| A11 | **Absences are materialised** as attendance rows when a lesson is completed, not inferred at query time. | Turns every report into an indexed scan instead of an anti-join against assignments. | None. |
| A12 | Instructors are a first-class entity with an **optional** login. | Many center instructors never touch the system. | None. |

### A.4 Ambiguities in the brief, and how they are resolved

1. **"Student has Educational Stage and Grade"** (§2) — storing both is denormalized. → Store `grade` only; expose `stage` as a property. (A5)
2. **"MonthlyCharge → Subject/Enrollment and Group"** (§27) — which is authoritative? → The charge is keyed on `(student, grade_subject, billing_month)` (unique). `group` is a **non-authoritative snapshot** of where the student studied when it was issued. Moving groups mid-month neither splits nor duplicates the charge.
3. **"A student should not have multiple *active* attendance records for the same lesson"** (§42) — "active" is slippery. → Hard `UNIQUE(lesson, student)`. Cancellation is a *state* on that row, not a second row. This also makes the scan endpoint naturally idempotent under race.
4. **Attendance type vs status** (§23) — the brief says do not merge them. → Three orthogonal columns: `state` (lifecycle), `status` (outcome), `attendance_type` (provenance). See 03.
5. **ABSENT is not a scan outcome** — nobody scans to be absent. It is produced by lesson completion. (A11)
6. **"Make-up" vs "alternative group"** — physically the same event (student sitting in a foreign group). → Distinguished by *intent*: `MAKEUP` when the scan is linked to a specific missed lesson (`makeup_for_lesson_id`), `ALTERNATIVE_GROUP` otherwise. The operator picks; the system defaults to `ALTERNATIVE_GROUP` and suggests `MAKEUP` when the student was absent from their own group in the same billing month.
7. **QR token secrecy** (§15) versus the token being printed on a card anyone can photograph. → The token is a *low-value bearer credential*: it **identifies**, it does not **authorize**. A scan is only possible from an authenticated operator session against an open lesson. See 06 §I.
8. **Refunds** (§26) — a refund is not a status, it is a transaction. → `Payment.kind ∈ {PAYMENT, REFUND}`; status is recomputed from the ledger.

### A.5 Non-functional requirements

- Scan round trip < 500 ms p95 (target < 120 ms on LAN, ≤ 6 queries).
- Correct under concurrent scanners (two devices, same card, same millisecond).
- Every financial and attendance mutation attributable to a user, with before/after values.
- Operable by a non-technical receptionist; the scanner screen must work with zero clicks.
- Data envelope: ~2,000 students, ~120 groups, ~30 lessons/day, ~600 scans/day, ~40k attendance rows/year. This is a *small* dataset — index it correctly and never shard anything.

---

## B. Domain model

### B.1 Bounded contexts → Django apps

```text
apps/
├── core/        Setting registry, AuditLog, TimeStampedModel, permission helpers, i18n/RTL utils
├── accounts/    User (custom), roles, auth views, RBAC decorators & mixins
├── academics/   EducationalStage, Grade, Subject, GradeSubject, Instructor, Group, GroupSchedule
├── students/    Student, StudentGroupAssignment
├── cards/       StudentCard, CardAssignment          (inventory + lifecycle)
├── lessons/     Lesson, lesson generation from GroupSchedule
├── attendance/  Attendance, AttendanceEvent, scan service   (the hot path)
├── payments/    MonthlyCharge, Payment, billing services
├── reports/     read-only query services + exporters (no models)
└── dashboard/   aggregation views (no models)
```

Dependencies point strictly downward: `attendance` may import read-services from `students`, `lessons`, `payments`; nothing imports `attendance`. `reports` and `dashboard` import everything and are imported by nothing.

### B.2 Entity relationship map

```text
EducationalStage 1───* Grade
Grade            1───* GradeSubject *───1 Subject        (the "offering": this grade studies this subject)
GradeSubject     1───* Group        *───1 Instructor
Group            1───* GroupSchedule                      (recurring weekly slots)
Group            1───* Lesson                             (concrete dated sessions)

Grade            1───* Student
Student          1───* StudentGroupAssignment *───1 Group      (history-preserving; ≤1 ACTIVE per GradeSubject)
Student          1───* CardAssignment         *───1 StudentCard (history-preserving; ≤1 open per side)
StudentCard      0/1──1 Student  (current_student — denormalized fast path)

Lesson           1───* Attendance *───1 Student
Attendance       *───1 Group  AS assigned_group   (nullable — NOT_ASSIGNED case)
Attendance       *───1 Group  AS attended_group   (= lesson.group, denormalized)
Attendance       0/1──1 Lesson AS makeup_for_lesson
Attendance       1───* AttendanceEvent                     (append-only scan log)

Student          1───* MonthlyCharge *───1 GradeSubject    (unique per billing_month)
MonthlyCharge    1───* Payment                             (ledger: PAYMENT | REFUND)
User             1───* AuditLog
```

### B.3 Entity definitions (semantics; columns live in 02)

**EducationalStage** — Primary / Preparatory / Secondary. Configurable, ordered, soft-retired via `is_active`.

**Grade** — "3rd Secondary". Belongs to exactly one stage. Ordered within its stage.

**Subject** — "Physics". A *global catalogue entry*, deliberately not tied to a grade.

**GradeSubject (subject offering)** — the Grade × Subject join meaning "we teach Physics to 3rd Secondary", carrying the **default monthly fee**. This is the key normalization decision of the whole schema: it makes it structurally impossible for a Group to claim a (grade, subject) pair the center does not offer; it gives billing a stable anchor that survives group changes; and it turns "the student's usual group *for this subject*" into a single-column indexed lookup. Wherever the brief says "subject/grade", read `GradeSubject`.

**Instructor** — teaching staff; optional `OneToOne` to `User`.

**Group** — a concrete cohort: "Physics 3Sec — Group A", with capacity, monthly fee (defaulted from the offering) and status. It belongs to a `GradeSubject`, so its grade and subject are derived, never duplicated.

**GroupSchedule** — recurring weekly slot(s) (weekday, start, end). The source for bulk lesson generation; a group may have 1–3 slots per week.

**Student** — the durable person record. It owns nothing about cards, groups, attendance or money directly; all four are separate entities pointing at it. `student_code` is a human-facing sequential code (safe to print and say aloud) and is **not** the QR token.

**StudentGroupAssignment** — "Ahmed normally studies Physics with Group A, from 01/08/2026". Temporal (`start_date`, `end_date`), `status ∈ {ACTIVE, ENDED, TRANSFERRED, CANCELLED}`. It carries a denormalized `grade_subject` (copied from `group`, validated on save) so that the *active* assignment for a subject is one indexed row — this serves both integrity (partial unique index) and hot-path speed. Changing groups **ends** the old row and **creates** a new one; it never rewrites the old row's group.

**StudentCard** — an item of *physical inventory* with a unique `card_number` (printed, human-readable) and a unique `qr_token` (printed as QR, opaque). Lifecycle: `AVAILABLE → ASSIGNED → (LOST | DISABLED | REPLACED)`. The card knows its current holder; the holder does not "own" the card row.

**CardAssignment** — append-only history: "card X was held by student Y from A to B, released because LOST". Answers §18 auditability.

**Lesson** — one dated session of one group, with its own attendance window and late threshold (defaulted from settings/group at creation, then independently editable). `status ∈ {SCHEDULED, OPEN, COMPLETED, CANCELLED}`.

**Attendance** — one row per (lesson, student), recording *both* groups, lifecycle state, outcome status, provenance type, times, and who touched it.

**AttendanceEvent** — append-only log of every scan attempt, including rejections and duplicates. Not a duplicate of Attendance: it is the forensic record (who scanned what, on which device, and what the system answered) and the future anchor for offline-sync idempotency.

**MonthlyCharge** — the obligation: "Ahmed owes 500 for Physics, August 2026". Holds `amount_due`, `discount_amount`, denormalized `total_paid`, generated `balance`, and derived `status`.

**Payment** — an immutable ledger line against a charge. Never edited, never deleted; corrected by posting a `REFUND` that points back at the original.

**AuditLog** — actor, action, entity, before/after JSON, reason, IP. Mandatory for money, attendance edits and card lifecycle.

**Setting** — typed key/value registry with a cached accessor, holding every policy the brief requires to be "configurable rather than hard-coded" (full list in 03).

### B.4 The three separations, expressed structurally

```text
Principle 1   Student ──< CardAssignment >── StudentCard ── qr_token
              Attendance points at Student, never at a card. Re-carding is a no-op for history.

Principle 2   Attendance.assigned_group  ← from StudentGroupAssignment (may be NULL)
              Attendance.attended_group  ← from Lesson.group (never NULL)
              No scan path ever writes StudentGroupAssignment. Ever.

Principle 3/4 Student ──< MonthlyCharge ──< Payment
              No FK exists between Attendance and MonthlyCharge/Payment in either direction.
              The only coupling is a read-only policy check inside the scan service, gated by a setting.
```

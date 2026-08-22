# 02 — Database Architecture

Conventions: every table gets `id BIGSERIAL` PK (`BigAutoField`), `created_at`/`updated_at` (`auto_now_add`/`auto_now`) via `core.TimeStampedModel`, and — where the brief demands traceability — `created_by`/`updated_by` FKs to `accounts.User` (`on_delete=PROTECT`, `null=True`).
Money: `DecimalField(max_digits=10, decimal_places=2)`. Enums: `TextChoices` stored as `varchar(20–30)`.
`on_delete` policy: **`PROTECT` everywhere by default.** Nothing in this system should cascade-delete a student, a lesson or a payment. `CASCADE` is used only for owned child rows that are meaningless alone (`GroupSchedule → Group`, `AttendanceEvent → Attendance` uses `SET_NULL`).

---

## 1. core

### `core_setting`
| Field | Type | Notes |
|---|---|---|
| `key` | `CharField(100)` | **PK** (`primary_key=True`), e.g. `attendance.duplicate_window_seconds` |
| `value` | `JSONField` | typed payload |
| `value_type` | `CharField(10)` | `INT`\|`BOOL`\|`STR`\|`DECIMAL`\|`CHOICE` |
| `label`, `label_ar` | `CharField(200)` | shown in Settings UI |
| `group` | `CharField(50)` | UI grouping: `attendance`, `payments`, `billing`, `cards` |
| `is_editable` | `BooleanField(default=True)` | |
| `updated_by` | FK User `SET_NULL` | |

Accessor `core.settings_registry.get(key)` reads through a Redis/locmem cache (`TTL 300s`, invalidated on save via `post_save`). Defaults are declared in code (`core/policies.py`); DB rows only store overrides — a missing row is not an error.

### `core_auditlog`
| Field | Type | Notes |
|---|---|---|
| `actor` | FK User `SET_NULL`, null | null = system/cron |
| `action` | `CharField(60)`, indexed | `PAYMENT_CREATED`, `ATTENDANCE_MODIFIED`, `CARD_REPLACED`, … |
| `content_type` | FK `ContentType` `PROTECT` | |
| `object_id` | `BigIntegerField` | |
| `object_repr` | `CharField(200)` | frozen label, survives deletion |
| `changes` | `JSONField` | `{"field": {"old": …, "new": …}}` |
| `reason` | `TextField(blank=True)` | **required** for attendance/payment corrections (enforced in service, not DB) |
| `ip_address` | `GenericIPAddressField`, null | |
| `user_agent` | `CharField(300)`, blank | |
| `created_at` | `DateTimeField(auto_now_add)`, indexed | |

Indexes: `(content_type, object_id, -created_at)`, `(action, -created_at)`, `(actor, -created_at)`.
Retention: never deleted in MVP; partition or archive after 3 years.

---

## 2. accounts

### `accounts_user` — `AbstractUser` subclass
**Must exist before the first `migrate`.** The project has no migrations yet, so this is TASK-002 and there is no painful swap later.

| Field | Type | Notes |
|---|---|---|
| …AbstractUser fields | | `username` = login handle; email optional |
| `full_name` | `CharField(150)` | Arabic display name |
| `phone` | `CharField(20)`, blank | |
| `role` | `CharField(30)`, choices `Role` | `SUPER_ADMIN`\|`CENTER_ADMIN`\|`INSTRUCTOR`\|`CASHIER`\|`SCAN_OPERATOR` |
| `is_active` | bool | standard |
| `must_change_password` | `BooleanField(default=False)` | seeded accounts |
| `last_login_ip` | `GenericIPAddressField`, null | |

`role` is a convenience denormalization; **authorization is enforced by Django permissions/groups**, seeded by `python manage.py seed_roles` so it stays configurable (brief §46). A `post_save` signal syncs the user into the Django `Group` matching their role.

---

## 3. academics

### `academics_educationalstage`
`name` `CharField(100) UNIQUE`, `name_ar` `CharField(100)`, `code` `CharField(20) UNIQUE`, `order` `PositiveSmallIntegerField(default=0)`, `is_active` bool.
Meta: `ordering = ["order", "id"]`.

### `academics_grade`
| Field | Type | Notes |
|---|---|---|
| `stage` | FK EducationalStage `PROTECT`, `related_name="grades"` | |
| `name`, `name_ar` | `CharField(100)` | |
| `code` | `CharField(20) UNIQUE` | e.g. `SEC3` |
| `order` | `PositiveSmallIntegerField` | |
| `is_active` | bool | |

Constraints: `UNIQUE(stage, name)`. Index: `(stage, order)`.

### `academics_subject`
`name` `CharField(100)`, `name_ar`, `code` `CharField(20) UNIQUE`, `color` `CharField(7)` (UI chip), `is_active` bool. `UNIQUE(name)`.

### `academics_gradesubject` — the subject offering
| Field | Type | Notes |
|---|---|---|
| `grade` | FK Grade `PROTECT`, `related_name="offerings"` | |
| `subject` | FK Subject `PROTECT`, `related_name="offerings"` | |
| `default_monthly_fee` | `Decimal(10,2)` | template for new groups/charges |
| `is_active` | bool | |

Constraints: `UNIQUE(grade, subject)`; `CHECK(default_monthly_fee >= 0)`.
Index: `(subject, grade)` (reverse lookups in reports).

### `academics_instructor`
| Field | Type |
|---|---|
| `user` | `OneToOneField(User, SET_NULL, null=True, blank=True, related_name="instructor")` |
| `full_name` | `CharField(150)` |
| `phone` | `CharField(20)`, blank |
| `subjects` | `ManyToManyField(Subject, blank=True)` (informational) |
| `is_active` | bool |

### `academics_group`
| Field | Type | Notes |
|---|---|---|
| `grade_subject` | FK GradeSubject `PROTECT`, `related_name="groups"` | grade + subject derived from here |
| `name`, `name_ar` | `CharField(100)` | "Group A" |
| `code` | `CharField(30) UNIQUE` | `SEC3-PHY-A` — used in URLs/exports |
| `instructor` | FK Instructor `PROTECT`, null | |
| `capacity` | `PositiveSmallIntegerField(default=0)` | 0 = unlimited |
| `monthly_fee` | `Decimal(10,2)` | defaults from offering at creation |
| `academic_year` | `CharField(9)`, blank | `2026/2027` label (A8) |
| `status` | `CharField(20)` | `ACTIVE`\|`PAUSED`\|`CLOSED` |
| `default_late_after_minutes` | `PositiveSmallIntegerField`, null | overrides global setting |
| `notes` | `TextField`, blank | |

Constraints: `UNIQUE(grade_subject, name)`; `CHECK(monthly_fee >= 0)`; `CHECK(capacity >= 0)`.
Indexes: `(grade_subject, status)`, `(status,)`.
Properties: `grade`, `subject`, `stage` → through `grade_subject`. Always query with `select_related("grade_subject__subject", "grade_subject__grade__stage", "instructor")`.

### `academics_groupschedule`
| Field | Type | Notes |
|---|---|---|
| `group` | FK Group `CASCADE`, `related_name="schedules"` | owned child |
| `weekday` | `PositiveSmallIntegerField` | 0=Saturday … 6=Friday (Egyptian week) |
| `start_time` | `TimeField` | local wall-clock |
| `end_time` | `TimeField` | |
| `room` | `CharField(30)`, blank | |
| `is_active` | bool | |

Constraints: `UNIQUE(group, weekday, start_time)`; `CHECK(end_time > start_time)`; `CHECK(weekday BETWEEN 0 AND 6)`.
Room double-booking is validated in the service layer (a DB constraint cannot express interval overlap portably), surfaced as a warning, not a hard block.

---

## 4. students

### `students_student`
| Field | Type | Notes |
|---|---|---|
| `student_code` | `CharField(20) UNIQUE`, indexed | `STD-000001`, generated by sequence service |
| `full_name` | `CharField(150)`, indexed | Arabic 4-part name |
| `photo` | `ImageField(upload_to="students/%Y/%m/")`, blank | |
| `date_of_birth` | `DateField`, null | |
| `gender` | `CharField(10)` | `MALE`\|`FEMALE` |
| `phone` | `CharField(20)`, blank, indexed | |
| `guardian_name` | `CharField(150)`, blank | |
| `guardian_phone` | `CharField(20)`, indexed | required in practice; primary contact |
| `guardian_relation` | `CharField(20)`, blank | `FATHER`\|`MOTHER`\|`OTHER` |
| `address` | `TextField`, blank | |
| `grade` | FK Grade `PROTECT`, `related_name="students"` | stage derived (A5) |
| `school` | `CharField(150)`, blank | |
| `status` | `CharField(20)`, indexed | `ACTIVE`\|`INACTIVE`\|`SUSPENDED`\|`TRANSFERRED`\|`GRADUATED` |
| `enrolled_on` | `DateField(default=today)` | |
| `notes` | `TextField`, blank | |
| `created_by` / `updated_by` | FK User `SET_NULL` | |

Indexes: `(status, grade)`, `full_name` (`GinIndex` + `pg_trgm` on Postgres for fast partial name search; plain index on SQLite), `guardian_phone`, `phone`.
Constraints: `CHECK(status IN (...))` (belt-and-braces alongside `choices`).
Properties: `stage`, `active_card`, `active_assignments`.
**No `group_id`, no `card_id`, no `is_paid` on this table** — by design.

### `students_studentgroupassignment`
| Field | Type | Notes |
|---|---|---|
| `student` | FK Student `PROTECT`, `related_name="assignments"` | |
| `group` | FK Group `PROTECT`, `related_name="assignments"` | |
| `grade_subject` | FK GradeSubject `PROTECT` | **denormalized from `group`**, validated in `save()`/`clean()` |
| `is_default` | `BooleanField(default=True)` | primary group for the subject |
| `start_date` | `DateField` | |
| `end_date` | `DateField`, null | null = open-ended |
| `status` | `CharField(20)` | `ACTIVE`\|`ENDED`\|`TRANSFERRED`\|`CANCELLED` |
| `end_reason` | `CharField(30)`, blank | `GROUP_CHANGE`\|`LEFT_CENTER`\|`SCHEDULE`\|`OTHER` |
| `notes` | `TextField`, blank | |
| `created_by` | FK User `SET_NULL` | |

Constraints:
```python
UniqueConstraint(fields=["student", "grade_subject"], condition=Q(status="ACTIVE"),
                 name="uq_active_assignment_per_offering")            # A4 — the integrity anchor
CheckConstraint(condition=Q(end_date__isnull=True) | Q(end_date__gte=F("start_date")),
                name="ck_assignment_date_order")
```
Indexes: `(student, status)`, `(group, status)`, `(grade_subject, status)`.
**Hot-path query** (one row, covered by the partial unique index):
```python
StudentGroupAssignment.objects.filter(
    student_id=sid, grade_subject_id=gsid, status="ACTIVE"
).values_list("group_id", flat=True).first()
```

---

## 5. cards

### `cards_studentcard`
| Field | Type | Notes |
|---|---|---|
| `card_number` | `CharField(30) UNIQUE`, indexed | `CARD-000001`, printed human-readable |
| `qr_token` | `CharField(64) UNIQUE`, `db_index=True` | opaque; see 03 §E.2 |
| `status` | `CharField(20)`, indexed | `AVAILABLE`\|`ASSIGNED`\|`LOST`\|`DISABLED`\|`REPLACED` |
| `current_student` | FK Student `PROTECT`, null, blank, `related_name="cards_held"` | **denormalized fast path**; authoritative history is `CardAssignment` |
| `batch` | `CharField(30)`, blank, indexed | print batch, for inventory ops |
| `issued_at` | `DateTimeField`, null | first assignment |
| `lost_at` / `disabled_at` / `replaced_at` | `DateTimeField`, null | |
| `replaced_by` | FK self `SET_NULL`, null, `related_name="replaces"` | new card that superseded this one |
| `notes` | `TextField`, blank | |

Constraints:
```python
UniqueConstraint(fields=["current_student"], condition=Q(status="ASSIGNED"),
                 name="uq_one_active_card_per_student")
CheckConstraint(condition=(Q(status="ASSIGNED") & Q(current_student__isnull=False))
                        | (~Q(status="ASSIGNED") & Q(current_student__isnull=True)),
                name="ck_card_holder_matches_status")
```
The second constraint is what makes the card lifecycle unfakeable: a card is `ASSIGNED` **iff** it points at a student. Release always nulls `current_student` in the same transaction as the status change.

Index: `(status, batch)` for "available cards in batch B" screens.
`qr_token` uniqueness is a **DB unique index**, not just app logic — it is the primary lookup key of the entire scan path.

### `cards_cardassignment` — history (§18)
| Field | Type | Notes |
|---|---|---|
| `card` | FK StudentCard `PROTECT`, `related_name="assignment_history"` | |
| `student` | FK Student `PROTECT`, `related_name="card_history"` | |
| `assigned_at` | `DateTimeField` | |
| `released_at` | `DateTimeField`, null | null = currently held |
| `release_reason` | `CharField(30)`, blank | `LOST`\|`DAMAGED`\|`REPLACED`\|`GRADUATED`\|`DISABLED`\|`OTHER` |
| `assigned_by` / `released_by` | FK User `SET_NULL` | |
| `notes` | `TextField`, blank | |

Constraints:
```python
UniqueConstraint(fields=["card"],    condition=Q(released_at__isnull=True), name="uq_open_assignment_per_card")
UniqueConstraint(fields=["student"], condition=Q(released_at__isnull=True), name="uq_open_assignment_per_student")
CheckConstraint(condition=Q(released_at__isnull=True) | Q(released_at__gte=F("assigned_at")), name="ck_card_assign_dates")
```
Index: `(student, -assigned_at)`.

---

## 6. lessons

### `lessons_lesson`
| Field | Type | Notes |
|---|---|---|
| `group` | FK Group `PROTECT`, `related_name="lessons"` | |
| `instructor` | FK Instructor `PROTECT`, null | snapshot; defaults from group |
| `sequence_no` | `PositiveSmallIntegerField`, null | "Lesson 03" within the billing month |
| `lesson_date` | `DateField`, indexed | **local** date, set in `save()` from `scheduled_start` |
| `scheduled_start` | `DateTimeField`, indexed | tz-aware |
| `scheduled_end` | `DateTimeField` | |
| `check_in_opens_at` | `DateTimeField` | default `start − attendance.window_open_before_minutes` |
| `check_in_closes_at` | `DateTimeField` | default `start + attendance.window_close_after_minutes` |
| `late_after` | `DateTimeField` | default `start + late_after_minutes` (group override wins) |
| `status` | `CharField(20)`, indexed | `SCHEDULED`\|`OPEN`\|`COMPLETED`\|`CANCELLED` |
| `actual_start_at` / `actual_end_at` | `DateTimeField`, null | set by start/complete actions |
| `expected_students` | `PositiveSmallIntegerField(default=0)` | snapshot of active assignments at open time |
| `cancel_reason` | `TextField`, blank | |
| `notes` | `TextField`, blank | |
| `created_by` | FK User `SET_NULL` | |

Constraints:
```python
UniqueConstraint(fields=["group", "scheduled_start"], name="uq_lesson_group_start")
CheckConstraint(condition=Q(scheduled_end__gt=F("scheduled_start")), name="ck_lesson_time_order")
CheckConstraint(condition=Q(check_in_closes_at__gt=F("check_in_opens_at")), name="ck_lesson_window_order")
```
Indexes: `(group, lesson_date)`, `(status, scheduled_start)`, `(lesson_date, status)`.
Generation: `generate_lessons(group, from_date, to_date)` expands `GroupSchedule` rows, skipping existing `(group, scheduled_start)` pairs and configured holidays — idempotent by construction.

---

## 7. attendance

### `attendance_attendance` — the core table
| Field | Type | Notes |
|---|---|---|
| `lesson` | FK Lesson `PROTECT`, `related_name="attendances"` | |
| `student` | FK Student `PROTECT`, `related_name="attendances"` | |
| `assigned_group` | FK Group `PROTECT`, **null**, `related_name="attendance_as_assigned"` | the usual group at scan time; null ⇒ not assigned |
| `attended_group` | FK Group `PROTECT`, `related_name="attendance_as_attended"` | `= lesson.group`, denormalized for reporting indexes |
| `state` | `CharField(20)`, indexed | `ABSENT`\|`CHECKED_IN`\|`CHECKED_OUT`\|`CANCELLED` |
| `status` | `CharField(20)`, indexed | `PRESENT`\|`LATE`\|`ABSENT`\|`PARTIAL`\|`CANCELLED` |
| `attendance_type` | `CharField(20)`, indexed | `NORMAL`\|`ALTERNATIVE_GROUP`\|`MAKEUP`\|`MANUAL`\|`EXCEPTIONAL` |
| `check_in_at` | `DateTimeField`, null | |
| `check_out_at` | `DateTimeField`, null | |
| `late_minutes` | `PositiveSmallIntegerField(default=0)` | frozen at check-in |
| `duration_minutes` | `PositiveSmallIntegerField`, null | frozen at check-out |
| `makeup_for_lesson` | FK Lesson `SET_NULL`, null, `related_name="makeups"` | set only when type = `MAKEUP` |
| `card_used` | FK StudentCard `SET_NULL`, null | which physical card produced the check-in |
| `is_manual` | `BooleanField(default=False)` | created or edited by hand |
| `requires_approval` / `approved_by` / `approved_at` | bool / FK User / dt | exceptional-case flow |
| `checked_in_by` / `checked_out_by` | FK User `SET_NULL` | operator on duty |
| `scan_source` | `CharField(20)` | `HID`\|`CAMERA`\|`MANUAL`\|`SYSTEM` |
| `notes` | `TextField`, blank | |

Constraints:
```python
UniqueConstraint(fields=["lesson", "student"], name="uq_attendance_lesson_student")   # idempotency anchor
CheckConstraint(condition=Q(check_out_at__isnull=True) | Q(check_out_at__gte=F("check_in_at")),
                name="ck_attendance_time_order")
CheckConstraint(condition=(Q(state__in=["CHECKED_IN", "CHECKED_OUT"]) & Q(check_in_at__isnull=False))
                        | (Q(state__in=["ABSENT", "CANCELLED"])),
                name="ck_attendance_state_requires_checkin")
CheckConstraint(condition=~Q(attendance_type="MAKEUP") | Q(makeup_for_lesson__isnull=False),
                name="ck_makeup_requires_source_lesson")
```
Indexes:
```python
Index(fields=["lesson", "state"])                                   # lesson dashboard
Index(fields=["student", "-check_in_at"])                           # student history
Index(fields=["attended_group", "lesson"])                          # group reports
Index(fields=["-check_in_at"], name="ix_att_recent")                # live feed
Index(fields=["lesson", "attendance_type"],
      condition=~Q(attendance_type="NORMAL"), name="ix_att_alternative")   # §35 report, partial index
```

### `attendance_attendanceevent` — append-only
| Field | Type | Notes |
|---|---|---|
| `attendance` | FK Attendance `SET_NULL`, null | null for rejected scans |
| `lesson` | FK Lesson `PROTECT` | |
| `student` | FK Student `SET_NULL`, null | null when card unknown |
| `card` | FK StudentCard `SET_NULL`, null | **never store the raw token here** |
| `event_type` | `CharField(30)`, indexed | `CHECK_IN`\|`CHECK_OUT`\|`DUPLICATE`\|`DENIED`\|`MANUAL_EDIT`\|`CANCEL`\|`APPROVAL` |
| `result_code` | `CharField(40)`, indexed | the machine code returned to the scanner (03 §D.6) |
| `message` | `CharField(200)` | operator-facing text as sent |
| `device_id` | `CharField(60)`, blank, indexed | scanner station identity |
| `operator` | FK User `SET_NULL`, null | |
| `idempotency_key` | `CharField(64)`, `UNIQUE`, null | client-generated; enables retry + future offline sync |
| `latency_ms` | `PositiveIntegerField`, null | server-side timing, feeds the perf dashboard |
| `payload` | `JSONField(default=dict)` | non-sensitive context |
| `created_at` | dt, indexed | |

Index: `(lesson, -created_at)`, `(result_code, -created_at)`.

---

## 8. payments

### `payments_monthlycharge`
| Field | Type | Notes |
|---|---|---|
| `student` | FK Student `PROTECT`, `related_name="charges"` | |
| `grade_subject` | FK GradeSubject `PROTECT` | **the billing anchor** (A1) |
| `group` | FK Group `SET_NULL`, null | snapshot only, non-authoritative |
| `billing_month` | `DateField`, indexed | always the 1st of the month |
| `amount_due` | `Decimal(10,2)` | fee snapshot (A3) |
| `discount_amount` | `Decimal(10,2)`, default 0 | negotiated reduction |
| `total_paid` | `Decimal(10,2)`, default 0 | denormalized ledger sum (PAYMENT − REFUND) |
| `balance` | `GeneratedField` (stored) | `amount_due − discount_amount − total_paid` |
| `status` | `CharField(20)`, indexed | `UNPAID`\|`PARTIALLY_PAID`\|`PAID`\|`OVERPAID`\|`WAIVED`\|`CANCELLED` |
| `due_date` | `DateField` | `billing_month + billing.due_day_of_month − 1` |
| `waived_reason` | `TextField`, blank | |
| `generated_by` | `CharField(20)` | `AUTO`\|`MANUAL` |
| `created_by` / `updated_by` | FK User `SET_NULL` | |

Constraints:
```python
UniqueConstraint(fields=["student", "grade_subject", "billing_month"], name="uq_charge_per_month")  # §42
CheckConstraint(condition=Q(amount_due__gte=0) & Q(discount_amount__gte=0) & Q(total_paid__gte=0),
                name="ck_charge_amounts_non_negative")
CheckConstraint(condition=Q(discount_amount__lte=F("amount_due")), name="ck_discount_not_exceeding_due")
```
`balance` as a stored `GeneratedField` (Django 5.0+, Postgres 12+/SQLite 3.31+) makes "who owes money" an index range scan:
```python
Index(fields=["billing_month", "status"])
Index(fields=["student", "-billing_month"])
Index(fields=["billing_month"], condition=Q(balance__gt=0), name="ix_charge_outstanding")   # partial
Index(fields=["group", "billing_month"])
```
`status` is **always** written by `recalculate_charge()` — never by a form. See 04 §F.4.

### `payments_payment` — immutable ledger
| Field | Type | Notes |
|---|---|---|
| `monthly_charge` | FK MonthlyCharge `PROTECT`, `related_name="payments"` | |
| `student` | FK Student `PROTECT`, indexed | denormalized: enables student-level ledger queries without a join |
| `kind` | `CharField(10)` | `PAYMENT`\|`REFUND` |
| `amount` | `Decimal(10,2)` | **always positive**; sign comes from `kind` |
| `paid_at` | `DateTimeField`, indexed | back-dating allowed for admins, audited |
| `method` | `CharField(20)` | `CASH`\|`BANK_TRANSFER`\|`CARD`\|`WALLET`\|`OTHER` |
| `receipt_number` | `CharField(30) UNIQUE` | sequential, printed |
| `reference` | `CharField(60)`, blank | bank/wallet txn id |
| `collected_by` | FK User `PROTECT` | the cashier — never nullable, this is money |
| `reverses` | FK self `SET_NULL`, null, `related_name="reversed_by"` | a REFUND may point at the PAYMENT it cancels |
| `notes` | `TextField`, blank | |

Constraints:
```python
CheckConstraint(condition=Q(amount__gt=0), name="ck_payment_amount_positive")            # §42
CheckConstraint(condition=~Q(kind="REFUND") | Q(reverses__isnull=False) | Q(notes__gt=""),
                name="ck_refund_needs_context")
```
Indexes: `(student, -paid_at)`, `(monthly_charge, paid_at)`, `(paid_at, method)` (daily collection report), `(collected_by, paid_at)` (cashier shift report).
**Application rule enforced in the service layer and the admin: `Payment` rows are never `UPDATE`d or `DELETE`d.** A mistake is corrected with a `REFUND` carrying `reverses_id`.

---

## 9. Cross-cutting integrity summary (brief §42)

| Requirement | Mechanism |
|---|---|
| QR token unique | `UNIQUE` index on `cards_studentcard.qr_token` |
| One active attendance per (student, lesson) | `UNIQUE(lesson, student)` — plus `get_or_create` inside `atomic()` on the scan path |
| Payment amounts never negative | `CHECK(amount > 0)` + `kind` for direction |
| No conflicting active assignments | partial `UNIQUE(student, grade_subject) WHERE status='ACTIVE'` |
| No duplicate monthly charges | `UNIQUE(student, grade_subject, billing_month)` |
| One active card per student | partial `UNIQUE(current_student) WHERE status='ASSIGNED'` + `UNIQUE(student) WHERE released_at IS NULL` on history |
| Card status ↔ holder consistent | `CHECK` linking `status='ASSIGNED'` to `current_student IS NOT NULL` |
| Money never orphaned | `PROTECT` on every FK out of `Payment` / `MonthlyCharge` |
| Concurrency | `transaction.atomic()` + `select_for_update()` on the charge row and the attendance row; unique constraints as the final arbiter |

## 10. Migration & data-seeding notes

1. Custom `User` **before** the first migrate (TASK-002). `AUTH_USER_MODEL = "accounts.User"`.
2. `GeneratedField` requires Postgres ≥ 12 / SQLite ≥ 3.31; Python 3.13 ships SQLite ≥ 3.45, so dev is fine.
3. `pg_trgm` extension migration (`TrigramExtension()`) guarded by `connection.vendor == "postgresql"` so SQLite dev migrations still run.
4. Seed data ships as idempotent management commands, not data migrations: `seed_roles`, `seed_settings`, `seed_academics --demo`, `import_cards <file.csv>`.
5. Every constraint above is created by a migration — none of them is "documentation only".

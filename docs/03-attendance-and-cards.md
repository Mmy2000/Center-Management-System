# 03 — Attendance State Machine & QR/Card Architecture

## D. Attendance state machine

### D.1 Three orthogonal axes (never collapse them)

| Axis | Field | Question it answers | Values |
|---|---|---|---|
| Lifecycle | `state` | Where is the student *now*? | `NOT_ATTENDED` (implicit: no row) → `CHECKED_IN` → `CHECKED_OUT`; plus `ABSENT`, `CANCELLED` |
| Outcome | `status` | How does this count in reports? | `PRESENT`, `LATE`, `ABSENT`, `PARTIAL`, `CANCELLED` |
| Provenance | `attendance_type` | Why/how did it happen? | `NORMAL`, `ALTERNATIVE_GROUP`, `MAKEUP`, `MANUAL`, `EXCEPTIONAL` |

`state` drives the scanner; `status` drives reports and (optionally) fees; `attendance_type` drives the alternative-group report and management insight. A student can be `CHECKED_OUT` + `LATE` + `ALTERNATIVE_GROUP` simultaneously — three facts, three columns.

### D.2 Lifecycle transitions

```text
                       (no row)
                     NOT_ATTENDED
                          │
        ┌─────────────────┼──────────────────────┐
   scan │ check-in   lesson completed │ absentee sweep
        ▼                              ▼
    CHECKED_IN ──────────────────►  ABSENT
        │   scan #2 (beyond dup window) / auto-close        │
        ▼                                                    │ manual
    CHECKED_OUT                                              │ correction
        │                                                    ▼
        └────────── manual cancel ──────────────►  CANCELLED (terminal)
```

Allowed:
| From | To | Trigger | Guard |
|---|---|---|---|
| `NOT_ATTENDED` | `CHECKED_IN` | scan / manual add | lesson `OPEN` (or admin override), window open, eligibility passed |
| `CHECKED_IN` | `CHECKED_OUT` | scan / manual / auto-close | `now − check_in_at ≥ attendance.min_checkout_gap_minutes` |
| `NOT_ATTENDED` | `ABSENT` | lesson completion sweep | student had an ACTIVE assignment to this group at lesson time |
| `ABSENT` | `CHECKED_IN` | manual correction | permission `attendance.change_attendance` + reason |
| any | `CANCELLED` | manual cancel | permission + reason (audited) |

Rejected (must fail gracefully, never 500):
| Attempted | Response |
|---|---|
| `NOT_ATTENDED → CHECKED_OUT` | `ERR_NOT_CHECKED_IN` — "لم يتم تسجيل الدخول أولاً". Offer a one-click "check in now" for operators with permission. |
| `CHECKED_OUT → CHECKED_IN` | `WARN_ALREADY_CHECKED_OUT` — shows the completed session; re-entry requires an admin "reopen" action (audited, sets `is_manual=True`). |
| `CHECKED_IN → CHECKED_IN` | `WARN_DUPLICATE` within the duplicate window; interpreted as check-out beyond `min_checkout_gap_minutes`. |
| `CANCELLED → *` | `ERR_ATTENDANCE_CANCELLED`; admin must un-cancel first. |

### D.3 Outcome (`status`) derivation

```text
at check-in:      check_in_at ≤ lesson.late_after   → PRESENT
                  check_in_at >  lesson.late_after  → LATE
                  late_minutes = max(0, ceil((check_in_at − lesson.scheduled_start).minutes))

at check-out:     duration_minutes = (check_out_at − check_in_at).minutes
                  if duration_minutes < attendance.partial_min_duration_minutes → PARTIAL
                  else keep PRESENT / LATE

at lesson completion:
                  rows still CHECKED_IN and attendance.auto_checkout_at_lesson_end = true
                      → check_out_at = lesson.actual_end_at or scheduled_end
                      → state = CHECKED_OUT, notes += "auto-checkout"
                  assigned students with no row → create state=ABSENT, status=ABSENT,
                      attendance_type=NORMAL, assigned_group=attended_group=lesson.group
```
`LATE` survives check-out: a late student who stays the full lesson is still `LATE` (that is what the center wants to see). `PARTIAL` overrides both because leaving early is the more actionable fact — this precedence is a single documented rule in `attendance/services.py::finalize_status()`.

### D.4 Eligibility resolution (brief §9) — the decision table

Inputs: `student`, `lesson` (⇒ `lesson.group.grade_subject`).
Resolve `assigned_group = ACTIVE assignment of student for lesson.group.grade_subject` (single indexed row).

| # | Condition | `attendance_type` | Default policy | Setting |
|---|---|---|---|---|
| A | `assigned_group == lesson.group` | `NORMAL` | allow | — |
| B | `assigned_group` exists, different group, same offering | `ALTERNATIVE_GROUP` (or `MAKEUP` if a `makeup_for_lesson` is supplied / auto-suggested) | allow + warn on screen | `attendance.alternative_group_policy` = `ALLOW` |
| C | no ACTIVE assignment, but student's `grade == lesson.grade` | `EXCEPTIONAL` | `REQUIRE_APPROVAL` | `attendance.not_assigned_policy` ∈ `BLOCK`\|`WARN`\|`REQUIRE_APPROVAL` |
| D | student's grade ≠ lesson grade (or different subject entirely) | `EXCEPTIONAL` | `REQUIRE_APPROVAL` | `attendance.different_grade_policy` ∈ `BLOCK`\|`REQUIRE_APPROVAL` |

Case B auto-suggests `MAKEUP` when the student has an `ABSENT` attendance row for the same `grade_subject` within the same billing month; the scanner shows a one-tap "سجّلها تعويضية" button which patches `attendance_type` and `makeup_for_lesson` on the just-created row.

`assigned_group` is written to the attendance row **as resolved at scan time** and never back-filled later — that is what makes the §35 alternative-group report historically truthful even after the student permanently moves to Group B.

**The scan path never writes `StudentGroupAssignment`.** (Principle 2.) If the front desk wants the change to be permanent, they use Students → Assignments → Change Group, which ends one row and opens another.

### D.5 Window, duplicate and payment gates (in evaluation order)

```text
1. token       → card found?                      else ERR_CARD_NOT_FOUND
2. card        → status == ASSIGNED?              else ERR_CARD_LOST / DISABLED / REPLACED / UNASSIGNED
3. student     → status == ACTIVE?                else ERR_STUDENT_INACTIVE  (SUSPENDED ⇒ ERR_STUDENT_SUSPENDED)
4. lesson      → status == OPEN?                  else ERR_LESSON_NOT_OPEN / ERR_LESSON_CANCELLED
5. window      → open ≤ now ≤ close?              else outside_window_policy: BLOCK | REQUIRE_APPROVAL | ALLOW_EXCEPTIONAL
6. eligibility → decision table D.4               else per-policy block / approval
7. payment     → only if payments.enforce_on_attendance
                 balance > 0 and today > billing_month + grace_days
                 → allow_unpaid / allow_partial policies decide WARN | BLOCK | REQUIRE_APPROVAL
8. duplicate   → existing row + now − last_scan < duplicate_window_seconds → WARN_DUPLICATE (no write)
9. capacity    → attended count ≥ group.capacity  → WARN_CAPACITY (never blocks; capacity is advisory)
10. write      → get_or_create / update inside one transaction
```
Gates 1–4 are cheap and ordered by likelihood of failure. Gate 7 is skipped entirely (zero queries) when enforcement is off — which is the default (A10).

### D.6 Result codes (the scanner contract)

```text
OK_CHECK_IN                 OK_CHECK_IN_ALTERNATIVE     OK_CHECK_IN_MAKEUP
OK_CHECK_IN_LATE            OK_CHECK_OUT                OK_CHECK_OUT_PARTIAL
WARN_DUPLICATE              WARN_NOT_ASSIGNED           WARN_PAYMENT_DUE
WARN_CAPACITY               WARN_ALREADY_CHECKED_OUT    WARN_OUTSIDE_WINDOW
NEEDS_APPROVAL_NOT_ASSIGNED NEEDS_APPROVAL_GRADE        NEEDS_APPROVAL_WINDOW
ERR_CARD_NOT_FOUND          ERR_CARD_UNASSIGNED         ERR_CARD_LOST
ERR_CARD_DISABLED           ERR_CARD_REPLACED           ERR_STUDENT_INACTIVE
ERR_STUDENT_SUSPENDED       ERR_LESSON_NOT_FOUND        ERR_LESSON_NOT_OPEN
ERR_LESSON_CANCELLED        ERR_WINDOW_CLOSED           ERR_NOT_CHECKED_IN
ERR_PAYMENT_BLOCKED         ERR_NOT_ASSIGNED_BLOCKED    ERR_GRADE_BLOCKED
ERR_ATTENDANCE_CANCELLED    ERR_RATE_LIMITED            ERR_SERVER
```
Every code maps to: an HTTP status (200 for OK/WARN/NEEDS_APPROVAL, 4xx for ERR), a colour (green/amber/red), a sound (beep/double-beep/buzz), and a bilingual message. The mapping lives in one module (`attendance/result_codes.py`) so the UI, the tests and the docs never diverge.

### D.7 Configurable policies (complete list, brief §12/§21/§22/§32)

| Key | Default | Meaning |
|---|---|---|
| `attendance.window_open_before_minutes` | 30 | window opens N min before start |
| `attendance.window_close_after_minutes` | 150 | window closes N min after start |
| `attendance.outside_window_policy` | `REQUIRE_APPROVAL` | `BLOCK` \| `REQUIRE_APPROVAL` \| `ALLOW_EXCEPTIONAL` |
| `attendance.late_after_minutes` | 15 | late threshold (group override wins) |
| `attendance.duplicate_window_seconds` | 20 | second scan inside this = duplicate, no write |
| `attendance.min_checkout_gap_minutes` | 10 | before this, a repeat scan is a duplicate, not a check-out |
| `attendance.partial_min_duration_minutes` | 30 | shorter attendance ⇒ `PARTIAL` |
| `attendance.auto_checkout_at_lesson_end` | `true` | close dangling check-ins on completion |
| `attendance.not_assigned_policy` | `REQUIRE_APPROVAL` | scenario C |
| `attendance.different_grade_policy` | `REQUIRE_APPROVAL` | scenario D |
| `attendance.alternative_group_policy` | `ALLOW` | scenario B |
| `attendance.materialize_absences` | `true` | absentee sweep on completion |
| `payments.enforce_on_attendance` | `false` | master switch for gate 7 |
| `payments.allow_unpaid_attendance` | `true` | |
| `payments.allow_partial_attendance` | `true` | |
| `payments.grace_days` | 10 | no enforcement before day N of the month |
| `payments.allow_overpayment` | `false` | reject payments exceeding balance |
| `billing.due_day_of_month` | 5 | |
| `billing.auto_generate_day` | 1 | monthly charge generation day |
| `cards.token_prefix` | `CMS1` | accepted prefix for generated tokens |
| `cards.accept_unprefixed_tokens` | `true` | tolerate vendor-printed batches |

### D.8 Manual correction (brief §24)

Any manual write goes through `attendance.services.manual_adjust(attendance, *, actor, reason, **fields)` which:
1. requires `attendance.change_attendance` and a **non-empty reason**;
2. captures a `before` snapshot of the changed fields only;
3. applies changes inside `atomic()`, sets `is_manual=True`, re-derives `status`;
4. writes `AuditLog(action="ATTENDANCE_MODIFIED", changes={field: {old, new}}, reason, ip)`;
5. writes an `AttendanceEvent(event_type="MANUAL_EDIT")`.

The student profile and lesson dashboard show a "✎ modified" badge linking to the audit trail. Nothing in the system can change attendance without steps 1–5 — the admin site registers Attendance as read-only-plus-action, not as a plain `ModelAdmin`.

### D.9 Edge cases (brief §53) → defined behaviour

| Case | Behaviour |
|---|---|
| Student scans twice quickly | `WARN_DUPLICATE`, no DB write, shows original check-in time |
| Student scans again after ≥ `min_checkout_gap` | `OK_CHECK_OUT` |
| Check-out without check-in | `ERR_NOT_CHECKED_IN`; operator may check in with permission |
| Inactive / lost / disabled / replaced card | dedicated `ERR_CARD_*` code, red screen, no student data shown beyond nothing |
| Attends another group | `ALTERNATIVE_GROUP`, assignment untouched |
| Make-up | `MAKEUP` + `makeup_for_lesson` link |
| Not assigned to this offering | policy C |
| Different subject/grade | policy D |
| Lesson cancelled | `ERR_LESSON_CANCELLED` |
| Lesson not open | `ERR_LESSON_NOT_OPEN` + "افتح الحصة" button for authorized roles |
| Late arrival | `OK_CHECK_IN_LATE`, `late_minutes` frozen |
| Forgot to check out | auto-checkout at completion, `notes` records it, `is_manual=False`, `scan_source=SYSTEM` |
| Instructor corrects attendance | D.8 |
| Two scanners, same QR, same instant | `UNIQUE(lesson, student)` + `get_or_create` in `atomic()`: exactly one row; loser returns `WARN_DUPLICATE` |
| Same card scanned at two *different* lessons at once | both succeed (legitimately different lessons); the alternative-group report surfaces it |
| Network fails mid-scan | client retries with the same `idempotency_key`; server returns the original result from `AttendanceEvent` |
| Server unavailable | scanner shows offline banner, keeps a local queue (post-MVP sync path already modelled by `idempotency_key`) |

---

## E. QR / card architecture

### E.1 Inventory-first model

Cards are **stock**, not documents generated per student:

```text
Print batch (vendor)             System
─────────────────────            ────────────────────────────────
CARD-000001 … 000500   ──import──►  500 rows, status=AVAILABLE
                                     │
                            enrollment scan
                                     ▼
                          status=ASSIGNED, current_student=Ahmed
                          + CardAssignment(open)
                                     │
                    lost / damaged / student leaves
                                     ▼
                    status=LOST|DISABLED|REPLACED, current_student=NULL
                          + CardAssignment.released_at set
```

Two supported origins (A9):
- **Vendor-printed**: `python manage.py import_cards batch.csv` with columns `card_number,qr_token[,batch]`. Validates uniqueness, rejects the whole file on any duplicate (all-or-nothing transaction).
- **System-generated**: `python manage.py generate_cards --count 500 --batch B7` creates tokens, then `Cards → Export batch` produces a CSV/PDF sheet (card number + QR image) for the print shop.

### E.2 QR token design (brief §15)

- Format: `CMS1:` + 22-char URL-safe base62 from `secrets.token_urlsafe(16)` → 128 bits of entropy. Total ≤ 32 chars ⇒ a small, fast-decoding QR (version 2–3, ECC level M) that scans reliably from a plastic card at 15 cm.
- Contains **no** student id, phone, national id or sequence. Two adjacent cards' tokens are unrelated.
- Stored in one `UNIQUE`-indexed column; lookup is a single index probe.
- Revocable: revocation is a card status change; the token itself is never reused, never recycled to another student.
- Normalization on input: strip whitespace/CR (HID scanners send a trailing `\r`), reject anything longer than 64 chars **before** touching the DB, accept an unprefixed token when `cards.accept_unprefixed_tokens` is on (vendor batches).
- Raw tokens are never logged, never written to `AttendanceEvent`, never rendered in a URL query string, and are masked (`CMS1:ab…9f`) anywhere they must be shown to an admin.

Why not sign the token (HMAC/JWT)? Because verification would still require a DB lookup to know the card's status and holder — the signature buys nothing and lengthens the QR. Random-and-indexed is the correct trade.

### E.3 Enrollment scan flow (brief §16)

```text
Create/open student  →  "Assign card"  →  operator scans the physical card
      │
      ▼
POST /api/cards/lookup/  {qr_token}
      │
      ├─ not found                  → ✕ بطاقة غير معروفة        (ERR_CARD_NOT_FOUND)
      ├─ status=ASSIGNED            → ✕ البطاقة مخصصة لطالب آخر  (shows holder name if permitted)
      ├─ status=LOST                → ✕ البطاقة مبلّغ عن فقدها
      ├─ status=DISABLED|REPLACED   → ✕ البطاقة معطّلة
      └─ status=AVAILABLE           → ✓ preview card_number → confirm
                                       │
                             POST /api/cards/assign/  {card_id, student_id}
                                       │  transaction.atomic():
                                       │    select_for_update card
                                       │    re-verify status=AVAILABLE  (TOCTOU guard)
                                       │    card.status=ASSIGNED, current_student=student
                                       │    CardAssignment.objects.create(...)
                                       │    AuditLog CARD_ASSIGNED
                                       ▼
                                    ✓ تم الربط
```
The re-verification inside the lock is what prevents two receptionists assigning the same card to two students; the partial unique indexes are the second line of defence.

### E.4 Replacement, loss, disabling (brief §17–18)

`cards.services.replace_card(old_card, new_card, *, actor, reason)` in one transaction:
1. lock both cards;
2. close the open `CardAssignment` for `old_card` (`released_at=now`, `release_reason`);
3. `old_card.status = LOST | DISABLED`, `current_student = None`, `replaced_by = new_card`, timestamps set;
4. assert `new_card.status == AVAILABLE`, assign it to the same student, open a new `CardAssignment`;
5. two `AuditLog` rows (`CARD_MARKED_LOST`, `CARD_ASSIGNED`) with the reason.

Consequences, by construction:
- The old token stops authenticating on the very next scan (gate 2) — no cache to invalidate, because card status is read fresh on every scan.
- **Historical attendance is untouched.** Attendance references `student`, and `card_used` keeps pointing at the old card as a historical fact.
- The student profile renders the full card timeline from `CardAssignment` (§18).

### E.5 Attendance scan flow (brief §19)

```text
POST /api/attendance/scan/ {lesson_id, qr_token, idempotency_key, device_id, scan_source}
  │
  1  normalize token, rate-limit check (device + user)
  2  StudentCard.select_related("current_student__grade").get(qr_token=…)      [Q1]
  3  validate card status → student status
  4  Lesson (from request-scoped cache or select_related)                       [Q2]
  5  validate lesson status + window
  6  assigned_group = active assignment(student, lesson.group.grade_subject)    [Q3]
  7  classify: NORMAL | ALTERNATIVE_GROUP | MAKEUP | EXCEPTIONAL  (+ policies)
  8  optional payment gate (skipped when disabled)                              [Q4]
  9  atomic:
        attendance, created = get_or_create(lesson, student, defaults=…)        [Q5]
        if not created: apply duplicate / check-out rules with select_for_update
        AttendanceEvent.objects.create(...)                                     [Q6]
  10 return JSON (result_code, student, groups, attendance, payment?) + timing
```
5–6 queries, one transaction, no N+1, no template rendering. Detailed optimisation notes in 06 §J.

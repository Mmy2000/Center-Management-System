# 05 — URL / AJAX API Design & UI/UX

## G. API design (Django MVT + AJAX, no DRF)

### G.1 Shape of the thing

Two kinds of endpoints, deliberately separated:

| Kind | Prefix | Returns | Used by |
|---|---|---|---|
| **Pages** | `/students/`, `/groups/`, … | full HTML (`TemplateView`/function view) | browser navigation |
| **JSON API** | `/api/…` | `JsonResponse` | scanner, live dashboards, modals, autocomplete |
| **HTML partials** | `/api/…/partial/` or `?partial=1` | `render_to_string` fragment | table refresh, pagination, filters (the MVT+AJAX idiom — no client-side templating) |

Auth: **Django session** + `@login_required`. CSRF: every mutating AJAX call sends `X-CSRFToken` from the cookie (a tiny `static/js/http.js` wrapper does this once for the whole app). No token/JWT layer exists in the MVP — there is no third-party client to authenticate.

### G.2 Envelope and error contract

Success (200):
```json
{"ok": true, "code": "OK_CHECK_IN", "data": {...}, "message": "تم تسجيل الحضور"}
```
Failure (400/403/404/409/429):
```json
{"ok": false, "code": "ERR_CARD_LOST", "message": "هذه البطاقة مبلّغ عن فقدها",
 "field_errors": {"amount": ["المبلغ يجب أن يكون أكبر من صفر"]}}
```
`code` is machine-readable and stable; `message` is localized and may change. HTTP status: `400` validation, `403` permission, `404` unknown object, `409` state conflict (lesson closed, card already assigned), `429` rate limited, `500` unexpected (with a Sentry id in the payload, never a traceback).

A single decorator `@ajax(login_required=True, perm="…", methods=["POST"])` handles: method check, permission check, JSON body parse, `ValidationError`/`DomainError` → envelope mapping, and timing (`latency_ms`). Views therefore contain no try/except boilerplate.

### G.3 Endpoint catalogue

**Auth**
```text
GET  /accounts/login/            page
POST /accounts/login/            form post, no-JavaScript fallback
POST /accounts/login/ajax/       JSON sign-in: {redirect, user} — the page uses this
POST /accounts/logout/
GET  /accounts/profile/          page (change own password)
```

Both sign-in paths share one throttle bucket (5/min/IP) and one form, so a
failure reads the same either way: wrong password, unknown user and disabled
account all answer `ERR_INVALID_CREDENTIALS` with an identical message. `next`
is accepted only when it stays on this host.

**Students**
```text
GET   /students/                              page: list + filters
GET   /api/students/                          JSON/partial: paginated, q, grade, stage, status, group, has_card
POST  /api/students/                          create                       (students.add_student)
GET   /students/<id>/                         page: profile (tabs)
GET   /api/students/<id>/                     JSON summary
PATCH /api/students/<id>/                     update                       (students.change_student)
POST  /api/students/<id>/status/              {status, reason}             (students.change_student)
GET   /api/students/search/?q=                typeahead: code, name, guardian phone (max 10)
GET   /api/students/<id>/attendance/          history, paginated + filters (§34)
GET   /api/students/<id>/financial-summary/   per-month, per-subject (§33)
GET   /api/students/<id>/cards/               card timeline (§18)
```

**Academic structure** — uniform CRUD, all `academics.*` perms:
```text
GET|POST        /api/stages/            /api/stages/<id>/     PATCH
GET|POST        /api/grades/            /api/grades/<id>/     PATCH
GET|POST        /api/subjects/          /api/subjects/<id>/   PATCH
GET|POST        /api/offerings/         /api/offerings/<id>/  PATCH      # GradeSubject
GET|POST        /api/instructors/       /api/instructors/<id>/ PATCH
GET  /api/grades/?stage=<id>            dependent-select feeder
GET  /api/offerings/?grade=<id>         dependent-select feeder
GET  /api/groups/?offering=<id>         dependent-select feeder
```

**Groups & assignments**
```text
GET   /groups/                                page
GET   /api/groups/                            filters: stage, grade, subject, instructor, status, q
POST  /api/groups/
GET   /groups/<id>/                           page: group dashboard (§36)
PATCH /api/groups/<id>/
GET   /api/groups/<id>/students/              roster (active assignments) + payment status column
POST  /api/groups/<id>/students/              {student_id, start_date, is_default}  → assign
DELETE/api/groups/<id>/students/<student_id>/ {end_date, reason}  → ends assignment (never deletes)
POST  /api/assignments/<id>/transfer/         {new_group_id, start_date, reason} → atomic end+create
GET   /api/groups/<id>/schedule/  POST /api/groups/<id>/schedule/  DELETE /api/schedule/<id>/
```
`DELETE .../students/<id>/` is *soft* by design: it sets `end_date` and `status=ENDED`. The verb is REST-shaped; the semantics are historical (Principle 5).

**Lessons**
```text
GET   /lessons/                          page: today / upcoming / active / completed tabs
GET   /api/lessons/                      filters: date range, group, instructor, status
POST  /api/lessons/                      single lesson
POST  /api/lessons/generate/             {group_id, from, to} → bulk from GroupSchedule (idempotent)
GET   /lessons/<id>/                     page: lesson dashboard (§37)
PATCH /api/lessons/<id>/                 times, window, instructor, notes
POST  /api/lessons/<id>/open/            SCHEDULED → OPEN, snapshots expected_students
POST  /api/lessons/<id>/complete/        OPEN → COMPLETED, auto-checkout + absentee sweep
POST  /api/lessons/<id>/cancel/          {reason} → CANCELLED
GET   /api/lessons/active/               lessons currently open (scanner lesson picker)
```

**Attendance**
```text
POST  /api/attendance/scan/              ★ the hot path
POST  /api/attendance/<id>/checkout/     manual check-out
PATCH /api/attendance/<id>/              manual correction {reason required}
POST  /api/attendance/<id>/cancel/       {reason}
POST  /api/attendance/<id>/approve/      supervisor override for NEEDS_APPROVAL_*
POST  /api/attendance/manual/            {lesson_id, student_id, type, reason} — no card
GET   /api/lessons/<id>/attendance/      roster + live counters
GET   /api/lessons/<id>/feed/?since=<iso> incremental events for the live board (polling)
```

**Cards**
```text
GET   /cards/                            page: inventory
GET   /api/cards/                        filters: status, batch, q(card_number)
POST  /api/cards/lookup/                 {qr_token} → card state (enrollment scan)
POST  /api/cards/assign/                 {card_id|qr_token, student_id}
POST  /api/cards/<id>/mark-lost/         {reason}
POST  /api/cards/<id>/disable/           {reason}
POST  /api/cards/<id>/replace/           {new_card_id|new_qr_token, reason}
POST  /api/cards/generate/               {count, batch} → mint blank stock (cards.add_studentcard)
POST  /api/cards/import/                 CSV upload, all-or-nothing (cards.add_studentcard)
GET   /api/cards/export/?batch&format=   pdf = QR sheet for the printer · csv = token list (managers only)
GET   /api/cards/<id>/history/
```

**Payments**
```text
GET   /payments/                              page: charges workspace
GET   /api/charges/                           filters: month, status, grade, subject, group, q, balance>0
POST  /api/charges/generate/                  {billing_month, group_id?, dry_run}
PATCH /api/charges/<id>/                      {discount_amount, due_date, reason}
POST  /api/charges/<id>/waive/                {reason}
POST  /api/charges/<id>/cancel/               {reason}
GET   /api/charges/<id>/                      charge + its ledger
POST  /api/payments/                          {charge_id, amount, method, paid_at?, reference?, notes?}
POST  /api/payments/<id>/refund/              {amount, reason}
GET   /api/payments/                          filters: date range, method, cashier, student
GET   /api/payments/<id>/receipt/[?size=thermal]  downloadable PDF receipt (A5 or 80 mm)
GET   /api/students/<id>/payments/
```

**Reports & dashboard**
```text
GET /reports/<slug>/                     page (filters + table)
GET /api/reports/attendance/             filters: from,to,stage,grade,subject,group,instructor,student,status
GET /api/reports/alternative-groups/     §35
GET /api/reports/student/<id>/
GET /api/reports/group/<id>/
GET /api/reports/payments/               collection, by method/cashier/day
GET /api/reports/outstanding/            unpaid + partially paid
GET /api/reports/<slug>/export/?format=pdf|xlsx|csv   # pdf = downloaded document
GET /api/dashboard/summary/              today's operational + monthly financial tiles (§49)
```

### G.4 The scan endpoint in detail (brief §44–45)

```http
POST /api/attendance/scan/
Content-Type: application/json
X-CSRFToken: …
```
```json
{
  "lesson_id": 25,
  "qr_token": "CMS1:7f92c8a14e314d919a82",
  "idempotency_key": "5f0c…-a31",
  "device_id": "desk-1",
  "scan_source": "HID",
  "intent": "AUTO",
  "makeup_for_lesson_id": null
}
```
`intent`: `AUTO` (default — server decides check-in vs check-out), `CHECK_IN`, `CHECK_OUT`. `idempotency_key` is a client UUID; replaying it returns the **first** result verbatim (from `AttendanceEvent`), which makes retries after a timeout safe today and offline sync possible later without a schema change.

Success:
```json
{
  "ok": true,
  "code": "OK_CHECK_IN_ALTERNATIVE",
  "message": "تم تسجيل الحضور — مجموعة بديلة",
  "data": {
    "action": "CHECK_IN",
    "student": {"id": 125, "code": "STD-000125", "name": "أحمد محمد", "photo": "/media/…"},
    "attendance": {"id": 9912, "state": "CHECKED_IN", "status": "PRESENT",
                   "type": "ALTERNATIVE_GROUP", "check_in": "08:05:12", "late_minutes": 0},
    "groups": {"assigned": "Group A", "attended": "Group B"},
    "payment": {"status": "PARTIALLY_PAID", "remaining": "200.00", "month": "2026-08"},
    "actions": [{"code": "MARK_MAKEUP", "label": "سجّلها تعويضية"},
                {"code": "COLLECT_PAYMENT", "label": "استلام دفعة", "charge_id": 771}],
    "latency_ms": 41
  }
}
```
Rejection (HTTP 409):
```json
{"ok": false, "code": "ERR_CARD_LOST", "message": "هذه البطاقة مبلّغ عن فقدها",
 "data": {"card_number": "CARD-000152", "action_hint": "REPLACE_CARD"}}
```

Exposure rules: name, code, photo, the two group names, attendance facts, and — only when the operator holds `payments.view_monthlycharge` — the payment block. Never: phone numbers, guardian data, address, DOB, other students' names, or the raw token.

Validation: `lesson_id` positive int and existing; `qr_token` string ≤ 64 chars matching `^[A-Za-z0-9:_\-]+$` (rejected before any DB hit); `idempotency_key` ≤ 64 chars; unknown fields ignored.
Authorization: `attendance.add_attendance`; instructors additionally must own the lesson's group (`lesson.group.instructor.user == request.user`) unless they hold `attendance.scan_any_group`.
Rate limit: 10 scans/second/device and 600/minute/user — generous for humans, fatal for a script.

### G.5 Authorization matrix

| Capability | Super Admin | Center Admin | Instructor | Cashier | Scan Operator |
|---|:--:|:--:|:--:|:--:|:--:|
| Academic structure CRUD | ✔ | ✔ | – | – | – |
| Students: view / create / edit | ✔ | ✔ | view (own groups) | view | view (scan result only) |
| Cards: inventory & import | ✔ | ✔ | – | – | – |
| Cards: assign / replace / lose | ✔ | ✔ | – | – | – |
| Groups & assignments | ✔ | ✔ | view own | – | – |
| Lessons: create/generate | ✔ | ✔ | own groups | – | – |
| Lesson open / complete | ✔ | ✔ | own groups | – | ✔ (open only) |
| Scan (check-in/out) | ✔ | ✔ | own groups | – | ✔ |
| Manual attendance correction | ✔ | ✔ | own groups + reason | – | – |
| Approve exceptional attendance | ✔ | ✔ | own groups | – | – |
| Charges: generate / waive / discount | ✔ | ✔ | – | ✔ (generate only) | – |
| Payments: collect / refund | ✔ | ✔ | – | ✔ / refund needs admin | – |
| Financial reports | ✔ | ✔ | – | ✔ | – |
| Attendance reports | ✔ | ✔ | own groups | – | – |
| Settings, users, roles | ✔ | ✔ (no role edit) | – | – | – |
| Audit log | ✔ | ✔ (read) | – | – | – |

Implemented as Django permissions grouped into `auth.Group`s by `seed_roles`; object-level "own groups" is a queryset filter in one place (`accounts.scoping.visible_groups(user)`), applied by every list view and every write service.

---

## H. UI / UX

### H.1 Global shell

Arabic-first RTL (`dir="rtl"`, `lang="ar"`) with a full English mode. The language toggle sits in the topbar and posts to `set_language`, so the choice lives in a cookie and **URLs never gain a `/ar/` prefix** — every hard-coded `/api/...` path in the JS keeps working. Direction, the Bootstrap stylesheet (RTL vs LTR) and the JS string catalog all follow the active language. Self-hosted Cairo webfont. Left (i.e. right-hand) sidebar mirrors brief §39:

```text
لوحة التحكم · الطلاب · الهيكل الدراسي · المجموعات · الحصص · الحضور · المدفوعات · التقارير · البطاقات · الإعدادات
```
Top bar: global search (`/` focuses it — student code, name, guardian phone, card number), active-lessons badge, user menu. Numbers render as Arabic-Indic or Latin per a user preference; money always `1,234.00 ج.م`.

### H.2 The scanner console — the screen that matters

Route `/scan/` → pick an open lesson (or auto-select if the operator's device has exactly one). Then `/scan/<lesson_id>/`:

```text
┌─────────────────────────────────────────────────────────────┐
│ الفيزياء — مجموعة أ    ٢٢/٠٨/٢٠٢٦  ٠٨:٠٠   ● الحصة مفتوحة  │
│ متوقع ٣٥ · حاضر ٢٧ · متأخر ٣ · بديل ٢ · بالداخل ٢٥          │
├───────────────────────────┬─────────────────────────────────┤
│                           │  آخر العمليات                    │
│      [ ماسح جاهز ]        │  ٠٨:٠٥ أحمد محمد    ✓ حضور       │
│   امسح كارت الطالب        │  ٠٨:٠٤ سارة علي     ⚠ بديلة      │
│                           │  ٠٨:٠٣ عمر حسن      ✕ بطاقة      │
│   ┌───────────────────┐   │  …                              │
│   │   صورة الطالب     │   │                                 │
│   │   أحمد محمد       │   │                                 │
│   │   ✓ تم تسجيل الحضور│  │                                 │
│   │   مجموعته: أ       │   │                                 │
│   │   الحالية: ب       │   │                                 │
│   │   نوع: مجموعة بديلة│   │                                 │
│   │   متبقي: ٢٠٠ ج.م   │   │                                 │
│   └───────────────────┘   │                                 │
└───────────────────────────┴─────────────────────────────────┘
```
Rules that make it fast:
- A hidden always-focused input captures HID scanner keystrokes; `Enter` (or a 120 ms keystroke-idle timer) submits. Clicking anywhere refocuses. No mouse needed, ever.
- The result card is **colour + sound + size**: green/✓/beep, amber/⚠/double-beep, red/✕/buzz — readable across a noisy room from two metres. The card holds for 3 s, then returns to "ready" while remaining scannable throughout (a scan during display just replaces it).
- Optimistic UI is forbidden here: nothing is shown until the server answers. The truth of "did he check in" must never be a guess.
- Camera mode (`html5-qrcode`) is a toggle for tablets/phones; same endpoint, `scan_source=CAMERA`.
- Offline banner: on network failure the client queues scans in `localStorage`, shows a persistent amber bar with the queue depth, and drains it with the original `idempotency_key`s when connectivity returns. (Client-side only; the server contract already supports it.)
- Supervisor override for `NEEDS_APPROVAL_*` opens an inline modal requiring a reason and re-auth of the approver's password if the session role lacks the permission.

### H.3 Key screens

| Screen | Contents |
|---|---|
| **Dashboard** (§49) | Today: lessons / active / expected / checked-in / absent / alternative. Month: expected, collected, outstanding, paid-partial-unpaid counts. Live list of open lessons with per-lesson counters. Quick actions: scan, collect payment, add student. |
| **Student list** | Server-paginated table, AJAX filters (stage, grade, status, group, subject, has-card, balance>0), debounced search, column of payment chips for the current month. |
| **Student profile** | Header (photo, code, grade, status, active card, quick actions). Tabs: **Overview** · **Groups** (active + history timeline) · **Attendance** (§34: lesson, date, usual group, attended group, in/out, duration, type, status) · **Payments** (§33: per-month per-subject with due/paid/remaining chips + ledger) · **Cards** (§18 timeline) · **Audit**. |
| **Enrollment wizard** | 3 steps: student data → assign groups (multi-subject, shows fees and a running monthly total) → scan card. Step 3 is skippable; the profile shows "no card" until done. |
| **Group dashboard** (§36) | Roster with attendance-rate and payment chips; next/last lesson; attendance sparkline; payment summary; capacity meter; buttons: generate lessons, open lesson, collect for group. |
| **Lesson dashboard** (§37) | Live counters, roster split into present / late / alternative / not-yet-arrived, real-time feed (5 s polling of `/feed/?since=`), inline manual check-in/out, complete-lesson button with a preview of "N will be marked absent". |
| **Payments workspace** | Month selector; two tabs: **Charges** (filterable, inline "collect" button) and **Transactions** (day book with cashier totals). Collect modal: charge context, amount (defaults to the remaining balance), method, reference, notes → prints receipt. |
| **Cards inventory** | Status tiles (available/assigned/lost/disabled) + table + actions. Stock is created from this screen: **بطاقات جديدة** mints a batch and hands back the printable QR sheet, **استيراد CSV** loads a vendor's pre-printed batch, **كشف الطباعة** re-exports either. A "scan to find" box jumps to whichever card/student a token belongs to. |
| **Reports** | One shared template: filter bar → table → export buttons (PDF / Excel / CSV — never a browser print dialog). Alternative-group report (§35) is a first-class menu item, defaulting to the current month. |
| **Settings** | Grouped policy editor rendering `core_setting` rows with typed widgets and inline help; users & roles; audit log viewer. |

### H.4 Interaction conventions

- Every list is server-rendered first, then refreshed by AJAX into the same `<tbody>` via the partial endpoints — no duplicated markup between Python and JS.
- Every destructive or financial action opens a modal that requires a typed **reason** where the audit demands it; the button stays disabled until the reason is non-empty.
- Dependent selects (stage → grade → offering → group) load through the feeder endpoints and reset their children on change.
- Toasts for success, inline field errors from `field_errors`, and a single global handler that renders `code`/`message` for anything unexpected.
- Keyboard: `/` search, `s` scanner, `n` new student, `Esc` closes modals.
- Everything degrades to a normal form post if JS fails — the pages are real Django views, not an SPA in disguise.

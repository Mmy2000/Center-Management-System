# 06 — Security, Performance, Testing

## I. Security analysis

### I.1 Assets and threat model

| Asset | Threat | Control |
|---|---|---|
| Student & guardian PII (phones, address, photo) | curious staff, mass export, scraped by a compromised low-privilege account | RBAC + object scoping; scan response excludes PII; export permission separate from view permission; audit on export |
| QR tokens | photographed card, guessed token, harvested from logs/URLs | 128-bit random tokens (guessing infeasible); tokens never in URLs, logs, or `AttendanceEvent`; masked in UI; a scan requires an authenticated operator + an OPEN lesson, so a stolen token yields only a false attendance mark in a room the thief must physically be in |
| Money records | insider fraud (unrecorded cash, deleted payments, silent edits) | payments immutable; `collected_by` non-null; refunds require reason + link; daily cashier close-out report; `AuditLog` on every mutation; delete permission removed from admin |
| Attendance records | falsified presence (paid-per-attendance disputes) | scanner writes are attributable (`checked_in_by`, `device_id`); manual edits flagged `is_manual` + reason + audit; `AttendanceEvent` is append-only forensic evidence |
| Accounts | weak/shared passwords, session hijack | Django password validators (min 10 chars for staff roles), `must_change_password` on seeded accounts, login throttling, `SESSION_COOKIE_SECURE`, `HttpOnly`, `SameSite=Lax`, 8-hour session with `SESSION_EXPIRE_AT_BROWSER_CLOSE=False` and idle timeout middleware for cashier stations |

Realistic risk ranking for this business: **insider misuse (1)** ≫ **PII leakage (2)** ≫ external attack (3). The design spends its security budget accordingly — auditing and least privilege first, cryptography last.

### I.2 Application controls

- **Transport**: HTTPS only. `SECURE_SSL_REDIRECT`, `SECURE_HSTS_SECONDS=31536000` (+ subdomains, preload), `SECURE_PROXY_SSL_HEADER` behind nginx.
- **Headers**: `X_FRAME_OPTIONS=DENY`, `SECURE_CONTENT_TYPE_NOSNIFF`, `Referrer-Policy=same-origin`, CSP via `django-csp` (`default-src 'self'`; no inline scripts — the scanner JS lives in a static file; camera scanning needs `worker-src blob:`).
- **CSRF**: enabled for every mutating endpoint including AJAX; `CSRF_COOKIE_SECURE`, `CSRF_TRUSTED_ORIGINS` set explicitly. No endpoint is `@csrf_exempt` — the scanner is same-origin.
- **AuthZ**: permission checked in the view decorator *and* enforced by queryset scoping in the service. An instructor requesting another instructor's lesson gets 404, not 403 (no existence disclosure).
- **Input validation**: Django Forms for every write path (including JSON endpoints — parse the dict into a Form). Token regex + length cap before any DB access. Decimal fields bounded. File uploads: images only, ≤ 2 MB, content-type sniffed by Pillow, stored under `MEDIA_ROOT` served by nginx with `X-Content-Type-Options: nosniff`; CSV imports parsed with a size cap and a strict header allowlist.
- **Rate limiting** (`django-ratelimit`, Redis backend): login 5/min/IP, scan 10/s/device + 600/min/user, card lookup 30/min/user, report export 10/hour/user, global 1000/min/IP at nginx.
- **SQL/XSS**: ORM only (no raw SQL outside two audited report aggregates that use parameterized `RawSQL`); templates autoescape; `|safe` is banned by a CI grep.
- **Secrets**: `django-environ`, `.env` outside VCS, `SECRET_KEY` rotated at deploy from the environment, `DEBUG=False` enforced by a startup assertion in production settings. **The current `SECRET_KEY` in `cms/settings.py` is a dev key and must be rotated before deployment.**
- **Media privacy**: student photos served through nginx from a non-guessable path (`students/<uuid4>.jpg`), not sequential ids.
- **Audit**: middleware attaches `request.user`/IP to a thread-local consumed by the audit service, so services never need the request object.

### I.3 Concurrency & data-integrity controls (security-adjacent)

- Every write service is wrapped in `transaction.atomic()`; the scan path also uses `select_for_update()` on the attendance row for the check-out branch and the payment path on the charge row.
- `ATOMIC_REQUESTS = False` (deliberate): long report requests must not hold transactions.
- The final arbiters are the DB constraints in 02 §9. Application checks exist for good error messages; constraints exist for correctness. Every unique constraint has a matching `IntegrityError` handler that converts it into a friendly `code`.

### I.4 Known accepted risks

1. A photographed card can be replayed by an operator-accompanied accomplice → mitigated operationally (the student must be physically present); optionally add a per-lesson "photo verify" toggle showing the student's photo prominently (already in the UI).
2. Cash fraud cannot be prevented by software, only detected → daily close-out + audit reports are the control.
3. No 2FA in the MVP → recommended for `SUPER_ADMIN` in Phase 11 via `django-otp` (one-line addition, no schema conflict).

---

## J. Performance — the scan path

**Budget: 500 ms end-to-end (brief §52). Design target: ≤ 6 queries, < 25 ms server time, < 120 ms p95 on LAN.** At ~600 scans/day with bursts of maybe 3/second before a lesson, this system is not throughput-bound; it is **latency- and correctness-bound**. Optimise the single request, do not build a distributed system.

### J.1 Query plan (each line is one indexed round trip)

| # | Query | Index used |
|---|---|---|
| Q1 | `StudentCard.objects.select_related("current_student__grade").get(qr_token=…)` | `uq` on `qr_token` |
| Q2 | `Lesson.objects.select_related("group__grade_subject__subject","group__grade")` — cached per lesson for 60 s, invalidated on lesson state change | PK |
| Q3 | active assignment `(student, grade_subject, status=ACTIVE)` → `values_list("group_id")` | partial `uq_active_assignment_per_offering` |
| Q4 | payment gate (**skipped by default**) | `(student, -billing_month)` |
| Q5 | `Attendance.objects.get_or_create(lesson, student, defaults=…)` | `uq_attendance_lesson_student` |
| Q6 | `AttendanceEvent.objects.create(...)` | — |

Q1 returns the student *and* their grade in one join, so no second student query exists. Q3 returns a bare id, not a model — nothing is instantiated that isn't rendered.

### J.2 Optimisations that are in scope

- **Indexes**: exactly those in 02; verified with `EXPLAIN ANALYZE` in a test that fails if the scan path stops using an index scan.
- **Lesson cache**: the open lesson's immutable-ish fields (`group`, window, `late_after`, `status`) cached in Redis under `lesson:{id}:v{updated_at}`; a state change bumps the key. Saves Q2 on most scans.
- **Settings cache**: the policy registry is one cached dict; gate evaluation costs zero queries.
- **`update_fields` everywhere**: check-out writes 4 columns, not 25.
- **Connection pooling**: `CONN_MAX_AGE=60` (+ PgBouncer in transaction mode if the box ever needs it).
- **Response size**: the scan payload is < 1 KB; no HTML rendering on the hot path.
- **Timing**: every scan stores `latency_ms` on its event; `/reports/scan-performance/` shows p50/p95/max per device — the SLA is measured, not assumed.
- **Static/media**: WhiteNoise with hashed filenames + far-future caching; the scanner page loads once per shift.

### J.3 Optimisations explicitly deferred (Principle 7)

Read replicas, sharding, a websocket layer, Celery on the scan path, materialised views, ElasticSearch. The live board polls every 5 s with a `since` cursor — for ≤ 40 concurrent viewers this is cheaper and far simpler than websockets, and swapping it for SSE later is a 40-line change.

### J.4 Report performance

Reports run on a dataset of tens of thousands of rows: aggregate in the database (`Sum`/`Count` with `filter=`), paginate every list, never build a Python loop over a queryset for totals. Exports > 5,000 rows go through a Celery task with a download link (Phase 10) — below that they stream synchronously.

---

## K. Testing strategy

Stack: `pytest`, `pytest-django`, `pytest-cov`, `factory_boy`, `freezegun`, `pytest-xdist`. Tests run against **Postgres** in CI (constraints and locking must be real) and are allowed on SQLite locally for speed, with locking tests skipped by marker.

### K.1 Layers and targets

| Layer | Scope | Target |
|---|---|---|
| Unit — domain services | eligibility classification, status derivation, charge status, receipt sequence, token normalization | 100% of `services.py` branches |
| Model/DB | every constraint from 02 §9 raises `IntegrityError` when violated; generated `balance` correctness; `PROTECT` behaviour | one test per named constraint |
| Integration | scan flow end-to-end through services; enrollment; replacement; charge generation; lesson completion sweep | all flows in 03/04 |
| View/API | status codes, permission matrix (05 §G.5) as a parametrized table, envelope shape, `field_errors` | every endpoint × every role |
| Concurrency | two threads scanning the same card; two cashiers paying the same charge; two operators assigning the same card | `TransactionTestCase`, Postgres only |
| Security | anonymous access to every URL → redirect; role escalation attempts; CSRF-less POST; rate limit trips; token not in any response body/log | dedicated `tests/security/` |
| E2E | Playwright: enrollment wizard, scan (HID keystroke simulation), collect payment, generate report | happy paths + 3 failure paths |
| Performance | scan endpoint under `pytest-benchmark`; `assertNumQueries(6)`; EXPLAIN assertion | fails CI on regression |

### K.2 The test cases that must exist (non-negotiable)

**Scan / attendance**
1. normal check-in → `NORMAL`, `PRESENT`, both group columns equal
2. check-in after `late_after` → `LATE`, `late_minutes` correct (freezegun)
3. alternative group → `ALTERNATIVE_GROUP`, `assigned_group != attended_group`, **assignment row unchanged** (the Principle-2 regression test)
4. make-up with `makeup_for_lesson` → type `MAKEUP`, constraint satisfied
5. not assigned, each of the three policies → block / warn / approval
6. different grade → approval required
7. duplicate inside window → no new row, `WARN_DUPLICATE`, original time returned
8. second scan after `min_checkout_gap` → check-out, `duration_minutes` correct
9. check-out before check-in → `ERR_NOT_CHECKED_IN`
10. `CHECKED_OUT` then scan → `WARN_ALREADY_CHECKED_OUT`
11. lost / disabled / replaced / unassigned card → the four distinct error codes
12. **replaced card: old token fails, new token succeeds, historical attendance rows unchanged**
13. lesson `SCHEDULED`/`COMPLETED`/`CANCELLED` → correct refusals
14. outside window, each policy
15. two threads, same card, same lesson → exactly one attendance row (`unique` holds), one OK + one duplicate
16. same `idempotency_key` replayed → identical response, one row
17. lesson completion → dangling check-ins auto-closed; absentees materialised for active assignees only; alternative-group attendees not double-counted as absent in their own lesson
18. `PARTIAL` when duration < threshold

**Cards**
19. assign available card; assign already-assigned card → error; assign to student who already holds one → error
20. concurrent assignment of the same card → one wins
21. replace: statuses, history rows, `replaced_by` link, audit entries
22. `CHECK` constraint: `ASSIGNED` without holder rejected at DB level

**Payments**
23. multiple partial payments walk `UNPAID → PARTIALLY_PAID → PAID`
24. overpayment rejected when policy off, `OVERPAID` when on
25. refund reduces `total_paid`, recomputes status, original row untouched
26. `amount <= 0` rejected by DB constraint
27. duplicate charge generation is idempotent (unique constraint + `get_or_create`)
28. charge generation is a fee **snapshot**: raising `group.monthly_fee` afterwards does not change issued charges
29. concurrent payments on one charge → `total_paid` exact (threads, Postgres)
30. `waive` / `cancel` excluded from expected-revenue aggregates
31. payment policy gate: unpaid student blocked / warned / allowed per settings, and **fully independent of attendance status**

**Assignments**
32. second ACTIVE assignment for the same offering → `IntegrityError`
33. transfer ends the old row and creates a new one; history preserved; attendance history unaffected
34. `grade_subject` denormalization always matches `group.grade_subject` (model `clean()` + constraint test)

**Security/permissions**
35. the full role × endpoint matrix
36. instructor cannot scan or read another instructor's lesson (404)
37. raw QR token never appears in any response, log line, or `AttendanceEvent` row

### K.3 Fixtures and data

`factory_boy` factories for every model with sensible Arabic-ish names; a `demo_center` fixture building 1 stage → 2 grades → 3 subjects → 6 groups → 60 students → 4 weeks of lessons → assorted attendance and payments. The same builder backs `manage.py seed_demo` so manual testing and automated testing share one dataset definition.

### K.4 CI gates

`ruff` + `black --check` + `django-check --deploy` + `makemigrations --check --dry-run` (no missing migrations) + `extract_messages -l en --check` (no untranslated strings) + `pytest -n auto --cov` with **coverage floors: 90% on `services.py` modules, 75% overall**, + `assertNumQueries` performance tests. A red gate blocks merge.

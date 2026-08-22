# Egyptian Tutoring Center Management System — Technical Blueprint

Status: **design complete, implementation not started**.
Target stack: **Django 6.1 (MVT) + AJAX**, Python 3.13, PostgreSQL 16 (SQLite for local dev).

## Document index

| # | Document | Covers (from the brief) |
|---|----------|--------------------------|
| 01 | [Requirements & Domain Model](01-requirements-and-domain.md) | A. Requirements analysis, assumptions, ambiguities · B. Domain model |
| 02 | [Database Architecture](02-database-schema.md) | C. Tables, fields, types, FKs, indexes, unique & check constraints |
| 03 | [Attendance & Card/QR Architecture](03-attendance-and-cards.md) | D. Attendance state machine · E. QR/card architecture |
| 04 | [Payment Architecture](04-payments.md) | F. Monthly billing, partial payments, refunds, policies |
| 05 | [URL / AJAX API & UI/UX](05-api-and-ui.md) | G. Endpoints, payloads, validation, errors, authz · H. Screens & flows |
| 06 | [Security, Performance, Testing](06-security-performance-testing.md) | I. Security analysis · J. Scan-path optimisation · K. Test strategy |
| 07 | [Deployment Architecture](07-deployment.md) | L. Docker, nginx, Postgres, Redis, SSL, backups, monitoring, CI/CD |
| 08 | [Implementation Roadmap](08-roadmap.md) | M. 12 phases with objectives, tasks, deps, deliverables, acceptance |
| 09 | [Task Backlog](09-tasks.md) | Sequential, independently testable tasks (TASK-001 …) |

## The seven non-negotiable principles

1. **Student ≠ Card.** A student may be re-carded any number of times; history survives.
2. **Assigned group ≠ Attended group.** Attendance stores both; scanning never mutates an assignment.
3. **Attendance ⟂ Payment.** Neither derives the other; coupling exists only through an explicit, configurable policy.
4. **Payments are transactions**, never a boolean. Balance and status are computed from a ledger.
5. **History is immutable.** Group changes, re-carding and re-pricing never rewrite past attendance or past money.
6. **The scan path is the hot path.** ≤ 6 queries, one transaction, < 500 ms budget (target p95 < 120 ms on LAN).
7. **No over-engineering.** Ship the MVP on Django + AJAX; add Celery/Redis/offline only where a real requirement appears.

## Stack decisions (and why)

| Layer | Choice | Rationale |
|-------|--------|-----------|
| Backend | Django 6.1, MVT, function/class views + service layer | Mandated by the user. Server-rendered pages keep the operational UI simple; a thin service layer keeps the complex rules (scan, billing) out of views and testable. |
| API style | Plain Django views returning `JsonResponse` (JSON) or `render_to_string` (HTML partials), session auth + CSRF | DRF is unnecessary overhead for a single first-party UI. `/api/*` endpoints are JSON for the scanner; list/table refreshes return HTML fragments — the natural MVT+AJAX idiom. |
| DB | PostgreSQL 16 prod, SQLite dev | Needs `SELECT … FOR UPDATE` (check-out/payment row locks), partial unique indexes, real concurrency. SQLite ignores row locks — correctness in dev still holds because every invariant is also a DB constraint. |
| Frontend | Django templates + Bootstrap 5 (RTL **and** LTR) + vanilla JS (`fetch`) | Arabic-first, English-complete. No build step, no node toolchain — a center's PC can run it. |
| Languages | **ar (default) + en**, switchable per user from the topbar | The choice rides in a cookie, so URLs stay identical in both languages and every hard-coded `/api/...` path keeps working. Direction, stylesheet and JS strings all follow. |
| Scanner input | **USB/Bluetooth HID 2D barcode scanner (primary)** + `html5-qrcode` camera (fallback) | A ~600 EGP HID scanner types the token and presses Enter: zero latency, no camera permissions, works on any cheap PC. The camera path exists for phones/tablets. Both post to the same endpoint. |
| Cache/queue | Redis (cache + sessions + rate limit) from Phase 2; **Celery only in Phase 9+** for monthly charge generation and report exports | Nothing in the MVP scan path needs a worker. Beat generates charges monthly; exports run async only if they get slow. |
| Documents (PDF) | **ReportLab + arabic-reshaper + python-bidi**, bundled Amiri face | One engine (`apps/core/pdf.py`) renders every receipt and report. Pure Python — no GTK/Pango (WeasyPrint) or headless Chromium, so it behaves identically on a Windows desk and in the Linux container. Arabic is shaped and bidi-ordered once, before it reaches the canvas. |
| Other exports | `openpyxl` (Excel), `csv` (stdlib, UTF-8 BOM) | Excel opens Arabic without an import wizard. |
| Printing | **Nothing calls `window.print()`** | Documents are downloaded as PDFs so what staff file is byte-identical to what they saw, independent of browser print settings. |
| Tests | pytest + pytest-django + factory_boy + `django.test.TransactionTestCase` for concurrency | Concurrency tests need real threads + Postgres. |

## Conventions

- All money: `DecimalField(max_digits=10, decimal_places=2)`, EGP, never float.
- All timestamps: tz-aware `DateTimeField`, `TIME_ZONE = "Africa/Cairo"`, `USE_TZ = True` (Egypt observes DST — never combine a naive date+time in business logic).
- Enums: `models.TextChoices`, stored as short uppercase strings.
- Business rules live in `apps/<app>/services.py`; views validate input, call a service, render. Models hold invariants + derived properties only.
- Every state-changing service call runs inside `transaction.atomic()` and writes an `AuditLog` row.

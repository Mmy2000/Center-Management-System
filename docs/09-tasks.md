# 09 — Implementation Task Backlog

Sequential, independently testable tasks. Each is small enough to implement and verify in isolation, and ordered so that no task breaks the architecture defined in 01–08.

Format per task: **Objective · Prerequisites · Requirements · Implementation · DB · Backend · Frontend · API · Files · Acceptance · Tests · Edge cases.** A dash (—) means "not applicable to this task".

---

## Phase 1 — Project Foundation

### TASK-001 — Settings package & environment configuration
**Objective.** Replace the single `cms/settings.py` with `base/dev/prod/test` modules driven by environment variables.
**Prerequisites.** —
**Requirements.** No secret in VCS; prod asserts `DEBUG=False`; `TIME_ZONE=Africa/Cairo`; `LANGUAGE_CODE=ar`; `USE_TZ=True`; `USE_I18N=True`.
**Implementation.** `django-environ`; `base.py` holds shared config; `dev.py` (SQLite, debug toolbar), `prod.py` (Postgres, Redis, security headers, Sentry hook), `test.py` (fast hashers, locmem cache). Add `.env.example`, `.gitignore`, `requirements/{base,dev,prod}.txt`.
**DB.** — **Backend.** settings only. **Frontend.** — **API.** —
**Files.** `cms/settings/{__init__,base,dev,prod,test}.py`, `.env.example`, `.gitignore`, `requirements/*.txt`, updated `manage.py`/`wsgi.py`/`asgi.py` defaults.
**Acceptance.** `manage.py check` passes with `DJANGO_SETTINGS_MODULE=cms.settings.dev` and `…prod` (env supplied); no `django-insecure-` key remains.
**Tests.** `test_settings_prod_requires_secret_key`, `test_timezone_is_cairo`.
**Edge cases.** Missing env var must fail loudly at startup, not silently default.

### TASK-002 — Custom user model (before any other migration)
**Objective.** Create `accounts.User` and set `AUTH_USER_MODEL`.
**Prerequisites.** TASK-001.
**Requirements.** `AbstractUser` + `full_name`, `phone`, `role`, `must_change_password`, `last_login_ip`. **No other app may have migrations yet.**
**Implementation.** `apps/accounts/models.py` per 02 §2; `Role(TextChoices)`; register in admin.
**DB.** `accounts_user` (initial migration). **Backend.** model + admin. **Frontend.** — **API.** —
**Files.** `apps/accounts/{__init__,apps,models,admin}.py`, `apps/accounts/migrations/0001_initial.py`.
**Acceptance.** `migrate` from an empty DB succeeds; `createsuperuser` works; `get_user_model()` is `accounts.User`.
**Tests.** `test_user_model_is_swapped`, `test_role_choices`.
**Edge cases.** If any migration exists already, drop the dev DB — never try to swap the user model later.

### TASK-003 — `apps/` package layout & app scaffolding
**Objective.** Create the ten apps with correct labels under `apps/`.
**Prerequisites.** TASK-002.
**Requirements.** App labels: `core, accounts, academics, students, cards, lessons, attendance, payments, reports, dashboard`; each with `apps.py` setting `name = "apps.x"`, `label = "x"`.
**Implementation.** `startapp` into `apps/`, fix `AppConfig`, register in `INSTALLED_APPS`.
**DB.** — **Backend.** app configs. **Frontend.** — **API.** —
**Files.** `apps/*/…`, updated `INSTALLED_APPS`.
**Acceptance.** `manage.py check` clean; `manage.py showmigrations` lists all apps.
**Tests.** `test_installed_apps_labels`.
**Edge cases.** Label collisions with third-party apps.

### TASK-004 — `core`: base model, Setting registry, policy defaults
**Objective.** Shared model base and the cached configuration registry.
**Prerequisites.** TASK-003.
**Requirements.** All policies of 03 §D.7 declared as code defaults; DB rows are overrides only; cache invalidated on save.
**Implementation.** `TimeStampedModel`; `Setting` model (PK = key); `core/policies.py` with `DEFAULTS`; `settings_registry.get/set/all_for_group()` using `cache` with a 300 s TTL and a `post_save` invalidation signal.
**DB.** `core_setting`. **Backend.** registry + signals. **Frontend.** — **API.** —
**Files.** `apps/core/{models,policies,registry,signals,apps}.py`, migration.
**Acceptance.** `get("payments.enforce_on_attendance")` returns `False` with an empty table; after `set(..., True)` it returns `True` immediately.
**Tests.** default fallback, override, type coercion, cache invalidation.
**Edge cases.** Unknown key → `KeyError` (typo protection, never a silent `None`).

### TASK-005 — `core`: AuditLog + audit service + middleware
**Objective.** One call site for every audited mutation.
**Prerequisites.** TASK-004.
**Requirements.** Captures actor, IP, user agent, entity, before/after diff, reason.
**Implementation.** `AuditLog` model per 02 §1; `audit.record(action, instance, changes=None, reason="")` reading a thread-local request context set by `AuditContextMiddleware`; `diff(old_instance, new_instance, fields)` helper.
**DB.** `core_auditlog` + indexes. **Backend.** service + middleware. **Frontend.** — **API.** —
**Files.** `apps/core/{models,audit,middleware}.py`, migration.
**Acceptance.** A service call inside a request writes actor + IP; the same call from a management command writes actor `NULL`.
**Tests.** diff correctness, null actor, GenericFK resolution, index existence.
**Edge cases.** Thread-local leakage between requests (middleware must clear in `finally`).

### TASK-006 — Base templates, RTL shell, AJAX plumbing
**Objective.** The UI skeleton and the JSON contract helper.
**Prerequisites.** TASK-004.
**Requirements.** RTL Arabic shell; CSRF-aware `fetch` wrapper; `@ajax` decorator implementing the 05 §G.2 envelope; `/healthz/`, `/readyz/`.
**Implementation.** `templates/base.html` (Bootstrap 5 RTL, Cairo font, sidebar, topbar), `_toast.html`, `_modal.html`, `_pagination.html`, `_filters.html`; `static/js/http.js`; `apps/core/http.py` with `@ajax`, `ok()`, `fail()`, `DomainError`.
**DB.** — **Backend.** decorator, health views. **Frontend.** base templates + JS. **API.** `GET /healthz/`, `GET /readyz/`.
**Files.** `templates/**`, `static/js/http.js`, `apps/core/{http,views}.py`, `cms/urls.py`.
**Acceptance.** A demo view returns the envelope; a raised `DomainError` becomes `{"ok": false, "code": …}` with the right status; `/readyz/` fails when the DB is down.
**Tests.** envelope shape, error mapping, method guard, permission guard.
**Edge cases.** Non-JSON body, oversized body, missing CSRF token.

---

## Phase 2 — Authentication & RBAC

### TASK-007 — Roles, permissions and `seed_roles`
**Objective.** Make the 05 §G.5 matrix real and reproducible.
**Prerequisites.** TASK-002, TASK-005.
**Requirements.** Idempotent command; custom permissions declared on the models that own them (`scan_any_group`, `approve_exceptional`, `export_reports`, `refund_payment`).
**Implementation.** `seed_roles` creates/updates five `auth.Group`s and attaches permissions from a declarative dict; `post_save` on `User` syncs group membership from `role`.
**DB.** rows in `auth_group`/`auth_permission`. **Backend.** command + signal. **Frontend.** — **API.** —
**Files.** `apps/accounts/management/commands/seed_roles.py`, `apps/accounts/permissions.py`, `apps/accounts/signals.py`.
**Acceptance.** Running twice changes nothing; each role holds exactly the documented permission set.
**Tests.** per-role permission set equality; re-run idempotency.
**Edge cases.** A permission that does not exist yet (app not migrated) must fail with a clear message.

### TASK-008 — Login, logout, password change, throttling
**Objective.** Session auth with brute-force protection.
**Prerequisites.** TASK-007.
**Requirements.** 5 attempts/min/IP; `must_change_password` forces a redirect; last login IP recorded.
**Implementation.** Django auth views with custom templates; `django-ratelimit` on the login POST; middleware for the forced password change.
**DB.** — **Backend.** views, middleware. **Frontend.** login page (RTL, branded). **API.** `POST /accounts/login/`, `POST /accounts/logout/`.
**Files.** `apps/accounts/{views,forms,urls,middleware}.py`, `templates/accounts/*.html`.
**Acceptance.** Sixth attempt in a minute returns 429; a seeded user is redirected to change password before any other page.
**Tests.** throttle, redirect loop safety, IP capture, logout invalidates session.
**Edge cases.** Proxy `X-Forwarded-For` handling; the change-password page itself must be exempt from the redirect.

### TASK-009 — Permission decorators, mixins and group scoping
**Objective.** One enforcement pattern used everywhere.
**Prerequisites.** TASK-007.
**Requirements.** Instructors see only their own groups; unauthorized object access returns 404, not 403.
**Implementation.** `@require_perm("app.codename")` composing with `@ajax`; `ScopedQuerysetMixin`; `accounts/scoping.py::visible_groups(user)`, `visible_lessons(user)`, `visible_students(user)`.
**DB.** — **Backend.** helpers. **Frontend.** — **API.** —
**Files.** `apps/accounts/{decorators,scoping,mixins}.py`.
**Acceptance.** An instructor requesting another instructor's group gets 404; a cashier POSTing to a student-edit endpoint gets 403.
**Tests.** parametrized matrix skeleton (filled in later phases).
**Edge cases.** Super admin bypass; users with no instructor profile.

### TASK-010 — User management UI
**Objective.** Admins create and manage staff accounts.
**Prerequisites.** TASK-009.
**Requirements.** Role assignment, deactivate (never delete), password reset by admin, audited.
**Implementation.** List/create/edit views restricted to admins; `role` change writes an audit entry.
**DB.** — **Backend.** views/forms. **Frontend.** `settings/users` pages. **API.** `GET/POST /api/users/`, `PATCH /api/users/<id>/`.
**Files.** `apps/accounts/views.py`, `templates/accounts/users/*.html`.
**Acceptance.** A center admin cannot grant `SUPER_ADMIN`; deactivation blocks login immediately.
**Tests.** privilege escalation attempt, audit entry on role change.
**Edge cases.** Deactivating your own account; the last super admin must not be deactivatable.

### TASK-011 — Settings UI (policy editor)
**Objective.** Expose 03 §D.7 to admins with typed widgets.
**Prerequisites.** TASK-004, TASK-009.
**Requirements.** Grouped by `group`; inline help; changes audited; non-editable keys hidden.
**Implementation.** Dynamic form built from `DEFAULTS` metadata; save writes `Setting` rows and busts the cache.
**DB.** — **Backend.** view + form factory. **Frontend.** `settings/policies` page. **API.** `POST /api/settings/`.
**Acceptance.** Toggling `payments.enforce_on_attendance` takes effect on the next scan with no restart.
**Tests.** validation per type, audit entry, cache invalidation.
**Edge cases.** Out-of-range values (negative minutes) rejected.

---

## Phase 3 — Educational Structure

### TASK-012 — `EducationalStage` model & CRUD
**Objective.** Configurable stages.
**Prerequisites.** TASK-006, TASK-009.
**Requirements.** `name` unique, `name_ar`, `code` unique, `order`, `is_active`; never hard-coded.
**Implementation.** Model + admin + list/create/edit AJAX endpoints + page.
**DB.** `academics_educationalstage`. **Backend.** model/service/views. **Frontend.** structure page tab. **API.** `GET/POST /api/stages/`, `PATCH /api/stages/<id>/`.
**Files.** `apps/academics/{models,forms,views,urls,admin}.py`, templates.
**Acceptance.** Creating "Secondary" then a duplicate returns a field error, not a 500.
**Tests.** uniqueness, ordering, deactivation hides it from selects but not from history.
**Edge cases.** Deactivating a stage that has active grades → warn, allow (soft), never cascade.

### TASK-013 — `Grade` model & CRUD
**Objective.** Grades under stages.
**Prerequisites.** TASK-012.
**Requirements.** FK to stage `PROTECT`; `UNIQUE(stage, name)`; ordered.
**Implementation.** As TASK-012 plus a `?stage=` feeder endpoint.
**DB.** `academics_grade` + `UNIQUE(stage, name)` + index `(stage, order)`. **API.** `GET/POST /api/grades/`, `PATCH /api/grades/<id>/`, `GET /api/grades/?stage=`.
**Acceptance.** Deleting a stage with grades is refused by `PROTECT`.
**Tests.** constraint, feeder filtering, ordering.
**Edge cases.** Same grade name under two different stages must be allowed.

### TASK-014 — `Subject` model & CRUD
**Objective.** Global subject catalogue.
**Prerequisites.** TASK-012.
**Requirements.** Independent of grade; unique name; UI colour.
**DB.** `academics_subject`. **API.** `GET/POST /api/subjects/`, `PATCH /api/subjects/<id>/`.
**Acceptance.** A subject can be attached to several grades in TASK-015 without duplication.
**Tests.** uniqueness; subject reuse across grades.
**Edge cases.** Renaming a subject must not break history (it is an FK, so it does not).

### TASK-015 — `GradeSubject` (subject offering) model & CRUD
**Objective.** "This grade studies this subject, at this default fee."
**Prerequisites.** TASK-013, TASK-014.
**Requirements.** `UNIQUE(grade, subject)`; `default_monthly_fee ≥ 0`; the billing anchor.
**Implementation.** Model + inline management from the grade page ("subjects taught to 3rd Secondary") + feeder `?grade=`.
**DB.** `academics_gradesubject` + constraints. **API.** `GET/POST /api/offerings/`, `PATCH /api/offerings/<id>/`, `GET /api/offerings/?grade=`.
**Acceptance.** Two grades can offer Physics at different fees; a duplicate pair is rejected by the DB.
**Tests.** unique pair, negative fee check constraint, feeder.
**Edge cases.** Changing `default_monthly_fee` must not touch existing groups or charges.

### TASK-016 — `Instructor` model & CRUD
**Objective.** Teaching staff with an optional login.
**Prerequisites.** TASK-014.
**Requirements.** Optional `OneToOne` to `User`; informational `subjects` M2M.
**DB.** `academics_instructor` (+ M2M table). **API.** `GET/POST /api/instructors/`, `PATCH /api/instructors/<id>/`.
**Acceptance.** An instructor without a user account can be assigned to groups; linking a user later grants scoped access.
**Tests.** optional link, scoping integration with TASK-009.
**Edge cases.** One user linked to two instructors must be impossible (`OneToOne`).

### TASK-017 — `Group` model & CRUD
**Objective.** The cohort entity.
**Prerequisites.** TASK-015, TASK-016.
**Requirements.** FK to `grade_subject`; `UNIQUE(grade_subject, name)`; unique `code`; capacity ≥ 0; fee defaults from the offering; grade/subject derived, never stored.
**Implementation.** Model with `grade`/`subject`/`stage` properties; creation form pre-filling `monthly_fee`; list with filters.
**DB.** `academics_group` + constraints + indexes. **API.** `GET/POST /api/groups/`, `PATCH /api/groups/<id>/`, `GET /api/groups/?offering=`.
**Acceptance.** A group cannot exist for a (grade, subject) pair that is not offered; `group.subject` resolves without extra queries when `select_related` is used.
**Tests.** constraint set, property resolution, `assertNumQueries` on the list view.
**Edge cases.** Changing a group's offering after lessons exist → forbid (raise `DomainError`).

### TASK-018 — `GroupSchedule` model & UI
**Objective.** Recurring weekly slots to generate lessons from.
**Prerequisites.** TASK-017.
**Requirements.** Egyptian week (0=Saturday); `end_time > start_time`; `UNIQUE(group, weekday, start_time)`; room conflicts warned.
**DB.** `academics_groupschedule` + constraints. **API.** `GET/POST /api/groups/<id>/schedule/`, `DELETE /api/schedule/<id>/`.
**Acceptance.** A group with two weekly slots produces eight lessons in a four-week month (TASK-042).
**Tests.** constraints, weekday mapping, overlap warning.
**Edge cases.** Slots crossing midnight → reject in MVP with a clear message.

### TASK-019 — Academic seed & demo data command
**Objective.** One command to get a realistic hierarchy.
**Prerequisites.** TASK-018.
**Requirements.** Idempotent; `--demo` builds the brief's example tree (Secondary → 3 grades → Physics/Math → Groups A/B/C).
**Implementation.** `seed_academics [--demo]` using `get_or_create` throughout.
**Files.** `apps/academics/management/commands/seed_academics.py`.
**Acceptance.** Running twice yields identical row counts.
**Tests.** idempotency; structure matches the brief's example.
**Edge cases.** Must refuse to run with `--demo` when `DEBUG=False` unless `--force`.

---

## Phase 4 — Student Management

### TASK-020 — `Student` model & migration
**Objective.** The durable person record.
**Prerequisites.** TASK-013.
**Requirements.** Fields per 02 §4; **no** group/card/payment columns; status choices; grade FK `PROTECT`; indexes for search.
**Implementation.** Model + `stage` property + admin; Postgres-only trigram index guarded by vendor check.
**DB.** `students_student` + indexes. **API.** —
**Acceptance.** `Student._meta` contains no field named `group`, `card`, or `is_paid` (asserted by a test).
**Tests.** field guard test, status choices, grade `PROTECT`, index creation on Postgres.
**Edge cases.** Missing DOB/phone allowed; guardian phone effectively required by the form, not the DB.

### TASK-021 — Student code generator
**Objective.** Collision-free human-facing codes.
**Prerequisites.** TASK-020.
**Requirements.** Format `STD-000001`; unique under concurrency; never reused.
**Implementation.** `students.services.next_student_code()` using a locked counter row (or a Postgres sequence), called inside the create transaction; `UNIQUE` index as the arbiter.
**Acceptance.** 50 concurrent creations produce 50 distinct codes.
**Tests.** threaded creation (Postgres), format, retry on `IntegrityError`.
**Edge cases.** Manual code entry by admin must be validated against the same format.

### TASK-022 — Student create/edit forms & services
**Objective.** Validated writes with audit.
**Prerequisites.** TASK-021, TASK-005.
**Requirements.** Photo ≤ 2 MB, images only, stored under a non-sequential name; phone format `^01[0125][0-9]{8}$` (Egyptian mobile) with a permissive fallback; every write audited.
**Implementation.** `StudentForm`; `create_student()` / `update_student()` services; Pillow validation.
**API.** `POST /api/students/`, `PATCH /api/students/<id>/`.
**Acceptance.** Invalid phone returns `field_errors`; a 5 MB upload is rejected before hitting disk.
**Tests.** validation matrix, audit diff, photo path non-sequential.
**Edge cases.** Duplicate student (same name + guardian phone) → warn, allow.

### TASK-023 — Student list page + AJAX filters/search
**Objective.** Fast finding.
**Prerequisites.** TASK-022.
**Requirements.** Server-side pagination (25/page), filters (stage, grade, status, group, subject, has-card, balance>0 later), debounced search over code/name/guardian phone.
**Implementation.** Page view + `?partial=1` fragment endpoint sharing one queryset builder.
**API.** `GET /api/students/`.
**Acceptance.** 2,000 students: first page < 300 ms, ≤ 5 queries.
**Tests.** `assertNumQueries`, filter correctness, pagination boundaries, scoping for instructors.
**Edge cases.** Arabic search with/without diacritics and with alef variants (normalise `أإآ` → `ا` in the search term).

### TASK-024 — Student profile page (tab shell)
**Objective.** The profile skeleton later phases fill.
**Prerequisites.** TASK-023.
**Requirements.** Header (photo, code, grade, status, active card, quick actions) + tabs Overview/Groups/Attendance/Payments/Cards/Audit, each lazy-loaded by AJAX.
**API.** `GET /api/students/<id>/`.
**Acceptance.** Tabs load independently; unavailable tabs show a placeholder, not an error.
**Tests.** permission scoping, lazy-load endpoints return 200.
**Edge cases.** Student with no photo, no card, no groups.

### TASK-025 — Student status transitions & typeahead
**Objective.** Lifecycle changes and global search feeder.
**Prerequisites.** TASK-024.
**Requirements.** Status change requires a reason and is audited; typeahead returns ≤ 10 results across code/name/guardian phone.
**API.** `POST /api/students/<id>/status/`, `GET /api/students/search/?q=`.
**Acceptance.** Suspending a student is visible in the scan flow later (gate 3); audit shows old → new + reason.
**Tests.** reason required, audit entry, typeahead ranking and limit.
**Edge cases.** `GRADUATED`/`TRANSFERRED` students must remain fully readable in history.

---

## Phase 5 — Pre-Printed Card Management

### TASK-026 — `StudentCard` model + constraints
**Objective.** Card inventory with an unfakeable lifecycle.
**Prerequisites.** TASK-020.
**Requirements.** `card_number` unique, `qr_token` unique+indexed, status choices, `current_student` nullable, the status↔holder `CHECK`, the partial unique on holder.
**DB.** `cards_studentcard` + all constraints of 02 §5.
**Acceptance.** Direct ORM attempts to set `status=ASSIGNED` with a null holder raise `IntegrityError`.
**Tests.** one test per named constraint; token uniqueness.
**Edge cases.** Token with trailing `\r` must not create a near-duplicate (normalise before save).

### TASK-027 — `CardAssignment` history model
**Objective.** Auditable card timeline.
**Prerequisites.** TASK-026.
**Requirements.** Partial uniques (one open per card, one open per student); date ordering check.
**DB.** `cards_cardassignment` + constraints + index `(student, -assigned_at)`.
**Acceptance.** Two open assignments for one student are impossible at the DB level.
**Tests.** constraint tests; timeline ordering.
**Edge cases.** Backdated assignment entry by an admin (allowed, audited).

### TASK-028 — Token generation & normalisation utilities
**Objective.** One place that defines what a token is.
**Prerequisites.** TASK-026.
**Requirements.** `CMS1:` + 22 chars base62 (`secrets.token_urlsafe(16)`); normalise (strip whitespace/CR, cap 64 chars, regex allowlist); masking helper.
**Implementation.** `cards/tokens.py`: `generate_token()`, `normalize_token(raw)`, `mask_token(t)`.
**Acceptance.** `normalize_token("  CMS1:abc\r\n")` → `"CMS1:abc"`; a 200-char input raises before any DB call.
**Tests.** fuzz the normaliser; entropy/uniqueness over 100k generations; masking never reveals > 8 chars.
**Edge cases.** Unprefixed vendor tokens accepted per `cards.accept_unprefixed_tokens`.

### TASK-029 — `import_cards` / `generate_cards` commands + batch export
**Objective.** Get inventory into the system, or out to the printer.
**Prerequisites.** TASK-028.
**Requirements.** All-or-nothing import; duplicate detection against DB *and* within the file; generation creates `AVAILABLE` rows; export produces CSV + printable QR sheet (A4, 10 cards/page).
**Implementation.** Commands + an export view rendering QR via `qrcode` into inline data-URI images (no external calls).
**API.** `POST /api/cards/import/` (admin), `GET /api/cards/export/?batch=`.
**Acceptance.** A 500-row file imports in one transaction; one duplicate aborts the whole import with a row-numbered error.
**Tests.** duplicate in-file, duplicate in-DB, malformed CSV, 10k-row performance, QR decodes back to the token.
**Edge cases.** BOM/UTF-16 CSVs from Excel; whitespace-padded columns.

### TASK-030 — Card lookup service & endpoint (enrollment scan)
**Objective.** Answer "what is this card?" safely.
**Prerequisites.** TASK-029.
**Requirements.** Distinct codes for not-found / assigned / lost / disabled / replaced / available; holder name shown only with the students-view permission.
**API.** `POST /api/cards/lookup/` `{qr_token}`.
**Acceptance.** Each of the six states returns its documented `code` and never leaks PII to unauthorised roles.
**Tests.** six-state matrix; PII redaction; rate limit (30/min/user).
**Edge cases.** Token that exists but whose holder was soft-deactivated.

### TASK-031 — Card assignment service & endpoint
**Objective.** Link a card to a student, race-safe.
**Prerequisites.** TASK-030, TASK-027.
**Requirements.** `atomic` + `select_for_update` + re-verify `AVAILABLE`; create `CardAssignment`; set `issued_at`; audit.
**API.** `POST /api/cards/assign/` `{card_id|qr_token, student_id}`.
**Acceptance.** Concurrent assignment of one card to two students → exactly one success, the other gets `ERR_CARD_UNAVAILABLE`.
**Tests.** threaded assignment (Postgres); assigning to a student who already holds a card → error; audit entries.
**Edge cases.** Student with a `LOST` card being given a new one → must go through TASK-033 (replace), not assign.

### TASK-032 — Mark lost / disable services
**Objective.** Revoke a card instantly.
**Prerequisites.** TASK-031.
**Requirements.** Reason required; closes the open history row; nulls `current_student`; sets timestamps; audited.
**API.** `POST /api/cards/<id>/mark-lost/`, `POST /api/cards/<id>/disable/`.
**Acceptance.** The token stops working on the next scan (verified in Phase 8 tests); the student now has no active card.
**Tests.** state transitions, constraint compliance, audit, reason enforcement.
**Edge cases.** Marking an `AVAILABLE` card lost (allowed — cards get lost in the drawer too).

### TASK-033 — Card replacement service
**Objective.** Old card out, new card in, history intact.
**Prerequisites.** TASK-032.
**Requirements.** Single transaction per 03 §E.4; `replaced_by` link; two audit entries.
**API.** `POST /api/cards/<id>/replace/` `{new_card_id|new_qr_token, reason}`.
**Acceptance.** After replacement: old card `LOST`/`DISABLED` with no holder, new card `ASSIGNED`, two history rows (one closed, one open), **all prior attendance rows unchanged**.
**Tests.** the full assertion set above; new card not `AVAILABLE` → error; concurrency.
**Edge cases.** Replacing with a card already held by someone else; replacing a card whose student is inactive.

### TASK-034 — Card inventory UI + student card tab
**Objective.** Operate cards without the shell.
**Prerequisites.** TASK-033.
**Requirements.** Status tiles, filters (status/batch/number), actions, "scan to find", card timeline on the profile (§18).
**API.** `GET /api/cards/`, `GET /api/cards/<id>/history/`, `GET /api/students/<id>/cards/`.
**Acceptance.** A receptionist can find an available card, assign it, mark it lost and replace it without leaving the UI.
**Tests.** view permissions, filter correctness, timeline ordering.
**Edge cases.** 10,000 cards → pagination performance.

---

## Phase 6 — Groups & Student Assignments

### TASK-035 — `StudentGroupAssignment` model + partial unique index
**Objective.** The "usual group" record, historical and unambiguous.
**Prerequisites.** TASK-017, TASK-020.
**Requirements.** Denormalized `grade_subject` validated against `group`; partial `UNIQUE(student, grade_subject) WHERE status='ACTIVE'`; date-order check.
**DB.** `students_studentgroupassignment` + constraints + indexes.
**Acceptance.** A second ACTIVE row for the same offering raises `IntegrityError`; `grade_subject` mismatch is rejected by `clean()`.
**Tests.** constraint tests; denormalization consistency; index used by the hot-path query (`EXPLAIN`).
**Edge cases.** Historical rows may overlap in dates as long as only one is ACTIVE.

### TASK-036 — Assign / end assignment services
**Objective.** Controlled membership changes.
**Prerequisites.** TASK-035.
**Requirements.** `assign_student(student, group, start_date, is_default)`; `end_assignment(assignment, end_date, reason)`; capacity check → warning not block; audited.
**API.** `POST /api/groups/<id>/students/`, `DELETE /api/groups/<id>/students/<student_id>/`.
**Acceptance.** Ending an assignment never deletes a row; the student keeps full history.
**Tests.** capacity warning, audit, double-assign rejection, grade mismatch rejection (student's grade must match the offering's grade).
**Edge cases.** Assigning a student whose grade differs from the offering → block with `ERR_GRADE_MISMATCH` (admins may override with a reason).

### TASK-037 — Transfer service (group change)
**Objective.** Move a student between groups of the same subject, preserving history.
**Prerequisites.** TASK-036.
**Requirements.** One transaction: end old (`status=TRANSFERRED`, `end_reason=GROUP_CHANGE`) + create new ACTIVE; both audited; charges untouched.
**API.** `POST /api/assignments/<id>/transfer/`.
**Acceptance.** Two rows exist afterwards; the old row still points at the old group; the current month's charge is not duplicated or re-priced.
**Tests.** row-count and field assertions; billing untouched; attendance history untouched.
**Edge cases.** Transfer to the same group → no-op with a clear message; transfer across offerings → rejected (that is an end + new assign).

### TASK-038 — Group roster & dashboard shell
**Objective.** See who is in a group.
**Prerequisites.** TASK-036.
**Requirements.** Roster of ACTIVE assignments with capacity meter; placeholders for attendance/payment columns (filled in Phases 8–9).
**API.** `GET /api/groups/<id>/students/`.
**Acceptance.** Roster loads in ≤ 3 queries; capacity meter reflects reality.
**Tests.** `assertNumQueries`, scoping, ordering by name.
**Edge cases.** Group with zero students; group over capacity (allowed, flagged).

### TASK-039 — Enrollment wizard (student → groups → card)
**Objective.** One flow for the front desk.
**Prerequisites.** TASK-031, TASK-036.
**Requirements.** Three steps; multi-subject selection with fees and a running monthly total; card step skippable; each step saves independently (no giant atomic form).
**Frontend.** Wizard UI with progress; step 3 focuses the hidden scan input.
**Acceptance.** A student can be created, assigned to three subjects and carded in under 90 seconds.
**Tests.** partial completion resumes correctly; abandonment leaves a valid student with no card.
**Edge cases.** Card scan fails mid-wizard → student is already saved, wizard resumes at step 3.

---

## Phase 7 — Lessons

### TASK-040 — `Lesson` model + constraints
**Objective.** Concrete sessions.
**Prerequisites.** TASK-017.
**Requirements.** Fields per 02 §6; `UNIQUE(group, scheduled_start)`; time-order and window-order checks; `lesson_date` derived in `save()` from local time.
**DB.** `lessons_lesson` + constraints + indexes.
**Acceptance.** A lesson created at 08:00 Cairo during DST stores the correct UTC instant and the correct local `lesson_date`.
**Tests.** DST boundary test (freezegun + explicit tz), constraints, derived date.
**Edge cases.** Lesson spanning midnight; a lesson moved to another day must keep `lesson_date` consistent.

### TASK-041 — Window & late defaults resolution
**Objective.** One function decides a lesson's timing policy.
**Prerequisites.** TASK-040, TASK-004.
**Requirements.** Precedence: explicit lesson value → group override → global setting.
**Implementation.** `lessons.services.resolve_windows(group, scheduled_start)` returning the three datetimes.
**Acceptance.** Changing the global setting affects only future lessons, never existing ones.
**Tests.** precedence matrix; immutability of created lessons.
**Edge cases.** `window_close_after` shorter than the lesson duration → warn.

### TASK-042 — `generate_lessons` service, command & endpoint
**Objective.** Bulk-create a month of lessons from schedules.
**Prerequisites.** TASK-041, TASK-018.
**Requirements.** Idempotent (skip existing `(group, scheduled_start)`); date range; optional holiday list; per-group or all groups; returns a report.
**API.** `POST /api/lessons/generate/`; command `generate_lessons --from --to [--group]`.
**Acceptance.** Running twice creates nothing new; a group with two weekly slots yields the expected count.
**Tests.** idempotency, count correctness, DST week, holiday skip.
**Edge cases.** Schedule added mid-month → generation fills only the remaining dates.

### TASK-043 — Lesson lifecycle actions
**Objective.** Open, complete, cancel — with guards.
**Prerequisites.** TASK-042.
**Requirements.** `SCHEDULED→OPEN→COMPLETED`, `*→CANCELLED` (reason required); `open` snapshots `expected_students`; `complete` calls the sweep (stubbed until TASK-055); all audited.
**API.** `POST /api/lessons/<id>/open/|complete/|cancel/`.
**Acceptance.** Invalid transitions return `409` with a documented code; opening twice is a no-op success.
**Tests.** transition matrix, permission scoping, audit.
**Edge cases.** Opening a lesson far outside its window (allowed for admins, warned).

### TASK-044 — Lessons list & detail pages
**Objective.** Operational view of the timetable.
**Prerequisites.** TASK-043.
**Requirements.** Tabs Today/Upcoming/Active/Completed; filters; detail shell with counters (zeros until Phase 8).
**API.** `GET /api/lessons/`, `GET /api/lessons/active/`.
**Acceptance.** "Active" lists exactly the lessons in `OPEN` state; instructors see only their own.
**Tests.** filters, scoping, `assertNumQueries`.
**Edge cases.** Overlapping lessons for one instructor → flagged in the UI.

### TASK-045 — Attendance-window helper for the scanner
**Objective.** Reusable "is this lesson scannable now?" check.
**Prerequisites.** TASK-043.
**Implementation.** `lessons.services.window_state(lesson, now)` → `BEFORE_OPEN | OPEN | AFTER_CLOSE`, plus a cached lesson snapshot (`lesson:{id}:v{updated_at}`).
**Acceptance.** The scan path can evaluate lesson state with zero DB queries on a cache hit.
**Tests.** boundary times (exactly at open/close), cache invalidation on lesson update.
**Edge cases.** Clock skew between scanner device and server — the server clock is authoritative, always.

---

## Phase 8 — QR Attendance (core)

### TASK-046 — `Attendance` model + constraints + indexes
**Objective.** The table that encodes Principle 2.
**Prerequisites.** TASK-040, TASK-035.
**Requirements.** Fields per 02 §7; `UNIQUE(lesson, student)`; time-order, state↔check-in, and makeup-link `CHECK`s; the five indexes including the partial alternative-group index.
**DB.** `attendance_attendance`.
**Acceptance.** `assigned_group` and `attended_group` are separate nullable/non-null FKs; a `MAKEUP` row without `makeup_for_lesson` is rejected by the DB.
**Tests.** one test per named constraint; index presence; `EXPLAIN` uses `uq_attendance_lesson_student` for the get_or_create lookup.
**Edge cases.** A student attending a group whose offering they are not assigned to → `assigned_group` NULL is legal.

### TASK-047 — `AttendanceEvent` model
**Objective.** Append-only forensic log + idempotency store.
**Prerequisites.** TASK-046.
**Requirements.** Never stores raw tokens; `idempotency_key` unique nullable; `latency_ms`; indexes `(lesson, -created_at)`, `(result_code, -created_at)`.
**Acceptance.** A test asserts no field of this model ever receives a value containing the `CMS1:` prefix.
**Tests.** token-leak test, uniqueness of idempotency key, ordering.
**Edge cases.** Events for rejected scans have null attendance/student — must still be queryable.

### TASK-048 — Result codes module
**Objective.** Single source of truth for the scanner contract.
**Prerequisites.** TASK-006.
**Requirements.** Every code of 03 §D.6 mapped to HTTP status, severity (`OK|WARN|NEEDS_APPROVAL|ERR`), colour, sound, `ar`/`en` message.
**Implementation.** `attendance/result_codes.py` with a frozen dict + helper `result(code, **data)`.
**Acceptance.** A test iterates every code and asserts a complete mapping; the UI reads severity/colour from the payload, never hard-codes them.
**Tests.** completeness, no duplicate codes, translations present.
**Edge cases.** Adding a code without a message must fail CI.

### TASK-049 — Eligibility classifier
**Objective.** Pure function implementing decision table 03 §D.4.
**Prerequisites.** TASK-046, TASK-035.
**Requirements.** Inputs `(student, lesson, assigned_group_id, makeup_for_lesson_id)`; output `(attendance_type, decision)` where decision ∈ `ALLOW|WARN|APPROVAL|BLOCK`; reads policies from the registry; **no DB writes**.
**Implementation.** `attendance/eligibility.py::classify()`; `resolve_assigned_group(student_id, grade_subject_id)` is a separate one-query helper.
**Acceptance.** Scenarios A/B/C/D produce exactly the documented types and decisions under all policy combinations.
**Tests.** exhaustive parametrized matrix (4 scenarios × 3 policies); zero-write assertion; single-query assertion for the resolver.
**Edge cases.** Student with an ACTIVE assignment that started in the future / ended yesterday → treated as not assigned for that date.

### TASK-050 — Scan service: check-in path
**Objective.** Gates 1–6 and 9–10 of 03 §D.5 for a first scan.
**Prerequisites.** TASK-049, TASK-045, TASK-032.
**Requirements.** Ordered gates; `get_or_create` inside `atomic()`; `assigned_group` frozen at scan time; `late_minutes` computed; `card_used`, `checked_in_by`, `scan_source` recorded; event written; `latency_ms` measured.
**Implementation.** `attendance/services.py::scan(lesson_id, qr_token, *, actor, device_id, idempotency_key, intent, makeup_for_lesson_id)`.
**Acceptance.** ≤ 6 queries (asserted); normal/alternative/late all correct; **no write to `StudentGroupAssignment` occurs** (asserted by a signal spy).
**Tests.** cases 1–4, 11, 13 of 06 §K.2; `assertNumQueries(6)`; principle-2 regression test.
**Edge cases.** Card whose holder was just deactivated; lesson cached but cancelled a second ago (cache invalidation).

### TASK-051 — Duplicate protection & check-out path
**Objective.** Second and subsequent scans.
**Prerequisites.** TASK-050.
**Requirements.** Within `duplicate_window_seconds` → `WARN_DUPLICATE`, no write; within `min_checkout_gap_minutes` → duplicate; beyond → check-out with `duration_minutes` and `PARTIAL` derivation; `CHECKED_OUT` + scan → `WARN_ALREADY_CHECKED_OUT`; `select_for_update` on the row.
**Acceptance.** The state machine of 03 §D.2 is fully honoured; check-out before check-in returns `ERR_NOT_CHECKED_IN`.
**Tests.** cases 7–10, 18; time-travel with freezegun.
**Edge cases.** A duplicate arriving while the first request is still committing (row lock resolves it).

### TASK-052 — Idempotency & concurrency handling
**Objective.** Safe retries and simultaneous scanners.
**Prerequisites.** TASK-051.
**Requirements.** Replay of an `idempotency_key` returns the stored response; `IntegrityError` on the unique attendance key is caught and converted to the duplicate path.
**Acceptance.** Two threads, same card, same lesson → one row, one OK, one `WARN_DUPLICATE`; replayed key → byte-identical payload, no new row.
**Tests.** case 15 and 16 (Postgres `TransactionTestCase`).
**Edge cases.** Same key replayed against a *different* lesson → treated as a new scan (key is scoped by lesson).

### TASK-053 — Scan endpoint, validation, rate limiting
**Objective.** Expose the service over HTTP.
**Prerequisites.** TASK-052, TASK-009.
**Requirements.** Payload validation per 05 §G.4 (token regex + length before DB); permission `attendance.add_attendance` + group scoping; rate limits 10/s/device, 600/min/user; response contains only the permitted fields; payment block only with `payments.view_monthlycharge`.
**API.** `POST /api/attendance/scan/`.
**Acceptance.** Response matches the documented JSON exactly; a 200-char token is rejected with zero queries.
**Tests.** contract test against the documented payload; role matrix; rate-limit trip; PII exposure test.
**Edge cases.** Missing CSRF; non-JSON body; unknown lesson id.

### TASK-054 — Manual attendance, correction, cancel, approve
**Objective.** Human overrides, fully audited.
**Prerequisites.** TASK-053.
**Requirements.** Reason mandatory; `is_manual=True`; status re-derived; `AuditLog` + `AttendanceEvent(MANUAL_EDIT)`; approval flow sets `approved_by`/`approved_at` and `attendance_type=EXCEPTIONAL`.
**API.** `POST /api/attendance/manual/`, `PATCH /api/attendance/<id>/`, `POST /api/attendance/<id>/cancel/`, `POST /api/attendance/<id>/approve/`, `POST /api/attendance/<id>/checkout/`.
**Acceptance.** No path exists to change attendance without a reason and an audit row (asserted by scanning the service module for direct `.save()` calls in CI).
**Tests.** reason enforcement, audit diff correctness, permission scoping, re-derivation of status.
**Edge cases.** Editing an attendance whose lesson is `COMPLETED` (allowed for admins, audited); cancelling then re-instating.

### TASK-055 — Lesson completion sweep
**Objective.** Close the lesson correctly.
**Prerequisites.** TASK-054, TASK-043.
**Requirements.** Auto-checkout dangling `CHECKED_IN` rows (if enabled) with `scan_source=SYSTEM`; materialise `ABSENT` rows for students with an ACTIVE assignment to this group at lesson time who have no row; idempotent; runs in one transaction; reports counts.
**Acceptance.** Re-completing a lesson creates nothing new; students who attended elsewhere as ALTERNATIVE are still marked absent **from their own lesson** (correct, and visible in reports as such).
**Tests.** case 17; idempotency; bulk performance for a 40-student group (≤ 4 queries via `bulk_create`).
**Edge cases.** Assignment created after the lesson date must not generate a retroactive absence.

### TASK-056 — Scanner console UI (HID + camera)
**Objective.** The screen the center lives on.
**Prerequisites.** TASK-053.
**Requirements.** Hidden always-focused input; Enter/idle-timer submit; big colour-coded result card with photo; sound per severity; last-10 event strip; camera toggle (`html5-qrcode`); no optimistic rendering; offline queue in `localStorage` with retry using the original keys.
**Frontend.** `templates/attendance/scanner.html`, `static/js/scanner.js`.
**Acceptance.** A physical HID scanner produces a result in < 1 s with zero mouse interaction; refocus survives clicks anywhere.
**Tests.** Playwright keystroke simulation (happy, duplicate, error paths); offline queue drain test.
**Edge cases.** Scanner sending keystrokes faster than the debounce; two Enter presses; browser tab losing focus.

### TASK-057 — Lesson dashboard + live feed
**Objective.** Real-time operational view (§37).
**Prerequisites.** TASK-055.
**Requirements.** Counters (expected, present, late, absent, alternative, currently inside); roster split by state; 5 s polling of `/feed/?since=`; inline manual check-in/out; complete-lesson preview ("N students will be marked absent").
**API.** `GET /api/lessons/<id>/attendance/`, `GET /api/lessons/<id>/feed/?since=`.
**Acceptance.** Counters match the DB exactly after 50 mixed scans; the feed returns only new events.
**Tests.** counter reconciliation test; `since` cursor correctness; `assertNumQueries` on the dashboard.
**Edge cases.** Two operators completing the same lesson simultaneously.

### TASK-058 — Student attendance history tab (§34)
**Objective.** Per-student attendance narrative.
**Prerequisites.** TASK-055.
**Requirements.** Columns: lesson, date, subject, usual group, attended group, in, out, duration, type, status; filters by subject/month/type; "modified" badge linking to audit.
**API.** `GET /api/students/<id>/attendance/`.
**Acceptance.** An alternative-group row visibly shows two different groups; an auto-checked-out row is labelled as such.
**Tests.** pagination, filters, `assertNumQueries`, badge rendering for manual rows.
**Edge cases.** Students with 300+ rows; students with attendance in a group they have since left.

---

## Phase 9 — Monthly Payments

### TASK-059 — `MonthlyCharge` model + generated balance
**Objective.** The obligation record.
**Prerequisites.** TASK-035, TASK-015.
**Requirements.** Fields per 02 §8; `UNIQUE(student, grade_subject, billing_month)`; non-negative and discount≤due checks; `balance` as a stored `GeneratedField`; the four indexes including the partial outstanding index.
**Acceptance.** `balance` updates automatically when `total_paid` changes; a duplicate charge raises `IntegrityError`.
**Tests.** constraint tests; generated column arithmetic; partial index usage in `EXPLAIN`.
**Edge cases.** `billing_month` not normalised to the 1st → normalise in `save()`.

### TASK-060 — `Payment` model (immutable ledger)
**Objective.** The transaction record.
**Prerequisites.** TASK-059.
**Requirements.** `kind`, `amount > 0` check, unique `receipt_number`, `collected_by` non-null `PROTECT`, `reverses` self-FK, indexes for day book and cashier reports; delete/update blocked at the service and admin layers.
**Acceptance.** `Payment.objects.filter(...).update(...)` is blocked by a guard (raise in `save()` when `pk` exists and fields other than `notes` change); admin has no delete action.
**Tests.** amount constraint, receipt uniqueness, immutability guard, `PROTECT` on charge deletion.
**Edge cases.** Backdated `paid_at` (allowed for admins, audited).

### TASK-061 — Receipt number sequence
**Objective.** Unique, ordered receipts.
**Prerequisites.** TASK-060.
**Requirements.** `R-000001`; concurrency-safe (locked counter row or DB sequence); never reused.
**Acceptance.** 50 concurrent payments produce 50 distinct receipt numbers.
**Tests.** threaded test on Postgres; format; retry on collision.
**Edge cases.** Year rollover formatting (decided: continuous numbering, no reset).

### TASK-062 — Charge generation service, command & preview
**Objective.** Bill everyone for a month, idempotently.
**Prerequisites.** TASK-059, TASK-036.
**Requirements.** Source = ACTIVE assignments overlapping the month; fee snapshot from `group.monthly_fee`; `due_date` from settings; `get_or_create`; `dry_run` returns a preview (count, total, per-group breakdown); audited.
**API.** `POST /api/charges/generate/`; command `generate_charges 2026-08 [--group] [--dry-run]`.
**Acceptance.** Re-running creates zero new rows; raising a group fee afterwards leaves issued charges unchanged.
**Tests.** cases 27, 28; mid-month assignment; ended assignment excluded; dry-run writes nothing.
**Edge cases.** Student assigned to two groups of *different* subjects → two charges (correct); a student transferred mid-month → still one charge.

### TASK-063 — Auto-charge on new assignment
**Objective.** No student silently unbilled.
**Prerequisites.** TASK-062.
**Requirements.** Creating an ACTIVE assignment triggers charge creation for the current month (if generation for that month has already run, per a `Setting`/marker); idempotent.
**Acceptance.** A student enrolled on the 20th has a charge immediately.
**Tests.** hook fires once; no duplicate; disabled when the month is not yet generated.
**Edge cases.** Backdated assignment (`start_date` last month) → charge for the start month too, flagged for review.

### TASK-064 — `record_payment` service
**Objective.** Take money, correctly, under concurrency.
**Prerequisites.** TASK-061, TASK-062.
**Requirements.** 04 §F.3 exactly: `select_for_update`, `F()` arithmetic, overpayment policy, status recomputation, audit.
**API.** `POST /api/payments/`.
**Acceptance.** Three partial payments walk `UNPAID → PARTIALLY_PAID → PAID`; concurrent payments keep `total_paid` exact.
**Tests.** cases 23, 24, 29; validation errors as `field_errors`; audit entry.
**Edge cases.** Payment against a `WAIVED`/`CANCELLED` charge → rejected; zero/negative amount → rejected.

### TASK-065 — Status recalculation + nightly drift check
**Objective.** Status is always derived, never typed.
**Prerequisites.** TASK-064.
**Requirements.** `recalculate_status(charge)` per 04 §F.4; `recalculate_charges [--month]` command recomputing `total_paid` from the ledger and reporting drift; forms never expose `status`.
**Acceptance.** Injecting a wrong `total_paid` directly is detected and corrected by the command, and reported.
**Tests.** all status transitions; sticky `WAIVED`/`CANCELLED`; drift detection.
**Edge cases.** `OVERPAID` when the policy allows it.

### TASK-066 — Refund, waive, cancel, discount
**Objective.** Corrections without mutation.
**Prerequisites.** TASK-065.
**Requirements.** Refund creates a `REFUND` row (optionally `reverses`), reason mandatory, permission `payments.refund_payment`; waive/cancel set sticky statuses with reasons; discount edits `discount_amount` with audit and re-derivation.
**API.** `POST /api/payments/<id>/refund/`, `POST /api/charges/<id>/waive/|cancel/`, `PATCH /api/charges/<id>/`.
**Acceptance.** Case 25 and 30 pass; the original payment row is byte-identical after a refund.
**Tests.** immutability, aggregate exclusions, permission checks.
**Edge cases.** Refund exceeding `total_paid` → rejected; cancelling a charge with payments → rejected (refund first).

### TASK-067 — Payments workspace UI + receipt
**Objective.** Daily cash operations.
**Prerequisites.** TASK-066.
**Requirements.** Month selector; Charges tab (filters incl. `balance>0`) with inline collect; Transactions tab (day book with per-cashier and per-method totals); collect modal defaulting to the remaining balance; printable receipt (A5 + 80 mm thermal CSS).
**API.** `GET /api/charges/`, `GET /api/payments/`, `GET /api/payments/<id>/receipt/`.
**Acceptance.** A cashier can find a student, collect and print in under 30 seconds; day-book totals reconcile with the ledger.
**Tests.** reconciliation test; permission scoping; print CSS smoke test.
**Edge cases.** Two cashiers collecting for the same charge at once (one is rejected as overpayment).

### TASK-068 — Payment gate in the scan service
**Objective.** The single, optional bridge between money and attendance.
**Prerequisites.** TASK-064, TASK-053.
**Requirements.** Gate 7 of 03 §D.5 exactly; **zero queries when disabled**; `WARN_PAYMENT_DUE` shows due/paid/remaining; `ERR_PAYMENT_BLOCKED` or `NEEDS_APPROVAL_*` per policy; scanner shows a "collect payment" action targeting the exact charge.
**Acceptance.** Case 31 passes; with the gate off, `assertNumQueries` on the scan path is unchanged from TASK-050.
**Tests.** policy matrix (enforce × unpaid × partial × grace); query-count test; independence test (attendance status never influences payment status and vice versa).
**Edge cases.** Student with no charge yet; student in the grace period; multi-subject student unpaid for a *different* subject (must not be blocked).

---

## Phase 10 — Dashboards & Reports

### TASK-069 — Dashboard aggregation service & page (§49)
**Objective.** The owner's morning screen.
**Prerequisites.** TASK-057, TASK-067.
**Requirements.** Today: lessons, active, expected, checked-in, absent, alternative. Month: expected, collected, outstanding, paid/partial/unpaid counts. Live open-lesson list. All aggregates in SQL.
**API.** `GET /api/dashboard/summary/`.
**Acceptance.** Renders in < 500 ms with a year of demo data; numbers reconcile with the underlying tables.
**Tests.** reconciliation test; `assertNumQueries` ≤ 6; role scoping.
**Edge cases.** Day with no lessons; month with no charges.

### TASK-070 — Group dashboard (§36)
**Objective.** Per-group operational + financial snapshot.
**Prerequisites.** TASK-069.
**Requirements.** Roster with attendance-rate and payment chips; last/next lesson; attendance trend; payment summary; capacity meter.
**Acceptance.** Attendance rate matches a manual count; payment chips match the charge table.
**Tests.** reconciliation; N+1 guard.
**Edge cases.** Group with no lessons yet.

### TASK-071 — Report framework
**Objective.** One template and one query pattern for all reports.
**Prerequisites.** TASK-069.
**Requirements.** Shared filter bar (date range, stage, grade, subject, group, instructor, student, status); shared table renderer; pagination; permission per report; a registry mapping slug → (query service, columns, permission).
**Files.** `apps/reports/{registry,services,views}.py`, `templates/reports/base_report.html`.
**Acceptance.** Adding a new report requires only a registry entry plus a query function.
**Tests.** filter application per report; permission enforcement.
**Edge cases.** Filters that exclude everything → empty state, not an error.

### TASK-072 — Attendance reports (§48)
**Objective.** Daily, lesson, student, group, subject, monthly, late, absent.
**Prerequisites.** TASK-071.
**Acceptance.** Every report supports the full filter set; totals reconcile with `Attendance` rows.
**Tests.** one reconciliation test per report; large-dataset performance.
**Edge cases.** Reports spanning a group transfer must attribute each row to the group actually attended.

### TASK-073 — Alternative-group & make-up report (§35)
**Objective.** The center's key management insight.
**Prerequisites.** TASK-072.
**Requirements.** Columns: student, usual group, attended group, date, type, approved-by; filters by type and month; frequency ranking ("students who changed group ≥ 3 times").
**Acceptance.** Uses the partial index (`EXPLAIN` asserted); matches the brief's example layout.
**Tests.** index usage, ranking correctness, permission.
**Edge cases.** Students with a NULL usual group (not assigned) must appear, labelled as such.

### TASK-074 — Financial reports (§48)
**Objective.** Collection, outstanding, per-group/subject summaries, daily/monthly revenue, cashier close-out.
**Prerequisites.** TASK-071, TASK-067.
**Acceptance.** Report totals equal ledger totals to the piastre (asserted); waived/cancelled excluded per 04 §F.5.
**Tests.** reconciliation tests; rounding; date-boundary (payments at 23:59).
**Edge cases.** Refunds crossing month boundaries.

### TASK-075 — Exporters (CSV / XLSX / PDF)
**Objective.** Get data out, in Arabic, intact.
**Prerequisites.** TASK-071.
**Requirements.** CSV with UTF-8 BOM; XLSX via `openpyxl` with RTL sheet direction; PDF via WeasyPrint behind a feature flag; exports audited; permission `reports.export_reports`.
**API.** `GET /api/reports/<slug>/export/?format=`.
**Acceptance.** Arabic opens correctly in Excel without an import wizard; a 10k-row export completes or is queued.
**Tests.** encoding test, column parity with the on-screen table, permission.
**Edge cases.** Very wide filters → row cap with an explicit warning, never a silent truncation.

### TASK-076 — Async export & Celery bootstrap
**Objective.** Keep big exports off the request cycle.
**Prerequisites.** TASK-075.
**Requirements.** Celery + Redis; `export_report` task; download link + expiry; Beat entries for `generate_charges` (monthly) and `recalculate_charges` (nightly).
**Acceptance.** Exports > 5,000 rows are queued and delivered; Beat generates charges on the configured day.
**Tests.** task idempotency; failure path surfaces an error to the user.
**Edge cases.** Worker down → user sees a queued state, not a hang.

---

## Phase 11 — Testing, Hardening & Security

### TASK-077 — Complete the mandatory test list
**Objective.** All 37 cases of 06 §K.2 implemented.
**Prerequisites.** Phase 10.
**Acceptance.** Every numbered case maps to a named test (a checklist test asserts the mapping exists).
**Tests.** the suite itself. **Edge cases.** Flaky time-dependent tests → all time is injected, never `now()` inside assertions.

### TASK-078 — Permission matrix suite
**Objective.** Lock 05 §G.5 in code.
**Prerequisites.** TASK-077.
**Requirements.** Parametrized over (role × endpoint × method) asserting 200/302/403/404.
**Acceptance.** Adding an endpoint without a matrix entry fails CI.
**Edge cases.** Endpoints intentionally public (`/healthz/`) are explicitly listed.

### TASK-079 — Concurrency suite on Postgres
**Objective.** Prove the locking story.
**Prerequisites.** TASK-077.
**Requirements.** Threaded tests for: same-card scan, same-charge payment, same-card assignment, receipt/student-code sequences.
**Acceptance.** Zero duplicates, zero lost updates, no deadlocks over 100 iterations.
**Edge cases.** Deadlock detection → retry decorator with a bounded backoff.

### TASK-080 — Security hardening pass
**Objective.** Apply 06 §I in full.
**Prerequisites.** TASK-078.
**Requirements.** CSP, HSTS, cookie flags, rate limits everywhere, Sentry scrubbing, `check --deploy` clean, `pip-audit` clean, `|safe` grep gate, token-leak test across responses and logs.
**Acceptance.** `manage.py check --deploy` reports zero issues on prod settings.
**Tests.** header assertions; scrubbing unit test; anonymous-access sweep over every URL name.
**Edge cases.** CSP breaking the camera scanner (`worker-src blob:` needed).

### TASK-081 — Load test of the scan endpoint
**Objective.** Verify the §52 budget.
**Prerequisites.** TASK-079.
**Requirements.** Locust/k6 scenario: 100 concurrent scans, mixed outcomes, realistic dataset.
**Acceptance.** p95 < 500 ms, error rate 0, no query-count regression.
**Edge cases.** Cold cache; Redis unavailable (must degrade, not fail).

### TASK-082 — i18n, RTL and accessibility pass
**Objective.** Make it usable by the people who will use it.
**Prerequisites.** TASK-080.
**Requirements.** Complete Arabic catalogue (`makemessages`/`compilemessages`); RTL layout audit on every page; keyboard shortcuts; colour-contrast AA; scanner readable from 2 m.
**Acceptance.** No untranslated user-facing string; every page renders correctly in RTL at 1366×768.
**Edge cases.** Mixed Arabic/Latin strings (student codes) and number formatting.

---

## Phase 12 — Production Deployment

### TASK-083 — Dockerfile & compose stack
**Objective.** Reproducible runtime.
**Prerequisites.** Phase 11.
**Requirements.** Multi-stage image, non-root, healthcheck, `collectstatic` at build; `docker-compose.prod.yml` per 07 §L.2; migrations as an explicit step.
**Acceptance.** `docker compose up` yields a working system from a clean machine.
**Edge cases.** File permissions on the media volume.

### TASK-084 — nginx, TLS and static/media serving
**Prerequisites.** TASK-083.
**Requirements.** TLS (Let's Encrypt or self-signed for LAN), gzip/brotli, `client_max_body_size`, `limit_req`, real-IP forwarding, media served with `nosniff`.
**Acceptance.** SSL Labs A (public) or a documented LAN cert procedure; `X-Forwarded-For` reaches the audit log correctly.
**Edge cases.** Camera scanning requires HTTPS — document it for LAN installs.

### TASK-085 — Backups + verified restore drill
**Prerequisites.** TASK-083.
**Requirements.** Nightly `pg_dump -Fc` + WAL archive, encrypted off-site copy, media sync, weekly automated restore into a scratch DB with row-count verification.
**Acceptance.** A restore has been performed and verified **before** go-live; the drill is scheduled and alerts on failure.
**Edge cases.** Backup disk full; restore of a dump taken mid-migration.

### TASK-086 — Monitoring, logging, alerting
**Prerequisites.** TASK-084.
**Requirements.** Sentry with scrubbing; JSON logs; `/healthz/`+`/readyz/` probes; alerts for 5xx spikes, scan p95, backup failure, disk, DB connections; daily business summary mail.
**Acceptance.** A deliberate error appears in Sentry with no PII; a simulated backup failure fires an alert.
**Edge cases.** Alert fatigue — thresholds tuned during the first week.

### TASK-087 — CI/CD pipeline
**Prerequisites.** TASK-083.
**Requirements.** PR gates (lint, migrations check, tests, coverage, audit); main → build, push, migrate, deploy, smoke test; documented rollback; additive-migration discipline.
**Acceptance.** One full deploy and one full rollback executed successfully.
**Edge cases.** A failed migration mid-deploy → runbook procedure exercised.

### TASK-088 — Runbook, go-live and staff training
**Prerequisites.** TASK-085, TASK-086, TASK-087.
**Requirements.** `docs/RUNBOOK.md` (restore, rollback, rotate secrets, reset password, re-run charge generation, recover from a bad migration); the 07 §L.8 checklist executed; Arabic quick-reference sheets for enrollment, scanning, collecting payment, correcting mistakes, lost cards.
**Acceptance.** The first real lesson is scanned with paper as a parallel fallback and the two records agree exactly; staff complete each task unaided.
**Edge cases.** Day-one internet outage → the offline queue and the paper fallback are both rehearsed.

---

## Working agreement for whoever implements this

1. **One task, one commit** (or one PR), with its tests. A task is done when its acceptance criteria are demonstrably met — not when the code exists.
2. **Never skip the constraint.** If a task says a rule is enforced by the database, an application-level check is not a substitute.
3. **Never write `StudentGroupAssignment` from the attendance code**, and never write `Attendance` from the payments code. If a task seems to require it, the design is being misread — re-read 01 §B.4.
4. **Money and attendance mutations always carry an actor and a reason.** No exceptions, including management commands (they log actor `NULL` and a reason describing the job).
5. When reality contradicts an assumption in 01 §A.3, update that table in the same PR that changes the behaviour. The documents are the specification, not a historical artefact.


# 11 — Multi-Tenancy Task Backlog

Continues the sequence of [09-tasks.md](09-tasks.md) at **TASK-089**. Architecture and rationale: [10-multi-tenancy.md](10-multi-tenancy.md) — read it before starting; every task below assumes its decisions (D1 shared schema + `tenant_id`, D2 subdomain addressing, D3 lifecycle + features + limits console).

Format per task: **Objective · Prerequisites · Requirements · Implementation · DB · Backend · Frontend · API · Files · Acceptance · Tests · Edge cases.** A dash (—) means "not applicable".

**Phase map** — 5 phases, ~19 focused days:

| Phase | Days | Tasks | Ships |
|-------|------|-------|-------|
| 13 — Tenancy Foundation | ~2 | 089 → 093 | Control plane + resolution + managers. **No domain model touched yet.** |
| 14 — Retrofitting the Domain | ~5 | 094 → 106 | `tenant` on all 20 models, constraints swapped, existing data migrated. |
| 15 — Features, Plans & Limits | ~3 | 107 → 110 | Per-client feature switches enforced at four layers. |
| 16 — Platform Console | ~5 | 111 → 118 | The dashboard you asked for. |
| 17 — Hardening & Deploy | ~4 | 119 → 123 | Leak suite, RLS, wildcard TLS, runbook. |

## Implementation log

**Phase 13 — done** (TASK-089 → 093). Control plane, resolution, managers, system check.

**Phase 14 — done** (TASK-094 → 105). All 20 models carry `tenant`; the existing
installation was migrated and verified row-for-row.

Four deviations from the plan above, each made while building and each for a
reason worth keeping:

1. **TASK-103 is not a separate release.** A `NOT NULL` column cannot exist
   before its backfill, so splitting them across deploys buys a half-migrated
   schema rather than safety. The sequence is instead three reversible steps in
   one `migrate`: `*_add_tenant` (nullable) → `tenancy.0002_bootstrap_default_tenant`
   (creates the tenant from the existing `center.name` setting, backfills every
   table, refuses to continue if any row is left unstamped) → `*_require_tenant`
   (`NOT NULL`). Every step has a reverse. Verified against the real
   `db.sqlite3`: 22 tables, identical row counts, zero null tenants.

2. **TASK-104 landed with Phase 14, not after it.** The middleware has to be in
   the stack for the suite and the app to run against tenant-scoped models at
   all; deferring it would have meant a branch that could not be exercised.

3. **`/healthz/` resolves no tenant.** It is a *process* check (07 §L.6) and must
   not touch the database — with resolution in front of it, a database outage
   would look like a dead process and an unknown Host header like a dead site.
   `/readyz/` deliberately keeps its tenant, since it is the check that *should*
   fail when the database is gone.

4. **Two mechanisms the plan did not anticipate**, both in `tenancy/base.py`:
   - *Deferred querysets.* A `ModelForm` builds its `ModelChoiceField` querysets
     when the class is **defined** — before any request, so before any tenant.
     Raising there makes the app unimportable. A queryset built outside a tenant
     is therefore marked deferred and resolves at evaluation time, which for a
     form field is inside a request. Every route to the database is covered,
     including `resolve_expression()` — a queryset used as `filter(x__in=qs)`
     would otherwise be compiled into SQL unfiltered.
   - *Constraint error re-keying.* Moving `unique=True` into
     `UniqueConstraint(["tenant", x])` silently disabled the check at the form
     layer, because `tenant` is `editable=False` and Django skips any constraint
     mentioning an excluded field — turning a duplicate student code into a 500
     at INSERT. `TenantOwnedModel` un-excludes `tenant` and reports the violation
     against `x` alone, so the message and the highlighted field are what they
     were before tenancy.

**TASK-119 partially done.** The model sweep, the cache/settings/sequence/rate-limit
isolation checks and the AST-based `all_tenants` guard are in
`apps/tenancy/tests/test_isolation.py` and run over every tenant-owned model
automatically. Still outstanding for TASK-119: the **URL sweep** (every detail
URL, another center's pk → 404).

**Phase 15 — done** (TASK-107 → 110). Feature resolution, four enforcement
layers, plan limits, subscription lifecycle.

Two bugs the tests caught, both worth recording:

1. **The quota read a cached count.** `TenantUsage` has a 60-second freshness
   window, so right after creating a student the cached number was one behind —
   long enough to walk straight past a limit of one. Limit decisions now always
   `COUNT(*)`; the cache is for the console list only. Creating a student is not
   the hot path, and the hot path never asks about quotas.

2. **The dashboard linked past its own gates.** The sidebar hid the payments
   entry correctly, but `templates/dashboard/home.html` still rendered "تحصيل
   دفعة" and the outstanding-balances report as quick actions — buttons that
   led to a 404. Gating the nav is not the same as gating the page.

Also settled while building: `client.login()` calls `authenticate()` directly
and never passes through the middleware, so tests that use it must supply the
tenant themselves; and `cms/settings/test.py` now allows `.testserver`, which
mirrors production's `.yourapp.com` so multi-host tests are possible at all.

**Phase 16 — done** (TASK-111 → 118). The platform console.

Three bugs the tests caught, all of them the kind that only show up when the
code actually runs:

1. **The console URLconf was importing tenant views.** `cms/urls_console.py`
   did `include("apps.core.urls")` for the health checks, which also mounted the
   tenant-facing `/settings/` and `/audit/` pages on the console host — where
   `/audit/` then shadowed the console's own. Including a whole app's URLs into
   that file is exactly the leak the separate URLconf exists to prevent, so
   routes now arrive one at a time and on purpose.

2. **`Tenant.primary_host` ignored `prefetch_related`.** `self.domains.filter(...)`
   builds a fresh queryset, so the client list fired one query per client — 21
   queries for 20 clients. The property now honours the prefetch when one exists.

3. **`branding` assumed a tenant.** It runs on every template render including
   the console's, where there is no center to brand; it now falls back to the
   catalogue defaults instead of raising.

Also settled: the throwaway models that prove `TenantOwnedModel` (`Widget`,
`Gadget`) register at *collection* time, so their tables must exist for the
whole session — otherwise every sweep over `apps.get_models()` (export, purge,
leak suite) hits a registered model with no table and fails in whichever
unrelated test runs next. They live in `apps/tenancy/tests/models.py` with a
session fixture in the **root** conftest, so `pytest apps/console` alone still
finds them.

**Not started:** Phase 17 (hardening: the URL leak sweep, RLS, wildcard TLS,
per-tenant backups, runbook).

---

**Three rules for the implementing agent.**

1. Never edit an existing `objects.` call site to add a tenant filter. If a query needs a filter that the manager does not already apply, the manager is wrong — fix the manager. There are 143 call sites; touching them by hand is how one gets missed.
2. Phase 14 lands in the three releases of 10 §N.12. Do not collapse them: additive migration, then data migration + constraint swap, then activation.
3. Every task that adds a cache key must namespace it by tenant in the *same commit*. A shared cache key is a data leak no queryset filter can catch.

---

## Phase 13 — Tenancy Foundation

### TASK-089 — `apps/tenancy`: Tenant + Domain models
**Objective.** The control-plane tables that name the clients and route requests to them.
**Prerequisites.** TASK-088.
**Requirements.** A tenant is identified by an immutable `slug`; a tenant has ≥1 `Domain` and exactly one primary; status drives the lifecycle of 10 §N.2; nothing in this app is tenant-owned.
**Implementation.** App label `tenancy`, added to `INSTALLED_APPS` **before** every domain app. `TenantStatus(TextChoices)` = TRIAL/ACTIVE/PAST_DUE/SUSPENDED/ARCHIVED. `Tenant` per 10 §N.2 with `slug` validated against `^[a-z0-9]([a-z0-9-]{1,30}[a-z0-9])$` and a reserved-slug blocklist (`admin`, `www`, `api`, `static`, `media`, `console`, `app`, `mail`). `Domain(tenant, host, is_primary, is_custom, verified_at)`, `host` lowercased on save.
**DB.** `tenancy_tenant`, `tenancy_domain`. `UNIQUE(host)`; partial `UNIQUE(tenant) WHERE is_primary`; index on `status`, on `expires_at`.
**Backend.** models + `Tenant.__str__` + `is_operational` property (TRIAL/ACTIVE/PAST_DUE). **Frontend.** — **API.** —
**Files.** `apps/tenancy/{__init__,apps,models,constants}.py`, `apps/tenancy/migrations/0001_initial.py`, updated `INSTALLED_APPS` in `base.py` **and** `pythonanywhere.py`.
**Acceptance.** `migrate` from empty succeeds; a second primary domain for the same tenant is rejected by the DB; a reserved slug is rejected by `full_clean`.
**Tests.** `test_slug_validation`, `test_reserved_slugs`, `test_one_primary_domain_per_tenant`, `test_host_lowercased`.
**Edge cases.** A host that is both custom and someone else's subdomain. Slug change after provisioning — forbid it (`editable=False` after first save) since it is the DNS name.

### TASK-090 — `apps/tenancy`: Plan, feature catalogue, TenantFeature
**Objective.** Declare every switchable feature in code and let a plan or a per-client override answer for it.
**Prerequisites.** TASK-089.
**Requirements.** The catalogue is code, the DB holds overrides only — the same contract `core/policies.py` already uses for settings, deliberately mirrored. An unknown feature key is always an error.
**Implementation.** `apps/tenancy/features.py` with `FeatureSpec` and the `FEATURES` tuple from 10 §N.8 (23 keys), `spec_for(key)`, `specs_for_group(group)`. `Plan` model + `PlanFeature(plan, feature_key)`. `TenantFeature(tenant, feature_key, state ∈ INHERIT|ON|OFF, note, updated_by)`. `FeatureState(TextChoices)`.
**DB.** `tenancy_plan`, `tenancy_planfeature` `UNIQUE(plan, feature_key)`, `tenancy_tenantfeature` `UNIQUE(tenant, feature_key)`.
**Backend.** models + a `seed_plans` management command creating `basic` / `standard` / `full`. **Frontend.** — **API.** —
**Files.** `apps/tenancy/{features,models}.py`, `apps/tenancy/management/commands/seed_plans.py`, migration.
**Acceptance.** `spec_for("nope")` raises `KeyError`; `seed_plans` is idempotent; a `PlanFeature` with an unknown key fails validation.
**Tests.** `test_unknown_feature_key_raises`, `test_seed_plans_idempotent`, `test_core_features_cannot_be_planned_off`, `test_depends_on_keys_all_exist`.
**Edge cases.** A `depends_on` cycle — assert acyclicity in a test over the catalogue itself.

### TASK-091 — Tenant context (contextvar) + `tenant_context()`
**Objective.** One process-wide, request-scoped answer to "which tenant is this".
**Prerequisites.** TASK-089.
**Requirements.** Set and reset with a token, never leaked between requests or threads; absence is an error, not an empty result.
**Implementation.** `apps/tenancy/context.py`: `_tenant: ContextVar`, `current_tenant()`, `require_tenant()` raising `TenantContextRequired`, `tenant_context(tenant)` context manager (re-entrant, restores the previous token), `all_tenants_context()` marker for the console. Exceptions in `apps/tenancy/exceptions.py`.
**DB.** — **Backend.** context module. **Frontend.** — **API.** —
**Files.** `apps/tenancy/{context,exceptions}.py`, `apps/tenancy/tests/test_context.py`.
**Acceptance.** Nesting `tenant_context(a)` inside `tenant_context(b)` restores `b` on exit; an exception inside the block still restores.
**Tests.** `test_nested_context_restores`, `test_exception_restores_context`, `test_thread_isolation` (two threads, two tenants, 200 interleaved reads — no cross-talk), `test_async_isolation`.
**Edge cases.** A generator that yields inside `tenant_context` — document that the context is *not* held across a yield boundary and never do it in services.

### TASK-092 — `TenantResolutionMiddleware` + session guard + status gate
**Objective.** Turn a `Host:` header into a tenant, or into a 404, before anything else runs.
**Prerequisites.** TASK-091.
**Requirements.** Zero queries steady-state; unknown host → 404 with no redirect; console host swaps the URLconf; the contextvar is reset in `finally`.
**Implementation.** Three middlewares in `apps/tenancy/middleware.py`, placed at positions [3], [8] and [11] of 10 §N.3:
- `TenantResolutionMiddleware` — normalise host (strip port, lowercase, IDNA), `cache.get(f"tenancy:host:{host}")` → miss → `Domain.objects.select_related("tenant","tenant__plan").get(host=host)`, cache 300 s (and cache the *negative* result for 60 s so an unknown-host flood does not hit the DB). Sets `request.tenant` and the contextvar; resets in `finally`. If `host == settings.CONSOLE_HOST`: `request.tenant = None`, `request.urlconf = "cms.urls_console"`.
- `TenantSessionGuardMiddleware` — after auth: `user.tenant_id != request.tenant_id` → flush session + 403; platform staff on a tenant host → allowed only when the session carries a valid impersonation record (TASK-115), else 403.
- `TenantStatusMiddleware` — `SUSPENDED`/`ARCHIVED` → render the gate template (403). Exempt: `core:healthz`, `core:readyz`, `accounts:logout`, `tenancy:gate`, `/static/`, `/media/`.
A `post_save`/`post_delete` signal on `Domain` and `Tenant` invalidates the host keys.
**DB.** — **Backend.** middleware + signals + `CONSOLE_HOST` setting. **Frontend.** `templates/tenancy/gate.html` (standalone, no sidebar, Arabic, the operator's contact line from a setting). **API.** —
**Files.** `apps/tenancy/{middleware,signals}.py`, `templates/tenancy/gate.html`, `cms/settings/base.py` (MIDDLEWARE order + `CONSOLE_HOST`), `cms/settings/pythonanywhere.py` (mirror it — that file imports nothing).
**Acceptance.** With the cache warm, a page request issues the same query count as before this task. `curl -H 'Host: nope.yourapp.com'` → 404. A suspended tenant's login page → gate, and no session is created.
**Tests.** `test_unknown_host_404`, `test_host_lookup_cached` (`assertNumQueries(0)` on the second request), `test_context_reset_after_exception`, `test_session_from_other_tenant_rejected`, `test_suspended_tenant_blocked`, `test_gate_exempt_paths`, `test_console_host_swaps_urlconf`.
**Edge cases.** Port in the Host header (`:8000` in dev). IPv6 literal. Unicode/IDNA host. A request arriving before any tenant exists (fresh install) — resolve to a 404 with a clear log line, not a 500.

### TASK-093 — `TenantOwnedModel`, `TenantManager`, and the system check that enforces them
**Objective.** Make tenant filtering the default for every domain query without editing a single existing call site.
**Prerequisites.** TASK-091.
**Requirements.** `_default_manager` is filtered; related traversal is not; a new model that forgets the base class fails `manage.py check`.
**Implementation.** In `apps/tenancy/models.py`: `TenantManager.get_queryset()` filtering on `current_tenant()` and raising `TenantContextRequired` when absent; `TenantOwnedModel` abstract base per 10 §N.4 with `objects` declared **first** and `Meta.base_manager_name = "all_tenants"`. Add `apps/tenancy/checks.py` registering a `django.core.checks` rule: every concrete model in `LOCAL_APPS` must either subclass `TenantOwnedModel` or appear in `TENANCY_EXEMPT_MODELS` (an explicit allowlist: `core.AuditLog`, `accounts.User`, everything in `tenancy`, everything in `console`) — otherwise `tenancy.E001`.
**DB.** — **Backend.** base model + manager + checks. **Frontend.** — **API.** —
**Files.** `apps/tenancy/{models,checks,apps}.py`, `apps/tenancy/tests/test_manager.py`.
**Acceptance.** Adding a throwaway model without the base class makes `manage.py check` fail with `tenancy.E001`; `Model.objects.all()` outside any context raises `TenantContextRequired`.
**Tests.** `test_default_manager_is_filtered`, `test_all_tenants_manager_is_not`, `test_no_context_raises`, `test_related_traversal_works_without_context`, `test_system_check_catches_unowned_model`.
**Edge cases.** `Model.objects` inside a migration — migrations use historical models with the plain manager, verify one runs. `refresh_from_db()` and `ModelChoiceField` querysets — both must still work (they use the base manager and the default manager respectively; assert both).

---

## Phase 14 — Retrofitting the Domain

> This phase lands as **Release A → B → C** (10 §N.12). Tasks 094–102 are Release A (additive, nullable, zero behaviour change). TASK-103 is Release B. TASK-104–106 are Release C.

### TASK-094 — `accounts.User`: tenant binding, platform staff, tenant-aware auth backend
**Objective.** Bind users to a center; keep platform staff outside every center.
**Prerequisites.** TASK-093.
**Requirements.** `username` unique **per tenant**; `NULL` tenant means platform staff and is still globally unique; login authenticates only within the resolved tenant; `createsuperuser` still works.
**Implementation.** Add `User.tenant` (nullable FK, `PROTECT`) and `User.is_platform_staff`. Drop `username`'s global `unique=True` (`_meta` override on `AbstractUser`'s field) and add `UniqueConstraint(["tenant","username"], name="uq_username_per_tenant")` + `UniqueConstraint(["username"], condition=Q(tenant__isnull=True), name="uq_platform_username")`. `TenantModelBackend(ModelBackend)` overriding `authenticate`/`get_user` to filter on `current_tenant()` (or `tenant__isnull=True` on the console host). `UserManager` gains `all_tenants`. `Role.SUPER_ADMIN` keeps its in-tenant meaning; document that it is not platform staff.
**DB.** `accounts_user.tenant_id`, `is_platform_staff`, two unique constraints (the global one dropped in Release B). **Backend.** model, manager, backend. **Frontend.** — **API.** —
**Files.** `apps/accounts/{models,backends,managers}.py`, migration, `AUTHENTICATION_BACKENDS` in `base.py` + `pythonanywhere.py`.
**Acceptance.** Two tenants can both have `admin`; logging in as `admin` on tenant A's host never returns B's user; `createsuperuser` with no host context creates `tenant=NULL, is_platform_staff=True`.
**Tests.** `test_same_username_two_tenants`, `test_login_scoped_to_host`, `test_platform_username_globally_unique`, `test_createsuperuser_makes_platform_staff`, `test_password_reset_scoped` (if reset is ever added).
**Edge cases.** `get_by_natural_key` returning multiple rows — must raise, never pick the first. A user whose tenant was archived. `AUTH_USER_MODEL` migrations are already shipped, so this is an `AlterField`, not a swap.

### TASK-095 — `core.Setting` and `core.Sequence` per tenant; registry cache namespacing
**Objective.** Give every center its own policy values and its own code counters.
**Prerequisites.** TASK-093.
**Requirements.** Both models currently use `key` as the primary key — that must become a surrogate PK with `UNIQUE(tenant, key)`. **The registry cache key must be namespaced in the same commit.**
**Implementation.** `Setting` and `Sequence` subclass `TenantOwnedModel`; PK → `BigAutoField`; `UniqueConstraint(["tenant","key"])`. `registry.CACHE_KEY` becomes `f"core:settings:{require_tenant().pk}:overrides"`; `get/set/reset/invalidate` all resolve through it; reading another tenant's settings is only possible inside `tenant_context()`. `sequences.next_number(key)` scopes its `get_or_create` to the tenant, so student codes and receipt numbers restart at 1 per center. `ratelimit.py` keys gain `{tenant_id}`.
**DB.** `core_setting`, `core_sequence` rebuilt with surrogate PKs. **Backend.** registry, sequences, ratelimit. **Frontend.** — **API.** —
**Files.** `apps/core/{models,registry,sequences,ratelimit}.py`, migrations.
**Acceptance.** Tenant A setting `payments.late_fee` leaves B on the default; two centers can hold receipt `R000001` simultaneously; `settings_registry.get(...)` with no tenant raises.
**Tests.** `test_settings_isolated_between_tenants`, `test_settings_cache_key_namespaced` (set for A, read for B in the same process — B is unaffected), `test_sequences_restart_per_tenant`, `test_ratelimit_isolated`, `test_registry_requires_context`.
**Edge cases.** A `Setting` PK change with existing rows — the data migration in TASK-103 rewrites them; verify `updated_by` FKs survive. `LocMemCache` in dev is per-process; the test must assert the *key*, not just observed behaviour.

### TASK-096 — `tenant` on `academics` (6 models) + constraint rewrite
**Objective.** Scope the academic hierarchy.
**Prerequisites.** TASK-093.
**Requirements.** Per the table in 10 §N.5: `EducationalStage.name/code`, `Subject.name/code` and `Group.code` gain `tenant`; `uq_grade_name_per_stage`, `uq_offering_grade_subject`, `uq_group_name_per_offering` and `uq_schedule_slot` are **left alone** (already tenant-scoped through their leading FK).
**Implementation.** All six models subclass `TenantOwnedModel`. `unique=True` → `UniqueConstraint(["tenant", …])`. Every existing composite `Index` gains `tenant_id` as its **first** column.
**DB.** 6 tables + constraints + index rewrites. **Backend.** models. **Frontend.** — **API.** —
**Files.** `apps/academics/models.py`, migration.
**Acceptance.** Two centers can each have a subject coded `PHY`; `makemigrations --check` is clean afterwards.
**Tests.** `test_duplicate_stage_code_across_tenants_allowed`, `test_duplicate_within_tenant_rejected`, one per rewritten constraint.
**Edge cases.** `seed_academics --demo` must now run inside `tenant_context()`; update the command in the same task.

### TASK-097 — `tenant` on `students` (2 models)
**Objective.** Scope students and their group assignments.
**Prerequisites.** TASK-096.
**Requirements.** `student_code` → `UNIQUE(tenant, student_code)`; the two partial constraints on `StudentGroupAssignment` are unchanged; `Student.photo.upload_to` becomes tenant-pathed (see TASK-106).
**Implementation.** Both models subclass `TenantOwnedModel`; indexes gain the leading `tenant_id`.
**DB.** 2 tables. **Backend.** models. **Frontend.** — **API.** —
**Files.** `apps/students/models.py`, migration.
**Acceptance.** `S000001` exists independently in two centers.
**Tests.** `test_student_code_unique_per_tenant`, `test_assignment_constraints_unchanged`.
**Edge cases.** `StudentQuerySet` custom methods must not re-add a global filter.

### TASK-098 — `tenant` on `cards` (2 models); `qr_token` stays global
**Objective.** Scope cards without weakening the scan token.
**Prerequisites.** TASK-097.
**Requirements.** `card_number` → `UNIQUE(tenant, card_number)`. **`qr_token` keeps its global `unique=True`** (10 §N.5) — and the scan lookup *still* filters by tenant.
**Implementation.** Both models subclass `TenantOwnedModel`. Card-scan index becomes `(tenant_id, qr_token)`. Verify `apps/attendance` resolves the token through `StudentCard.objects` (now tenant-filtered) and not through `all_tenants`.
**DB.** 2 tables. **Backend.** models. **Frontend.** — **API.** —
**Files.** `apps/cards/models.py`, migration.
**Acceptance.** Two centers can print card `0001`; scanning tenant B's token on tenant A's host returns the normal "unknown card" result code — **not** a 500 and not a hit.
**Tests.** `test_card_number_unique_per_tenant`, `test_qr_token_globally_unique`, `test_cross_tenant_scan_is_unknown_card`.
**Edge cases.** Bulk card import must stamp `tenant` on every row in one `bulk_create` — `bulk_create` bypasses `save()`, so the tenant stamp cannot rely on the `save()` guard of TASK-104. Handle it explicitly and test it.

### TASK-099 — `tenant` on `lessons`
**Objective.** Scope lessons.
**Prerequisites.** TASK-096.
**Requirements.** `uq_lesson_group_start` unchanged; indexes gain the leading `tenant_id`; `generate_lessons` runs per tenant.
**Implementation.** `Lesson` subclasses `TenantOwnedModel`; `LessonQuerySet` untouched; the generation command takes `--tenant` (or loops over operational tenants).
**DB.** 1 table. **Backend.** model + command. **Frontend.** — **API.** —
**Files.** `apps/lessons/models.py`, `apps/lessons/management/commands/*.py`, migration.
**Acceptance.** Generation for A creates nothing for B.
**Tests.** `test_lesson_generation_scoped`, `test_lesson_indexes_lead_with_tenant`.
**Edge cases.** `bulk_create` in the generator — same caveat as TASK-098.

### TASK-100 — `tenant` on `attendance` (2 models)
**Objective.** Scope attendance without adding a query to the hot path.
**Prerequisites.** TASK-099, TASK-098.
**Requirements.** `uq_attendance_lesson_student` unchanged; `AttendanceEvent.idempotency_key` → `UNIQUE(tenant, idempotency_key)` (it is client-generated; two centers will collide); the scan path stays within its 6-query budget.
**Implementation.** Both models subclass `TenantOwnedModel`; `(tenant_id, lesson_id, student_id)` index; re-run the scan-path query-count assertion from Phase 8 and confirm it is unchanged.
**DB.** 2 tables. **Backend.** models. **Frontend.** — **API.** —
**Files.** `apps/attendance/models.py`, migration.
**Acceptance.** The Phase 8 `assertNumQueries` test for `/api/scan/` passes with the same number.
**Tests.** `test_idempotency_key_unique_per_tenant`, `test_scan_query_budget_unchanged`, `test_scan_isolated_between_tenants`.
**Edge cases.** Offline queue replay posting an event with a key another tenant already used.

### TASK-101 — `tenant` on `payments` (2 models)
**Objective.** Scope money.
**Prerequisites.** TASK-097, TASK-096.
**Requirements.** `receipt_number` → `UNIQUE(tenant, receipt_number)`; `uq_charge_per_month` unchanged; charge generation runs per tenant.
**Implementation.** Both models subclass `TenantOwnedModel`; `(tenant_id, billing_month, status)` index; `generate_charges` takes `--tenant` and loops over operational tenants when omitted, logging a per-tenant result line.
**DB.** 2 tables. **Backend.** models + command. **Frontend.** — **API.** —
**Files.** `apps/payments/models.py`, `apps/payments/management/commands/*.py`, migration.
**Acceptance.** Two centers hold receipt `R000001`; a generation run reports one line per tenant and a failure in one tenant does not abort the others.
**Tests.** `test_receipt_number_unique_per_tenant`, `test_charge_generation_per_tenant`, `test_generation_failure_isolated`.
**Edge cases.** A refund crossing tenants is impossible by construction — assert it. Money reports must never `all_tenants`.

### TASK-102 — `tenant` on `core.AuditLog`
**Objective.** Make the audit trail answer "which center" and keep platform actions out of any center's log.
**Prerequisites.** TASK-093.
**Requirements.** `tenant` **nullable** (platform actions have none); `AuditContextMiddleware` stamps it; `audit.record()` picks it up from the context, not from an argument.
**Implementation.** Add the field (exempt model — do not subclass `TenantOwnedModel`; the console must read across tenants). `audit.record()` reads `current_tenant()`. The center's own audit screen filters on the current tenant explicitly.
**DB.** `core_auditlog.tenant_id` + index `(tenant_id, -created_at)`. **Backend.** model + audit service + middleware. **Frontend.** the existing `templates/core/audit_log.html` needs no change. **API.** —
**Files.** `apps/core/{models,audit,middleware}.py`, migration.
**Acceptance.** A tenant action writes `tenant_id`; a console action writes `NULL`; the center's audit screen shows only its own rows.
**Tests.** `test_audit_stamped_with_tenant`, `test_platform_action_has_null_tenant`, `test_audit_screen_scoped`.
**Edge cases.** A management command with no tenant context must still write a row (tenant `NULL`), not crash.

### TASK-103 — **Release B**: data migration of the existing installation
**Objective.** Turn the current single-center database into tenant #1, then make `tenant` non-null.
**Prerequisites.** TASK-094 → 102 all deployed as Release A.
**Requirements.** Runs after a **verified backup and a verified restore** (07 §L.5). Asserts a row count per table before and after. Irreversible — it is the only such step in this plan.
**Implementation.** One migration in `tenancy` plus one per app, in dependency order:
1. Create `Tenant(slug="default", name=<current `center.name` setting>, status=ACTIVE, plan=<full>)`.
2. Create its primary `Domain` from `DEFAULT_TENANT_HOST` (env var, required by this migration).
3. Backfill `tenant_id` on all 20 tables with `UPDATE … SET tenant_id = <id>` (raw `RunPython` with `all_tenants`, batched).
4. Bind every existing `User` to it, except any `is_superuser` account, which becomes platform staff.
5. Rewrite `Setting`/`Sequence` primary keys.
6. `AlterField` every `tenant` to `null=False`; drop the old global unique constraints; add the new composite ones and the rewritten indexes.
7. Assert `Model.all_tenants.count() == pre_count` for each table; raise and roll back on mismatch.
**DB.** all 20 tables. **Backend.** migrations + a `check_tenancy_migration` command that reports the pre/post counts. **Frontend.** — **API.** —
**Files.** `apps/*/migrations/00XX_backfill_tenant.py`, `apps/tenancy/management/commands/check_tenancy_migration.py`, `docs/RUNBOOK.md` section.
**Acceptance.** On a copy of the production SQLite/Postgres DB: migration completes, every count matches, the app serves the existing center on its existing host with no visible change, and every user can still log in.
**Tests.** A migration test using `django_test_migrations` (or a hand-rolled `MigratorTestCase`): seed a pre-migration DB with rows in all 20 tables, migrate, assert counts and that `tenant_id` is uniformly set.
**Edge cases.** Orphan rows whose FK parent is missing. Rows created between the backup and the migration — take the app down for this one. `DEFAULT_TENANT_HOST` unset → fail loudly before touching anything.

### TASK-104 — **Release C**: write-side guards (auto-stamp + cross-tenant FK rejection)
**Objective.** Make it impossible to write a row into the wrong tenant.
**Prerequisites.** TASK-103.
**Requirements.** `save()` stamps `tenant` from the context when unset; a mismatch raises; a FK pointing at another tenant's row raises; `bulk_create` paths are handled explicitly.
**Implementation.** `TenantOwnedModel.save()`: if `tenant_id` is `None`, set from `require_tenant()`; if set and different, raise `CrossTenantWrite`. `clean()` (and a `pre_save` for safety) walks `_meta.concrete_fields`, and for every FK whose target subclasses `TenantOwnedModel` asserts `value.tenant_id == self.tenant_id`. A `TenantOwnedQuerySet.bulk_create` override stamps every unsaved instance. Audit the four bulk paths found in TASK-098/099 and route them through it.
**DB.** — **Backend.** base model + queryset. **Frontend.** — **API.** —
**Files.** `apps/tenancy/models.py`, `apps/core/tests/test_write_guards.py`.
**Acceptance.** `Lesson(group=<B's group>)` inside A's context raises `CrossTenantWrite`; `bulk_create` of 500 cards stamps all 500.
**Tests.** `test_auto_stamp`, `test_mismatch_raises`, `test_cross_tenant_fk_rejected` (parametrised over every FK between tenant-owned models), `test_bulk_create_stamps`.
**Edge cases.** `update_fields` on `save()` — the stamp must not be silently dropped. `QuerySet.update()` cannot be guarded by `save()`; grep for it and assert in a test that no `update()` sets a FK to a tenant-owned model.

### TASK-105 — Audit `scoping.py`, services and reports for context correctness
**Objective.** Confirm the 143 existing call sites need no edits, and fix the handful that do.
**Prerequisites.** TASK-104.
**Requirements.** No business logic changes. The output is a short report plus the minimum diff.
**Implementation.** Systematically read `apps/*/services.py` (1 819 lines), `apps/*/api*.py` (2 318 lines) and `apps/accounts/scoping.py`. Flag: any `apps.get_model(...).objects` (fine), any raw SQL, any `.extra()`, any cache key without a tenant, any `select_related` crossing to a non-tenant model, any aggregate over a whole table. `visible_groups/visible_lessons/visible_students` already start from `Model.objects` and therefore inherit the tenant filter — verify, do not rewrite.
**DB.** — **Backend.** minimal fixes. **Frontend.** — **API.** —
**Files.** `docs/RUNBOOK.md` (findings section) + whatever fixes fall out.
**Acceptance.** A written list of every place that reaches the ORM outside a request (commands, signals, Celery) with its tenant-context strategy named.
**Tests.** `test_scoping_is_tenant_scoped` (an instructor in A cannot see B's groups even with a matching instructor id), `test_no_raw_sql_without_tenant`.
**Edge cases.** Report aggregates that use `.values().annotate()` — confirm the filter is applied before the aggregate.

### TASK-106 — Per-tenant media, uploads and PDF assets
**Objective.** Keep one center's student photos off another center's disk path and out of another center's URLs.
**Prerequisites.** TASK-104.
**Requirements.** `media/t/<tenant_id>/…`; a media URL from tenant A must not be servable from tenant B's host; the 5 MB upload cap and the `storage_mb` plan limit both apply.
**Implementation.** A `tenant_upload_to(subdir)` callable used by `Student.photo` and any future `FileField`. A media-serving guard: in production nginx/Caddy serves `/media/t/<id>/` only when the request's tenant matches — implemented as an `X-Accel-Redirect`/`internal` location driven by a small Django view, because a plain static mapping cannot know the tenant. In `DEBUG`, the same check in the dev static view.
**DB.** — **Backend.** upload_to + a protected media view. **Frontend.** — **API.** —
**Files.** `apps/core/storage.py`, `apps/students/models.py`, migration (`AlterField` on `photo` — existing files are moved by a data migration), nginx/Caddy config in 07.
**Acceptance.** Uploading a photo for a student in A writes under `media/t/<A>/`; requesting that URL on B's host returns 404.
**Tests.** `test_upload_path_scoped`, `test_cross_tenant_media_404`, `test_existing_photos_moved`.
**Edge cases.** A photo whose row is migrated but whose file move failed — the data migration must report, not silently drop.

---

## Phase 15 — Features, Plans & Limits

### TASK-107 — Feature resolver + per-tenant cache
**Objective.** Answer `has_feature("payments")` in zero queries.
**Prerequisites.** TASK-090, TASK-092.
**Requirements.** Resolution order exactly as 10 §N.8; result is a `frozenset` cached per tenant for 300 s; `depends_on` applied transitively; `is_core` always wins; invalidated on any `TenantFeature`/`PlanFeature`/`Plan`/`Tenant.plan` save.
**Implementation.** `apps/tenancy/resolver.py`: `enabled_features(tenant) -> frozenset[str]`, `has_feature(key, tenant=None)` defaulting to `current_tenant()`, `spec_for` validation so a typo raises rather than returning `False`. Cache key `tenancy:features:{tenant_id}`. Signals invalidate.
**DB.** — **Backend.** resolver + signals. **Frontend.** — **API.** —
**Files.** `apps/tenancy/{resolver,signals}.py`, tests.
**Acceptance.** Second call in a request costs 0 queries; disabling `payments` also disables `payments.refunds`; `has_feature("typo")` raises `KeyError`.
**Tests.** `test_resolution_order` (all five branches), `test_depends_on_transitive`, `test_core_feature_cannot_be_disabled`, `test_cache_invalidated_on_override_save`, `test_unknown_key_raises`, `test_zero_queries_when_warm`.
**Edge cases.** A tenant with no plan (should be impossible — `PROTECT` + non-null; assert). A feature removed from the catalogue while overrides still reference it — ignore the orphan row and log once.

### TASK-108 — Enforcement at four layers
**Objective.** Make a disabled feature actually unreachable, from the sidebar down to the service.
**Prerequisites.** TASK-107.
**Requirements.** Page views 404 (not 403 — do not confirm the feature exists). AJAX returns `ERR_FEATURE_DISABLED` at 404. Templates hide nav. Services refuse even from a command. Permission checks are unchanged and orthogonal: **both** must pass.
**Implementation.**
- `@require_feature("payments")` decorator + `RequireFeatureMixin`, next to the existing `require_perm`/`RequirePermMixin` in `apps/accounts/decorators.py` — same shape, so the codebase has one idiom.
- `apps/core/http.py`: `ajax(methods, *, perm=None, feature=None, login_required=True)` — one new kwarg, checked after `perm`, returning the envelope's failure form.
- `apps/tenancy/templatetags/features.py`: `{% if_feature "x" %}…{% else %}…{% endif_feature %}` and a `has_feature` filter; a `features` context processor exposing `FEATURES` (the frozenset) so `_sidebar.html` and `_topbar.html` can gate inline.
- Service-level `require_feature(key)` guard at the top of every entry point in `payments/services.py`, `cards/services.py`, `reports/`.
- Apply to every existing view/endpoint: map each URL to its feature key in one table in the task's commit message.
**DB.** — **Backend.** decorators + `@ajax` kwarg + service guards. **Frontend.** template tag, context processor, `_sidebar.html`, `_topbar.html`, `templates/dashboard/*` cards. **API.** every endpoint gains `feature=` where applicable.
**Files.** `apps/accounts/decorators.py`, `apps/core/http.py`, `apps/core/context_processors.py`, `apps/tenancy/templatetags/features.py`, `templates/partials/_sidebar.html`, `templates/partials/_topbar.html`, all `apps/*/{views,api}.py`.
**Acceptance.** With `payments` off for A: the sidebar entry is gone, `/payments/` returns 404, `POST /api/payments/collect/` returns `ERR_FEATURE_DISABLED`, the dashboard's money cards are absent, and `payments.services.collect()` raises when called from a shell. B is untouched.
**Tests.** A parametrised matrix over (feature × URL) asserting 404 when off and 200/403-by-permission when on; `test_sidebar_hides_disabled`, `test_service_guard_blocks_command`, `test_permission_and_feature_are_both_required`, `test_disabling_destroys_no_data` (turn off, count rows, turn on, count again, compare).
**Edge cases.** A user with a bookmark to a now-disabled URL. A feature disabled mid-session. `reports.financial` depending on `payments` — disabling payments must remove the financial report tab too.

### TASK-109 — Plan limits and quota enforcement
**Objective.** Stop a `basic` client from loading 5 000 students.
**Prerequisites.** TASK-107.
**Requirements.** Enforced in creation services only, never in a signal (a signal would fire during the TASK-103 migration and during restores). `NULL` = unlimited. Bulk paths check before importing and import nothing rather than a partial batch.
**Implementation.** `apps/tenancy/quota.py`: `check(tenant, resource, count=1)` raising `DomainError("ERR_QUOTA_EXCEEDED", …)` with an Arabic message naming the limit and the plan; counts read from `TenantUsage` when younger than 60 s, else a live `COUNT(*)` that refreshes it. Call sites: `students.services.create_student`, `accounts.services.create_user`, group creation, `cards.services.import_batch` (checks `count=len(batch)` up front).
**DB.** — **Backend.** quota module + call sites. **Frontend.** the error surfaces through the existing toast/envelope path — no new UI. **API.** new error code documented in 05 §G.2.
**Files.** `apps/tenancy/quota.py`, `apps/students/services.py`, `apps/accounts/services.py`, `apps/academics/services.py` (or views), `apps/cards/services.py`, `docs/05-api-and-ui.md`.
**Acceptance.** A `basic` tenant at 200/200 students gets a clear Arabic refusal; importing 100 cards with 40 slots left imports 0 and says so.
**Tests.** `test_quota_blocks_creation`, `test_unlimited_when_null`, `test_bulk_import_all_or_nothing`, `test_usage_cache_refresh`, `test_quota_not_enforced_during_migration`.
**Edge cases.** Raising a limit must take effect immediately (invalidate the usage cache on `Plan`/`Tenant` save). A tenant already **over** a newly-lowered limit: block creation, never delete.

### TASK-110 — Subscription lifecycle: expiry, grace, auto-suspend
**Objective.** Let a subscription end without you being awake.
**Prerequisites.** TASK-092, TASK-089.
**Requirements.** `TRIAL → ACTIVE → PAST_DUE → SUSPENDED` driven by dates; every transition is audited; suspension blocks login within one request; nothing is deleted.
**Implementation.** `manage.py enforce_subscriptions` (daily, cron or Celery beat): `TRIAL` past `trial_ends_at` → `PAST_DUE`; `ACTIVE` past `expires_at` → `PAST_DUE` with `grace_until = expires_at + grace_days`; `PAST_DUE` past `grace_until` → `SUSPENDED`. Each writes a `PlatformAuditLog` row. A banner partial shows the countdown for `TRIAL` (from 7 days out) and the warning for `PAST_DUE`.
**DB.** — **Backend.** command + a `Tenant.transition_to()` method that is the only writer of `status`. **Frontend.** `templates/partials/_subscription_banner.html` included from `base.html`. **API.** —
**Files.** `apps/tenancy/{models,services}.py`, `apps/tenancy/management/commands/enforce_subscriptions.py`, `templates/partials/_subscription_banner.html`, `templates/base.html`, `docs/07-deployment.md` (cron entry).
**Acceptance.** With `freezegun`, walking the clock past each boundary produces exactly the expected status and one audit row per transition; a suspended tenant cannot log in; resuming restores full function with no data change.
**Tests.** `test_trial_expires_to_past_due`, `test_grace_then_suspend`, `test_transitions_audited`, `test_suspend_resume_is_lossless`, `test_command_idempotent` (running twice changes nothing).
**Edge cases.** A tenant manually suspended by an operator must not be auto-resumed by the command. Clock skew / DST — dates are compared in the tenant's timezone.

---

## Phase 16 — Platform Console

### TASK-111 — `apps/console`: app, host guard, separate URLconf, base template
**Objective.** A console that a tenant host physically cannot route to.
**Prerequisites.** TASK-092, TASK-094.
**Requirements.** Own URLconf (`cms/urls_console.py`) selected by host; every view requires `is_platform_staff`; the tenant URLconf contains no console route; the console never runs inside a tenant context except deliberately via `tenant_context()`.
**Implementation.** `apps/console` app. `cms/urls_console.py` mounting only console + auth + health. `PlatformStaffRequiredMixin` / `@platform_staff_required` returning 404 (not 403) for a non-staff user. A distinct base template — LTR-capable, visually different from the tenant UI so nobody confuses the two — and a separate login view for the console host.
**DB.** — **Backend.** app + guards. **Frontend.** `templates/console/base.html`, `login.html`. **API.** —
**Files.** `apps/console/{__init__,apps,views,urls,decorators}.py`, `cms/urls_console.py`, `templates/console/*`, `INSTALLED_APPS`.
**Acceptance.** `/tenants/` on a tenant host → 404. A center admin authenticated on the console host → 404. `manage.py show_urls`-equivalent confirms disjoint URLconfs.
**Tests.** `test_console_unreachable_from_tenant_host`, `test_non_platform_staff_404`, `test_tenant_urlconf_has_no_console_routes`, `test_console_login_only_matches_platform_users`.
**Edge cases.** `CONSOLE_HOST` unset in dev — fall back to `console.localhost` and document it in `.env.example`.

### TASK-112 — Tenant list, detail and usage
**Objective.** See every client and their state on one screen.
**Prerequisites.** TASK-111.
**Requirements.** One query for the list (via `TenantUsage`), not `N × 6` counts. Filters: status, plan, expiring in 30 days, over quota. Search by name/slug/domain/owner email.
**Implementation.** List view with the existing pagination/filter partial idiom. Detail view with tabs: identity, plan & limits, domains, features, usage, timeline (`PlatformAuditLog` + the tenant's own `AuditLog` head). `refresh_usage(tenant)` service + a nightly `refresh_tenant_usage` command + a manual "refresh" button.
**DB.** `TenantUsage` from TASK-089/090 (add here if not yet created). **Backend.** views + usage service + command. **Frontend.** `templates/console/tenant_{list,detail}.html`. **API.** `/console/api/tenants/<id>/usage/refresh/`.
**Acceptance.** 50 tenants render in one query plus pagination; usage figures match a live count after refresh.
**Tests.** `test_list_query_count`, `test_filters`, `test_usage_refresh_matches_live_count`, `test_detail_reads_across_tenant_context_safely`.
**Edge cases.** A tenant whose usage row is missing (created before the model) — create it lazily.

### TASK-113 — Provisioning: `provision_tenant` command + console wizard
**Objective.** A new client, live, in under a minute, with no shell.
**Prerequisites.** TASK-112, TASK-095, TASK-096.
**Requirements.** One transaction; idempotent; the wizard is a thin form over the command so both paths are identical; the owner's one-time password is shown exactly once and never stored in plaintext.
**Implementation.** `provision_tenant --slug --name --plan [--owner-email] [--demo]` performing the 7 steps of 10 §N.10, every DB step inside `tenant_context(new_tenant)`. The wizard is a 3-step form (identity+plan → academics preset/demo → owner account) posting to the same service. `PlatformAuditLog` on success.
**DB.** — **Backend.** command + service. **Frontend.** `templates/console/tenant_new.html` + a result screen with the one-time credentials. **API.** —
**Acceptance.** Provisioning `elnour` yields a working `elnour.yourapp.com` login with seeded roles, settings and academic defaults; a forced failure at step 5 leaves zero rows behind.
**Tests.** `test_provision_creates_everything`, `test_provision_atomic_on_failure`, `test_provision_idempotent_slug_rejected`, `test_owner_must_change_password`, `test_seeds_run_in_tenant_context`.
**Edge cases.** A slug that collides with an existing DNS record. `--demo` on a production plan — warn.

### TASK-114 — Lifecycle controls + plan assignment + the feature toggle screen
**Objective.** The switches you asked for.
**Prerequisites.** TASK-113, TASK-107, TASK-110.
**Requirements.** Suspend/resume/archive each require a typed reason. The feature screen shows, per key: the plan's default, the current override tri-state, and the effective value — so nobody has to reason about precedence in their head. Changing a plan warns about which features it turns off and which limits would be breached.
**Implementation.** POST-only action views calling `Tenant.transition_to()`. The feature screen groups by `FeatureSpec.group`, renders a 3-way control (`inherit` / `on` / `off`), saves via one AJAX endpoint per key using the existing envelope, and shows the effective value recomputed live. `is_core` keys render disabled with a tooltip. Every change writes a `PlatformAuditLog` row with before/after.
**DB.** — **Backend.** action views + a `set_feature(tenant, key, state, actor, note)` service. **Frontend.** `templates/console/tenant_features.html`, `_suspend_modal.html`. **API.** `/console/api/tenants/<id>/features/<key>/`, `/console/api/tenants/<id>/status/`, `/console/api/tenants/<id>/plan/`.
**Acceptance.** Toggling `payments` off for A takes effect on A's next request (cache invalidated), leaves B alone, and appears in the platform audit log with the actor. Suspending A logs A's users out on their next request.
**Tests.** `test_toggle_takes_effect_immediately`, `test_toggle_audited`, `test_core_feature_toggle_rejected`, `test_plan_change_warns_on_limit_breach`, `test_suspend_requires_reason`, `test_effective_value_matches_resolver` (parametrised over all 23 keys × 3 states).
**Edge cases.** Two operators toggling the same key concurrently — last write wins, both audited. Setting `INHERIT` must delete the override row, not store `INHERIT` and drift.

### TASK-115 — Impersonation ("enter tenant")
**Objective.** Support a client without asking for their password — and without being able to quietly move their money.
**Prerequisites.** TASK-114, TASK-092.
**Requirements.** All of 10 §N.10: read-only by default; write mode is a second, separately-audited toggle with a typed reason; never available for payment collection, refunds or deletes in either mode; a red banner on every page; audited on start and end with duration; hard 30-minute expiry; cannot change a password or create a user.
**Implementation.** `/console/tenants/<id>/enter/` writes a signed impersonation record into the session (`impersonator_id`, `tenant_id`, `mode`, `expires_at`), then redirects to the tenant host. `TenantSessionGuardMiddleware` (TASK-092) already allows platform staff on a tenant host only when this record validates. A `read_only` request flag makes every unsafe method fail with `ERR_IMPERSONATION_READ_ONLY`; an explicit denylist blocks payment/refund/delete endpoints even in write mode. The tenant's own `AuditLog` records the actor as the operator with `on_behalf_of`.
**DB.** `PlatformAuditLog` rows; `AuditLog.on_behalf_of` (nullable FK) or a `changes` key — prefer the explicit column. **Backend.** enter/leave views, middleware extension, the denylist. **Frontend.** `templates/partials/_impersonation_banner.html` in `base.html`. **API.** —
**Acceptance.** An operator enters A read-only, can browse every screen, and every POST fails; enabling write mode with a reason allows edits but still refuses `POST /api/payments/collect/`; the banner is present on all 27 templates; leaving restores the console session; the session expires after 30 minutes.
**Tests.** `test_read_only_blocks_writes`, `test_write_mode_requires_reason`, `test_money_endpoints_blocked_in_both_modes`, `test_banner_present`, `test_expiry`, `test_start_and_end_audited_with_duration`, `test_impersonation_cannot_change_password`, `test_tenant_audit_records_on_behalf_of`.
**Edge cases.** An operator impersonating while the tenant gets suspended. A forged session cookie — the record is signed and cross-checked against `PlatformAuditLog`. Two concurrent impersonations of different tenants in one browser — scope the record by host.

### TASK-116 — Per-tenant export, backup and delete
**Objective.** Give a leaving client their data, and be able to remove them cleanly.
**Prerequisites.** TASK-112.
**Requirements.** Export is complete (every tenant-owned table + media) and re-importable. Delete is two-step, typed-confirmation, export-first, and audited.
**Implementation.** `export_tenant --slug --out` producing a zip: `data.json` (a tenant-scoped `dumpdata` equivalent using `all_tenants.filter(tenant=…)`), `media/`, `manifest.json` (row counts, schema version, exported_at). `import_tenant` for restore into a *new* slug. Console: "export" queues the command and offers the file; "delete" requires typing the slug, confirms an export exists, sets `ARCHIVED` + `purge_after`, and a separate `purge_tenants` command does the actual deletion after the retention window.
**DB.** — **Backend.** three commands + console views. **Frontend.** `templates/console/tenant_export.html`, `_delete_modal.html`. **API.** —
**Acceptance.** Export → import into a fresh slug reproduces identical row counts per table and a working login; delete never runs synchronously from a click.
**Tests.** `test_export_covers_every_tenant_owned_model` (parametrised over the model registry, so a new model that is not exported fails), `test_roundtrip_row_counts`, `test_delete_requires_export`, `test_purge_respects_retention`.
**Edge cases.** A model added later and forgotten in the export — the parametrised test is what catches it. Large media archives — stream, do not buffer.

### TASK-117 — Console dashboard
**Objective.** The one screen you open every morning.
**Prerequisites.** TASK-112, TASK-110.
**Requirements.** Cross-tenant KPIs read from `TenantUsage`, not live counts. Expiring-in-30-days, over-quota and recently-suspended lists are actionable links.
**Implementation.** Tenants by status; total students/users across all clients; new clients this month; subscriptions expiring in 30 days; tenants over 90 % of any limit; the last 20 platform audit entries; a 30-day sparkline of total scans and total collections (from the nightly usage refresh, never a live aggregate).
**DB.** — **Backend.** dashboard view + aggregate helpers. **Frontend.** `templates/console/dashboard.html`. **API.** —
**Acceptance.** Renders in ≤ 5 queries with 100 tenants.
**Tests.** `test_dashboard_query_count`, `test_expiring_list_boundaries`, `test_over_quota_detection`.
**Edge cases.** Zero tenants (fresh install) — render an empty state that links to the provisioning wizard.

### TASK-118 — `PlatformAuditLog` viewer
**Objective.** Answer "who suspended this client, and why".
**Prerequisites.** TASK-111.
**Requirements.** Append-only; filterable by actor, tenant, action, date; every console mutation writes exactly one row; impersonation sessions are visible with their duration.
**Implementation.** Model (if not already added in TASK-089), a `platform_audit.record()` service mirroring `core.audit.record()`, and a list view reusing the existing audit-log template idiom.
**DB.** `tenancy_platformauditlog` + indexes on `(tenant, -created_at)`, `(actor, -created_at)`, `(action, -created_at)`. **Backend.** service + view. **Frontend.** `templates/console/audit.html`. **API.** —
**Acceptance.** Every console POST in the test suite produces exactly one row; the viewer filters correctly.
**Tests.** `test_every_console_mutation_audited` (parametrised over the console URLconf's POST routes), `test_filters`, `test_append_only`.
**Edge cases.** A failed mutation must not write a success row.

---

## Phase 17 — Hardening & Deployment

### TASK-119 — The cross-tenant leak suite
**Objective.** Prove isolation instead of asserting it, and make a future regression fail CI.
**Prerequisites.** all of Phase 14 and 15.
**Requirements.** Two fully-populated tenants with **identical** data (same student codes, same card numbers, same receipt numbers, same usernames) so any leak is unambiguous. Parametrised over the model registry and the URLconf, so new models and new URLs are covered automatically.
**Implementation.** A `two_tenants` fixture built from factory_boy. Then:
- **Model sweep** — for every model subclassing `TenantOwnedModel`: inside A's context, `Model.objects.count()` equals A's rows only, and `Model.objects.filter(pk=<B's pk>).exists()` is `False`.
- **URL sweep** — for every named URL taking a pk, authenticated as A's admin, request B's object's pk → assert **404** (never 200, never 403 — a 403 confirms existence).
- **Manager sweep** — every custom `QuerySet` method on every tenant-owned model, called in A's context, returns nothing of B's.
- **Scan test** — B's `qr_token` posted to A's scan endpoint returns the unknown-card result code.
- **Cache test** — a value cached under A must not be readable under B, for the settings registry, the feature resolver, the host cache and the rate limiter.
- **Coverage guard** — a test that fails if a concrete model in `LOCAL_APPS` is neither `TenantOwnedModel` nor on the exemption allowlist (complements the system check of TASK-093 by running in CI even if checks are silenced).
- **Grep guard** — `all_tenants` appears only under `apps/tenancy/`, `apps/console/` and `*/migrations/`.
**DB.** — **Backend.** — **Frontend.** — **API.** —
**Files.** `apps/tenancy/tests/{factories,test_isolation,test_url_isolation,test_cache_isolation,test_coverage_guard}.py`, CI gate in `.github/workflows/`.
**Acceptance.** The whole suite is green, and deliberately removing the tenant filter from `TenantManager` makes ≥ 20 tests fail (verify this by doing it once).
**Tests.** — (this task *is* the tests)
**Edge cases.** A model with no detail URL — skip explicitly with a reason, never silently.

### TASK-120 — Postgres row-level security (optional hardening)
**Objective.** A database-enforced backstop under the application-level filters.
**Prerequisites.** TASK-119 green, system live and stable.
**Requirements.** Optional and reversible by a settings flag. Must not break `CONN_MAX_AGE` connection reuse, migrations, or the console's cross-tenant reads.
**Implementation.** For every tenant-owned table: `ENABLE ROW LEVEL SECURITY` + a policy `tenant_id = current_setting('app.tenant_id', true)::bigint`. The app connects as a non-superuser role without `BYPASSRLS`; a separate console role has `BYPASSRLS`. `TenantResolutionMiddleware` issues `SET LOCAL app.tenant_id` inside the request's transaction — with `ATOMIC_REQUESTS = False` that means an explicit connection hook, so use a `connection_created` + per-request `SET` and an unconditional `RESET` in `finally`. Gate on `TENANCY_RLS_ENABLED`.
**DB.** RLS policies via `RunSQL` migrations (with reverse SQL). **Backend.** connection hook. **Frontend.** — **API.** —
**Acceptance.** With RLS on, deleting the application-level filter still yields zero cross-tenant rows; migrations and the console still work.
**Tests.** `test_rls_blocks_raw_sql_cross_tenant`, `test_rls_reset_between_requests`, `test_console_role_bypasses`, `test_rls_can_be_disabled`.
**Edge cases.** Connection pooling / pgbouncer in transaction mode. A `SET` that survives into the next request on a reused connection — this is the failure mode to test hardest.

### TASK-121 — Deployment: wildcard DNS, TLS, hosts, cookies, proxy
**Objective.** Make the subdomain topology of 10 §N.11 real.
**Prerequisites.** TASK-092.
**Requirements.** Wildcard cert for `*.yourapp.com`; on-demand TLS for custom domains gated by a Django endpoint; `SESSION_COOKIE_DOMAIN` **unset**; `CSRF_TRUSTED_ORIGINS` wildcarded; the proxy rejects unknown hosts.
**Implementation.** Caddyfile (recommended) with `on_demand_tls { ask http://web:8000/internal/tls-check }` — plus the nginx+certbot DNS-01 alternative documented for those who prefer it. `/internal/tls-check?domain=` → 200 only for a verified `Domain` whose tenant is operational; cached 300 s; rate-limited; reachable only from the proxy network. Settings: `ALLOWED_HOSTS=[".yourapp.com", CONSOLE_HOST]`, `CSRF_TRUSTED_ORIGINS=["https://*.yourapp.com"]`, `SESSION_COOKIE_DOMAIN` explicitly `None` with a comment saying why. Update `.env.example`, `docker-compose.prod.yml`, and 07 §L.
**DB.** — **Backend.** the tls-check view. **Frontend.** — **API.** `/internal/tls-check`.
**Files.** `Caddyfile`, `docker-compose.prod.yml`, `cms/settings/{base,prod}.py`, `apps/tenancy/views.py`, `.env.example`, `docs/07-deployment.md`.
**Acceptance.** Two subdomains and one custom domain all serve valid TLS; an unknown host gets no certificate and no response; a session cookie set on A is not sent to B (verify in a browser, not only in tests).
**Tests.** `test_tls_check_only_for_verified_domains`, `test_tls_check_rejects_suspended`, `test_tls_check_rate_limited`, `test_cookie_domain_unset`.
**Edge cases.** Certificate-issuance rate limits when someone enumerates hostnames — the `ask` endpoint is exactly the defence; make sure the negative answer is cached. Also update `cms/settings/pythonanywhere.py`'s docstring to state plainly that the free tier is single-tenant demo only.

### TASK-122 — Per-tenant backup, monitoring and the runbook
**Objective.** Operate N clients without N times the effort.
**Prerequisites.** TASK-116, TASK-121.
**Requirements.** A restore of one client must not require restoring everyone. Monitoring is per-tenant where it matters (errors, scan p95, size) and aggregate elsewhere.
**Implementation.** Nightly `pg_dump` stays whole-cluster (it is one DB), **plus** a nightly `export_tenant` per operational tenant so a single-client restore is a file, not a surgery. Sentry tags gain `tenant.slug`. The `scan` logger gains `tenant_id`. Metrics per tenant: scan p95, error rate, DB rows, media MB. Alerts: any tenant's scan p95 > 500 ms, any tenant over quota, any export failure. `docs/RUNBOOK.md` gains: provision a client, suspend for non-payment, restore one client, move a client to a new domain, roll back the tenancy migration, and what to do when the tenant contextvar leaks (symptoms + immediate mitigation).
**DB.** — **Backend.** logging/Sentry wiring + cron entries. **Frontend.** — **API.** —
**Files.** `docs/07-deployment.md`, `docs/RUNBOOK.md`, `cms/settings/prod.py`, compose cron service.
**Acceptance.** A single client is restored into a scratch environment from last night's per-tenant export, verified by row counts, without touching any other client.
**Tests.** `test_sentry_tag_present`, `test_scan_log_has_tenant`, plus a documented manual restore drill.
**Edge cases.** An export that grows past the disk — rotate and alert.

### TASK-123 — Documentation, go-live checklist, and the first two real clients
**Objective.** Close the loop: the docs describe what was built, and two paying clients run on it.
**Prerequisites.** TASK-119 → 122.
**Requirements.** 01–09 are amended where multi-tenancy changed them; the go-live checklist of 07 §L.8 gains its tenancy items; the first two clients are provisioned through the console, not the shell.
**Implementation.** Update: `docs/README.md` index (add 10 and 11), 02 §C (the `tenant` column and every rewritten constraint), 05 §G.2 (`ERR_FEATURE_DISABLED`, `ERR_QUOTA_EXCEEDED`, `ERR_IMPERSONATION_READ_ONLY`), 06 §I (tenant isolation as a threat class, with the leak suite as its control), 07 §L (topology, TLS, backups), 08 (phases 13–17 appended). New checklist items: wildcard cert valid and auto-renewing; `SESSION_COOKIE_DOMAIN` unset; Redis running (mandatory with >1 worker — 10 §N.13); the leak suite green in CI; `enforce_subscriptions` scheduled; a per-tenant export verified by restore; the existing center migrated and serving on its own subdomain.
**DB.** — **Backend.** — **Frontend.** — **API.** —
**Acceptance.** Two real clients are live on two subdomains with different plans and visibly different enabled features; the original center is one of them and lost nothing in the migration.
**Tests.** The full suite green; scan p95 measured against the pre-tenancy baseline and unchanged.
**Edge cases.** The first client to ask for a feature that is not in the catalogue — the answer is a new `FeatureSpec`, never a fork.

---

## Sequencing notes for the agent

- **089 → 093 can be done in one sitting** and change nothing user-visible. Merge them before starting Phase 14.
- **096 → 102 are independent of each other** given 093, and can be parallelised; 100 depends on 098 + 099 only for its tests.
- **103 is the only irreversible task.** Do not start it until 094–102 are deployed and stable, and never without a verified restore.
- **108 is the widest diff** (every view, every endpoint, the sidebar). Do it as one commit with the URL→feature table in the message, so the review is a table check rather than a code read.
- **119 should be written incrementally from Phase 14 onward**, not at the end. Each model task adds its own isolation test; TASK-119 then generalises them into the parametrised sweep.
- **Do not start Phase 16 before 119's model sweep is green.** A console that reads across tenants is exactly the thing that turns a filtering bug into a breach.

# 10 — Multi-Tenancy Architecture

> **N. One deployment, many centers.** This document turns the single-center system described in 01–09 into a SaaS that serves every client from one codebase, one database and one deploy, with a separate platform console that creates clients, suspends them, and switches individual features on and off per client.

It is written to be implemented by an agent working task-by-task from [11-tenancy-tasks.md](11-tenancy-tasks.md). Read this document first; it is the "why" that the task list assumes.

---

## N.1 Decisions

| # | Decision | Chosen | Rejected |
|---|----------|--------|----------|
| D1 | Isolation model | **Shared schema, row-level `tenant_id`** on every domain table, enforced by a context-var manager + middleware + a leak-test suite | Schema-per-tenant (`django-tenants`): Postgres-only, `migrate` runs N times, cross-client console stats need a loop over schemas. DB-per-tenant: strongest isolation but N migrations per release and no cross-tenant query at all. |
| D2 | Addressing | **Subdomain `<slug>.yourapp.com`, plus an optional custom domain per client**; the console lives on its own host | Path prefix `/t/<slug>/`: would touch every `{% url %}` and every hard-coded `/api/...` in the JS. |
| D3 | Console scope | **Lifecycle + plans + feature flags + limits + impersonation + per-client export.** No money handling. | Minimal on/off (too little — you cannot answer "why is this client slow / over quota"). Full SaaS billing (a second billing system on top of the student one). |

**D1 is reversible.** Because every row carries `tenant_id` from day one, moving a large client to their own database later is a router change plus a data copy — not a data-model change. Do not design anything that assumes a single database *connection*; design against `current_tenant()`.

### The four rules that make D1 safe

1. **A model is tenant-owned or explicitly not.** There is no third state, and a system check fails the build if a new model is neither.
2. **The default manager is tenant-filtered.** Reaching outside a tenant requires typing `all_tenants`, which is greppable and tested for.
3. **No tenant in context is an error, not an empty result.** `.none()` hides bugs in production; a raised `TenantContextRequired` fails loudly in tests.
4. **Every cache key, sequence, setting, file path and receipt number is namespaced by tenant.** A shared cache key is a data leak that no queryset filter will catch.

---

## N.2 Control-plane schema (`apps/tenancy`)

The control plane is *not* tenant-owned — it is the table of tenants themselves. It lives in a new app `apps/tenancy` and is readable only from the console and from middleware.

```text
Tenant                             the client (one tutoring center company)
├── id, slug, name, name_en
├── status         TRIAL | ACTIVE | PAST_DUE | SUSPENDED | ARCHIVED
├── plan           FK Plan (PROTECT)
├── trial_ends_at, expires_at, grace_until
├── locale defaults: timezone, language, currency
├── owner_email, owner_phone, notes
├── created_at, activated_at, suspended_at, suspended_reason
└── archived_at, purge_after

Domain                             how a request finds the tenant
├── tenant FK (CASCADE)
├── host        'elnour.yourapp.com'  |  'portal.elnour.com'
├── is_primary  bool  (exactly one per tenant — partial unique)
├── is_custom   bool  (drives TLS issuance)
└── verified_at

Plan                               a bundle of features + limits
├── name, slug, is_public, sort_order
├── features    M2M PlanFeature -> feature key      (what is ON by default)
├── max_students, max_users, max_groups, max_cards  (null = unlimited)
├── storage_mb, retention_days
└── price_note  (free text — you invoice outside the system)

TenantFeature                      per-client override of the plan
├── tenant FK, feature_key
├── state       INHERIT | ON | OFF
├── note, updated_by, updated_at
└── UNIQUE(tenant, feature_key)

TenantUsage                        denormalised counters for the console
├── tenant FK (OneToOne)
├── students, active_students, users, groups, cards, lessons_30d
├── payments_30d_total, storage_mb
└── computed_at        (refreshed nightly + on demand)

PlatformAuditLog                   what platform staff did to which tenant
├── actor (platform user), tenant, action, changes, reason, ip
└── created_at
```

`TenantUsage` exists so the console list page is one query over `N` tenants instead of `N × 6` counts. It is a cache, never a source of truth.

### Tenant status semantics

| Status | Can log in | Can write | What the user sees |
|--------|-----------|-----------|--------------------|
| `TRIAL` | yes | yes | a countdown banner from 7 days out |
| `ACTIVE` | yes | yes | nothing |
| `PAST_DUE` | yes | yes | a persistent warning banner; `grace_until` in the message |
| `SUSPENDED` | **no** | no | the gate page (HTTP 403) with the center's own contact line; login is blocked |
| `ARCHIVED` | no | no | gate page; data retained until `purge_after`, then a console-driven purge |

Suspension **never deletes or hides data**. It is a door, not a shredder. A suspended tenant that is resumed is byte-identical to what it was.

---

## N.3 Request lifecycle

```text
Host: elnour.yourapp.com
        │
   [1] SecurityMiddleware
   [2] SessionMiddleware
   [3] TenantResolutionMiddleware ──► cache.get("tenancy:host:elnour.yourapp.com")
        │                              miss → Domain.objects.select_related("tenant","tenant__plan")
        │                              unknown host → 404 (never a redirect: do not confirm what exists)
        │                              console host → request.tenant = None
        │                                             request.urlconf = "cms.urls_console"
        │                              sets the contextvar; resets it in finally
   [4] LocaleMiddleware               language default now comes from tenant.language
   [5] CommonMiddleware
   [6] CsrfViewMiddleware
   [7] AuthenticationMiddleware
   [8] TenantSessionGuardMiddleware ─► user.tenant_id != request.tenant.id → flush session, 403
        │                              platform staff on a tenant host → only via impersonation
   [9] MessageMiddleware
  [10] XFrameOptionsMiddleware
  [11] TenantStatusMiddleware ───────► SUSPENDED/ARCHIVED → gate page (exempt: logout, gate, healthz)
  [12] AuditContextMiddleware         now also stamps tenant on every AuditLog row
  [13] ForcePasswordChangeMiddleware
        │
        ▼  view — every ORM call is already tenant-filtered
```

Two things are load-bearing:

- **Order.** Tenant resolution must run *before* `AuthenticationMiddleware`, because the user lookup itself is tenant-scoped. `request.user` is lazy, so placing resolution at [3] is enough.
- **`finally`.** The contextvar is set with a token and reset in `finally`, exactly as `AuditContextMiddleware` clears its thread-local. Under a threaded WSGI worker a leaked contextvar means the next request on that thread serves another center's data. This is the single highest-severity bug class in the whole design; it gets its own test.

**The host lookup is cached (300 s).** Otherwise every request — including the hot scan path — gains a query. A `Domain` save invalidates its own key.

---

## N.4 Data isolation

```python
# apps/tenancy/context.py
_tenant: ContextVar[Tenant | None] = ContextVar("current_tenant", default=None)

def current_tenant() -> Tenant | None: ...
def require_tenant() -> Tenant:            # raises TenantContextRequired
def tenant_context(tenant):                # context manager, for console + commands
def all_tenants_context():                 # explicit, audited escape hatch
```

```python
# apps/tenancy/models.py
class TenantManager(models.Manager):
    def get_queryset(self):
        qs = super().get_queryset()
        tenant = current_tenant()
        if tenant is None:
            raise TenantContextRequired(f"{self.model.__name__}.objects used with no tenant")
        return qs.filter(tenant_id=tenant.pk)


class TenantOwnedModel(TimeStampedModel):
    tenant = models.ForeignKey("tenancy.Tenant", on_delete=models.PROTECT,
                               related_name="+", db_index=True, editable=False)

    objects = TenantManager()          # declared first → _default_manager
    all_tenants = models.Manager()     # explicit, greppable, tested

    class Meta:
        abstract = True
        base_manager_name = "all_tenants"
```

Why `base_manager_name = "all_tenants"`: related descriptors (`group.lessons`, `refresh_from_db()`, FK traversal) are built from the *base* manager. Leaving them tenant-filtered would raise `TenantContextRequired` inside management commands, and it buys nothing — `group` already belongs to exactly one tenant, so `group.lessons` cannot cross a boundary.

Why `objects` is declared **first**: `_default_manager` is what Django admin, `ModelChoiceField`, `get_object_or_404` and generic code reach for. Those must be filtered.

Why `on_delete=PROTECT`: deleting a tenant must be a deliberate console operation with an export first, never a cascade someone triggers from the Django admin.

### Defence in depth

| Layer | Catches | Task |
|-------|---------|------|
| 1. `TenantManager` on `_default_manager` | 95 % of reads, all 143 existing `objects.` call sites — with **no edit to those call sites** | T-093 |
| 2. `save()` guard: auto-stamp `tenant`, reject a mismatch | writes that would land in the wrong tenant | T-104 |
| 3. Cross-tenant FK guard in `clean()`/`save()` | `Lesson(group=<other tenant's group>)` | T-104 |
| 4. `TenantSessionGuardMiddleware` | a session cookie replayed on another host | T-092 |
| 5. Leak-test suite (two identical tenants, every model, every URL) | regressions, and any new model that forgets the base class | T-119 |
| 6. Postgres RLS (optional) | raw SQL, a future `.extra()`, a bug in layers 1–3 | T-120 |

Layers 1–5 are mandatory. Layer 6 is real hardening but costs connection-level `SET LOCAL` discipline under `CONN_MAX_AGE`; schedule it once the system is live and stable.

---

## N.5 Which models get `tenant`, and what happens to the constraints

**The rule:** a unique constraint rooted at a *scalar* column must gain `tenant`. A constraint whose leading field is a FK to a tenant-owned model is already tenant-scoped and must be left alone.

| Model | `tenant` | Constraint change |
|-------|:--------:|-------------------|
| `accounts.User` | nullable | `username` unique → `UNIQUE(tenant, username)` + partial `UNIQUE(username) WHERE tenant IS NULL` for platform staff |
| `core.Setting` | yes | PK was `key` → surrogate PK + `UNIQUE(tenant, key)` |
| `core.Sequence` | yes | PK was `key` → surrogate PK + `UNIQUE(tenant, key)` |
| `core.AuditLog` | nullable | none (nullable: platform actions have no tenant) |
| `academics.EducationalStage` | yes | `name`, `code` → `UNIQUE(tenant, …)` |
| `academics.Grade` | yes | `code` → `UNIQUE(tenant, code)`; `uq_grade_name_per_stage` **unchanged** |
| `academics.Subject` | yes | `name`, `code` → `UNIQUE(tenant, …)` |
| `academics.GradeSubject` | yes | `uq_offering_grade_subject` **unchanged** |
| `academics.Instructor` | yes | — |
| `academics.Group` | yes | `code` → `UNIQUE(tenant, code)`; `uq_group_name_per_offering` **unchanged** |
| `academics.GroupSchedule` | yes | `uq_schedule_slot` **unchanged** |
| `students.Student` | yes | `student_code` → `UNIQUE(tenant, student_code)` |
| `students.StudentGroupAssignment` | yes | `uq_active_assignment_per_offering` **unchanged** |
| `cards.StudentCard` | yes | `card_number` → `UNIQUE(tenant, card_number)`; **`qr_token` stays globally unique** |
| `cards.CardAssignment` | yes | both partial uniques **unchanged** |
| `lessons.Lesson` | yes | `uq_lesson_group_start` **unchanged** |
| `attendance.Attendance` | yes | `uq_attendance_lesson_student` **unchanged** |
| `attendance.AttendanceEvent` | yes | `idempotency_key` → `UNIQUE(tenant, idempotency_key)` — it is client-generated, two centers will collide |
| `payments.MonthlyCharge` | yes | `uq_charge_per_month` **unchanged** |
| `payments.Payment` | yes | `receipt_number` → `UNIQUE(tenant, receipt_number)` — receipts restart at 1 per center |

**`qr_token` deliberately stays globally unique.** It is a 64-char random token; global uniqueness is free and guarantees a scanned token can never resolve to two rows in two centers. The lookup in the scan path *still* filters by tenant — global uniqueness is a safety net, not the authorisation.

**`core.Setting` and `core.Sequence` are the sharpest edges in this migration.** Both use `key` as the primary key today, and both are read on hot paths. Changing a primary key means the `Setting` FK-free design is easy but the registry's cache key must become `core:settings:<tenant_id>:overrides` in the same commit — a shared cache key would serve one center's fees to another. See T-095.

---

## N.6 Users, roles and sessions

- `User.tenant` is **nullable**. `NULL` means platform staff (you and your team); everyone else belongs to exactly one tenant.
- A new field `User.is_platform_staff` gates the console. `is_superuser` is **not** reused for this: a center admin should never be a superuser, and a platform operator should not automatically get Django admin.
- `Role.SUPER_ADMIN` keeps its current meaning *inside a tenant* — it is the center's owner, not you. The console role is separate and lives outside the tenant's role table.
- Authentication needs a `TenantModelBackend`: `ModelBackend` calls `get_by_natural_key(username)`, which now returns multiple rows across tenants. The backend filters by `current_tenant()`, and on the console host filters to `tenant__isnull=True`.
- `createsuperuser` must still work with no tenant in context → it creates platform staff.
- **Cookies:** leave `SESSION_COOKIE_DOMAIN` unset. Each subdomain then gets its own cookie and a session simply cannot travel between centers. `CSRF_TRUSTED_ORIGINS` gains `https://*.yourapp.com` (Django supports the wildcard).
- `seed_roles` becomes per-tenant: the five `auth.Group` rows are created inside the tenant during provisioning. Django groups/permissions are global tables, so the group name is namespaced (`t<id>:CENTER_ADMIN`) — or, simpler and recommended, groups stay global (five rows, shared) and only the `User → Group` membership is per-user. Permissions describe *what a role can do*, which is identical for every center; the tenant boundary is enforced by the queryset, not by the permission. **Choose the shared-groups option** — it avoids `N × 5` groups and keeps `seed_roles` a one-time global command.

---

## N.7 Per-tenant configuration, sequences and files

| Thing | Today | Multi-tenant |
|-------|-------|--------------|
| `settings_registry` cache key | `core:settings:overrides` | `core:settings:<tenant_id>:overrides` |
| `settings_registry.get()` with no tenant | n/a | raises `TenantContextRequired` |
| Reading another tenant's settings | n/a | `with tenant_context(t): settings_registry.get(...)` — the only supported way |
| `sequences.next_number("student")` | global row | `(tenant, key)` row; codes restart at 1 per center |
| `branding` context processor | `center.name` from `Setting` | unchanged — it reads the registry, which is now tenant-aware for free |
| `MEDIA_ROOT` layout | `media/students/…` | `media/t/<tenant_id>/students/…` via a tenant-aware `upload_to` |
| PDF output | in-memory | unchanged; the branding block it renders is already registry-driven |
| Rate-limit keys (`core/ratelimit.py`) | `rl:<scope>:<ip>` | `rl:<tenant_id>:<scope>:<ip>` — one center's traffic must not throttle another |

The `branding` context processor needing **no change** is the payoff of the existing policy-registry design. Keep that property: anything center-specific should be a `PolicySpec`, not a Django setting.

---

## N.8 Feature flags

Two orthogonal gates now guard every screen: **permission** (what this *user* may do — unchanged) and **feature** (what this *tenant* bought). Both must pass. Never conflate them by editing the permission matrix per tenant.

```python
# apps/tenancy/features.py   — mirrors the shape of core/policies.py deliberately
@dataclass(frozen=True)
class FeatureSpec:
    key: str
    label: str            # Arabic
    group: str            # for laying the console screen out
    default: bool         # when the plan says nothing
    depends_on: tuple = ()
    is_core: bool = False # cannot be switched off (attendance, students)
```

### Starting catalogue

| Group | Key | Default | Notes |
|-------|-----|:-------:|-------|
| core | `students` | on | `is_core` — cannot be disabled |
| core | `academics` | on | `is_core` |
| core | `attendance.scan` | on | `is_core` |
| attendance | `attendance.camera_scanner` | on | the `html5-qrcode` fallback path |
| attendance | `attendance.exceptional` | on | approve-exceptional flow |
| attendance | `attendance.offline_queue` | off | 05 §H.2 |
| cards | `cards` | on | disabling hides card management; attendance falls back to manual |
| cards | `cards.bulk_import` | on | |
| payments | `payments` | on | disabling hides the whole money side — **data is retained** |
| payments | `payments.refunds` | on | `depends_on=("payments",)` |
| payments | `payments.waivers` | on | `depends_on=("payments",)` |
| payments | `payments.receipt_pdf` | on | `depends_on=("payments",)` |
| payments | `payments.enforce_on_attendance` | off | the coupling policy of 04 §F |
| reports | `reports.operational` | on | |
| reports | `reports.financial` | on | `depends_on=("payments",)` |
| reports | `reports.export_excel` | on | |
| reports | `reports.export_pdf` | on | |
| ui | `ui.theme_picker` | on | |
| ui | `i18n.english` | on | off → the topbar language switch disappears, `ar` only |
| admin | `audit.viewer` | on | the center's own audit screen |
| admin | `settings.editor` | on | off → policies are frozen at whatever you provisioned |
| admin | `users.management` | on | off → you create the center's staff accounts for them |

### Resolution order

```text
TenantFeature.state == ON            → True    (explicit per-client override)
TenantFeature.state == OFF           → False
TenantFeature absent / INHERIT       → key in tenant.plan.features
plan says nothing                    → FeatureSpec.default
any depends_on resolves False        → False   (transitively)
FeatureSpec.is_core                  → always True
```

Resolved once per request into a `frozenset`, cached under `tenancy:features:<tenant_id>` (300 s), invalidated on any `TenantFeature`/`Plan` save. **The scan path must not gain a query** — this cache is what guarantees it.

### Enforcement — four layers, all required

1. **Page views:** `@require_feature("payments")` → **404**, not 403. A center that did not buy payments should not learn the URL exists.
2. **AJAX:** `@ajax(methods=("POST",), perm="payments.add_payment", feature="payments")` → `ERR_FEATURE_DISABLED`, status 404. One extra kwarg on the existing decorator, so every endpoint is one line away from being gated.
3. **Templates:** `{% load features %}{% if_feature "payments" %}…{% endif_feature %}`, plus a `FEATURES` frozenset in the context processor so `_sidebar.html` hides nav entries. The sidebar already gates on `perms.*`; feature checks sit alongside — `{% if perms.payments.view_monthlycharge and "payments" in FEATURES %}`.
4. **Services:** the guard repeats in `services.py`, so a management command, a Celery beat job or a future import script cannot bypass a disabled feature.

**Disabling a feature must never destroy data.** Turning `payments` off hides the screens and blocks the endpoints; the ledger, the charges and the receipts stay exactly where they are. Turning it back on restores the center to the state it was in. Write that as a test.

---

## N.9 Plan limits

`max_students`, `max_users`, `max_groups`, `max_cards`, `storage_mb`; `NULL` means unlimited.

Enforced in the **creation services only** (`students.services.create_student`, `accounts.services.create_user`, group creation, card import), never in a signal — a signal fires during the data migration and during restores, where the limit is irrelevant.

```python
quota.check(tenant, "students")     # raises DomainError("ERR_QUOTA_EXCEEDED", …)
```

Counts come from `TenantUsage` when fresh (60 s) and from a real `COUNT(*)` otherwise. The error message is Arabic and names the limit and the plan; it does **not** tell the center admin to "upgrade" — it tells them to contact the operator, because you sell offline.

Card import is the one bulk path: it must check the quota **before** importing, report how many of N would fit, and import nothing rather than a partial batch.

---

## N.10 The platform console (`apps/console`)

Lives on its own host (`admin.yourapp.com`) with its **own URLconf** (`cms/urls_console.py`), swapped in by the resolution middleware. This is the key isolation property: the tenant URLconf physically does not contain a console route, so no path traversal, permission bug or misconfigured proxy can reach it from a tenant host.

```text
/                       dashboard — tenants by status, expiring in 30 days, cross-client KPIs
/tenants/               list + filters (status, plan, expiring, over quota) + search
/tenants/new/           provisioning wizard
/tenants/<id>/          detail: identity, plan, domains, features, limits, usage, timeline
/tenants/<id>/features/ per-feature tri-state toggles, grouped, with the plan default shown
/tenants/<id>/suspend/  suspend / resume / archive, reason required
/tenants/<id>/enter/    impersonation
/tenants/<id>/export/   full data export (JSON + media archive)
/plans/                 plan CRUD
/audit/                 PlatformAuditLog
/health/                aggregate: per-tenant error rate, scan latency, DB size
```

### Provisioning (the wizard, T-113)

One transaction, one command (`manage.py provision_tenant --slug …`), one screen — the wizard is a form over the command so both paths stay identical:

```text
1. Tenant row + primary Domain (<slug>.yourapp.com)
2. Plan assignment, trial_ends_at
3. seed_settings   inside tenant_context()  → policy overrides for this center
4. seed_academics  optional --demo, or the Egyptian stage/grade defaults
5. The owner user  (CENTER_ADMIN, must_change_password=True), one-time password shown once
6. TenantUsage row
7. PlatformAuditLog entry
```

Idempotent and transactional: a failure at step 5 leaves no half-built tenant.

### Impersonation (T-115)

Non-negotiable requirements, because this is the feature that costs you a client's trust if it is loose:

- Read-only by default. Writing requires a second, separately-audited toggle and a typed reason.
- Never available for a payment-collecting or delete endpoint, in either mode.
- A red banner on **every** page, naming the operator and offering "leave".
- `PlatformAuditLog` on start and on end, with duration.
- Hard expiry (30 min), and the impersonated session cannot change a password or create a user.
- The tenant's own `AuditLog` also records the action as performed *by the operator on behalf of*, so the center's audit trail never silently attributes your action to their staff.

---

## N.11 Deployment

```text
                *.yourapp.com  A → <ip>          (wildcard)
                admin.yourapp.com A → <ip>
                portal.elnour.com CNAME → yourapp.com   (per custom domain)
                              │
                    ┌─────────▼──────────┐
                    │  Caddy (or nginx)  │  wildcard cert via DNS-01 for *.yourapp.com
                    │                    │  on-demand TLS for custom domains, gated by
                    │                    │  ask → /internal/tls-check?domain=…
                    └─────────┬──────────┘
                              │ Host header preserved
                    ┌─────────▼──────────┐
                    │  gunicorn / Django │
                    └────────────────────┘
```

- **Wildcard TLS** needs DNS-01, so certbot needs a DNS-provider plugin — or use **Caddy**, whose on-demand TLS solves the custom-domain case cleanly: it asks a Django endpoint whether a hostname is a known, active `Domain` before issuing. That endpoint is 4 lines and must be rate-limited and cached.
- **`ALLOWED_HOSTS`**: `[".yourapp.com"]` covers every subdomain. Custom domains cannot go in a static list; validate them in `TenantResolutionMiddleware` against the cached `Domain` table and let the proxy reject anything it does not recognise. Do **not** set `ALLOWED_HOSTS = ["*"]` unless the proxy enforces the allowlist.
- **`SESSION_COOKIE_DOMAIN` stays unset.** Setting it to `.yourapp.com` would share one session cookie across every center — the worst possible bug in this system.
- **HSTS**: `includeSubDomains` is correct here, but it commits every future subdomain to HTTPS for a year. Confirm the wildcard cert renews before enabling preload.
- **PythonAnywhere free tier cannot host this.** No wildcard subdomain, no custom domain, no Postgres. `cms/settings/pythonanywhere.py` stays as-is and keeps serving a single-tenant demo — the code runs there with one tenant whose primary domain is `<user>.pythonanywhere.com`. Say so in its docstring rather than letting someone discover it during a deploy.
- **Postgres is now mandatory in production**, not merely recommended: partial unique indexes carry several of the tenant-scoped constraints, and MySQL/SQLite would silently allow duplicates across centers.

---

## N.12 Migrating the existing installation

The current `db.sqlite3` becomes tenant #1. Additive-first, in three releases, matching the migration discipline already set in 07 §L.7 — **no release both stops writing a column and drops it.**

```text
Release A   add `tenant` FK nullable everywhere; add the new tenancy tables;
            keep every old unique constraint. Deploys with zero behaviour change.

Release B   data migration: create Tenant(slug="default") from the current
            center.name setting, its primary Domain from the current host,
            backfill tenant_id on all 20 tables, bind every existing user.
            Then: tenant → NOT NULL, swap the unique constraints, add the
            composite indexes. Verified by a row-count assertion per table.

Release C   turn on the middleware, the managers and the console.
            (Rolling back C is a settings change, not a migration.)
```

Release B is the only irreversible step. It runs after a verified backup **and a verified restore** — the same rule 07 §L.5 already states.

---

## N.13 Performance

The scan path budget (≤ 6 queries, p95 < 120 ms on LAN, principle 6) survives:

| Addition | Cost | Mitigation |
|----------|------|------------|
| Host → tenant lookup | +1 query/request | cached 300 s by host; 0 queries steady-state |
| Feature resolution | +1–2 queries | cached 300 s per tenant as one frozenset |
| `tenant_id` in every WHERE | ~0 | it becomes the **leading column** of every hot composite index |
| Per-tenant setting cache | 0 | same registry, namespaced key |

Index rewrites: every composite index in 02 §C gains `tenant_id` as its first column — `(tenant_id, qr_token)`, `(tenant_id, lesson_id, student_id)`, `(tenant_id, billing_month, status)`. A trailing `tenant_id` would be useless; the position matters.

Because the cache is now the mechanism that keeps two centers apart *and* keeps the scan path fast, **Redis becomes mandatory the moment there is more than one gunicorn worker.** LocMemCache is per-process: with 4 workers, a feature you just disabled stays on for up to 300 s in 3 of them.

---

## N.14 Explicitly out of scope

Named so the agent does not invent them:

- Self-serve signup, trials-by-credit-card, dunning, invoices for your own service. You sell and invoice offline; the console only records `expires_at`.
- Per-tenant custom code, custom templates or per-tenant CSS beyond the existing theme/accent policies.
- Cross-tenant reporting for center owners. Only platform staff ever see more than one center.
- Moving a tenant to a dedicated database. The design allows it; nothing implements it yet.
- Tenant-scoped Django admin. `/admin/` stays platform-only and is not part of the product.

---

## N.15 Definition of done

1. Two centers, two subdomains, identical data, and no request in either can observe a single row of the other — proven by the parametrised leak suite over every model and every URL (T-119).
2. A new model that forgets `TenantOwnedModel` fails `manage.py check` (T-093).
3. Disabling `payments` for center A hides every payment screen and endpoint for A, changes nothing for B, and destroys no data — restorable by re-enabling.
4. Suspending a center blocks login within one request and leaves the database untouched.
5. Provisioning a new center is one console form, under a minute, with no shell access.
6. Scan p95 is unchanged against the pre-tenancy baseline.
7. `git grep all_tenants` returns hits only inside `apps/tenancy`, `apps/console` and migrations — enforced by a test.

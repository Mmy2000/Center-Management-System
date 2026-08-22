# 07 — Deployment Architecture

## L.1 Topology

A single-center system with ~2k students does **not** need Kubernetes, autoscaling, or a managed cluster. One well-configured VPS (or an on-premise mini-PC in the center, which is often the right answer in Egypt given connectivity), plus off-site backups.

```text
                   Internet / center LAN
                            │  443
                    ┌───────▼────────┐
                    │     nginx      │  TLS (Let's Encrypt), gzip/brotli,
                    │  reverse proxy │  static+media, rate limit, real-IP
                    └───┬────────┬───┘
                        │        │ /static/, /media/  (served from disk)
              unix socket│
                    ┌───▼──────────────┐
                    │ gunicorn (Django)│  3–5 sync workers, threads=2,
                    │  app:cms.wsgi    │  timeout 30, max-requests 1000
                    └───┬───────────┬──┘
                        │           │
              ┌─────────▼──┐   ┌────▼──────┐
              │ PostgreSQL │   │   Redis   │  cache + sessions + ratelimit
              │     16     │   │     7     │  (+ Celery broker, Phase 9+)
              └─────┬──────┘   └────┬──────┘
                    │               │
               nightly pg_dump  ┌───▼─────────────────┐
               + WAL archive    │ celery worker + beat│  charge generation,
                    │           └─────────────────────┘  exports, nightly recalcs
              ┌─────▼──────┐
              │ off-site   │  encrypted, 30 daily + 12 monthly
              │  backups   │
              └────────────┘
```

Sizing: 2 vCPU / 4 GB RAM / 40 GB SSD handles this load with a wide margin. Postgres `shared_buffers=1GB`, `work_mem=16MB`, `max_connections=50`.

## L.2 Containers (`docker-compose.prod.yml`)

| Service | Image | Notes |
|---|---|---|
| `web` | app image (python:3.13-slim, non-root user) | `gunicorn cms.wsgi:application --workers 4 --threads 2 --timeout 30 --max-requests 1000 --max-requests-jitter 100 --bind unix:/run/gunicorn.sock` |
| `nginx` | nginx:alpine | TLS termination, static/media, `client_max_body_size 5m`, `limit_req_zone` |
| `db` | postgres:16-alpine | named volume, `POSTGRES_*` from `.env`, healthcheck |
| `redis` | redis:7-alpine | `--maxmemory 256mb --maxmemory-policy allkeys-lru`, `appendonly no` (cache only) |
| `worker` / `beat` | app image | Celery; **only from Phase 9** |
| `backup` | postgres:16-alpine + cron | `pg_dump -Fc` nightly → encrypted → rclone to off-site |

Image build: multi-stage, wheels compiled in the builder, `collectstatic` at build time, `HEALTHCHECK` hitting `/healthz/`. Migrations run as an explicit deploy step (`docker compose run --rm web python manage.py migrate`), never automatically at container start — an unattended migration on a crash-loop is how data gets hurt.

## L.3 Settings layout

```text
cms/settings/
├── base.py     shared; reads env via django-environ
├── dev.py      DEBUG=True, SQLite or local Postgres, console mail, django-debug-toolbar
├── prod.py     DEBUG=False (asserted), Postgres, Redis cache+sessions, security headers, Sentry
├── pythonanywhere.py  free-tier host: standalone, no base import, no env (§L.9)
└── test.py     fast hashers, in-memory locmem cache, Postgres for CI
```
Environment variables: `DJANGO_SETTINGS_MODULE`, `SECRET_KEY`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `DATABASE_URL`, `REDIS_URL`, `SENTRY_DSN`, `TIME_ZONE=Africa/Cairo`, `LANGUAGE_CODE=ar`, `MEDIA_ROOT`, `BACKUP_*`. `.env` is never committed; a committed `.env.example` documents every key.

The current `cms/settings.py` (dev key, SQLite, `TIME_ZONE=UTC`, `ALLOWED_HOSTS=[]`) is replaced by this package in TASK-001/TASK-003.

## L.4 TLS, domains, offline-first reality

- Public deployment: Let's Encrypt via certbot in the nginx container (`--webroot`), auto-renew twice daily.
- On-premise deployment: the center's PCs reach the box over LAN by hostname; use a self-signed cert installed once on each machine, **or** run HTTP on a trusted LAN with the caveat documented — camera scanning requires a secure context, so HTTPS is mandatory if camera mode is used (HID scanners work over plain HTTP).
- The scanner must keep working when the center's internet dies: an on-premise box makes this free; a cloud box makes offline queueing (05 §H.2) load-bearing. **Recommend on-premise for centers with unreliable internet, cloud for the rest** — the application is identical.

## L.5 Backups & recovery

| What | How | Retention | Verification |
|---|---|---|---|
| Database | nightly `pg_dump -Fc` + WAL archiving for PITR | 30 daily, 12 monthly | weekly automated **restore into a scratch DB** + row-count check — a backup that has never been restored is not a backup |
| Media (photos) | nightly rsync/rclone to off-site | 30 days | monthly spot check |
| Config/secrets | `.env` in a password manager, not in backups | — | documented |

Documented RPO 24 h (RTO with WAL: ~15 min), RTO 1 h. A `docs/RUNBOOK.md` written in Phase 12 covers: restore, rotate secrets, roll back a release, reset a user's password, re-run charge generation, and recover from a bad migration.

## L.6 Monitoring, logging, alerting

- **Fonts**: `static/fonts/` ships the Cairo web font (UI) and Amiri (PDF), both SIL OFL. Nothing is fetched from Google at runtime — the app works offline and under a strict CSP.
- **Errors**: Sentry (`SENTRY_DSN`), with `send_default_pii=False` and a `before_send` scrubbing `qr_token`, phones and addresses.
- **Logs**: JSON to stdout → Docker `json-file` (rotated, 10 MB × 5) or Loki. One `scan` logger records `result_code`, `lesson_id`, `student_id`, `device_id`, `latency_ms` — **never the token**.
- **Health**: `/healthz/` (process), `/readyz/` (DB + Redis round trip) polled by Uptime Kuma / Healthchecks.io.
- **Metrics that matter**: scan p95 latency, scan error-rate by code, failed logins, charge-generation results, backup success, disk free. Alerts (email/Telegram) on: 5xx spike, scan p95 > 500 ms for 5 min, backup failure, disk > 85%, DB connections > 80%.
- **Business heartbeat**: a daily 22:00 summary mail to the owner (attendance %, collections, outstanding) — doubles as a "the system is alive" signal.

## L.7 CI/CD

GitHub Actions:
```text
on PR      → ruff · black --check · makemigrations --check · pytest (Postgres service) · coverage gates · pip-audit
on main    → build image · push to registry · deploy over SSH:
             docker compose pull
             docker compose run --rm web python manage.py migrate --noinput
             docker compose up -d --no-deps web worker
             docker compose exec nginx nginx -s reload
             smoke test: /healthz/, /readyz/, login page 200
             rollback = redeploy previous tag (migrations are additive; destructive
                        migrations are split across two releases, never one)
```
Migration discipline: additive-first (add column nullable → backfill → switch code → drop in a later release). No migration in this project may drop a column carrying financial or attendance data in the same release that stops writing it.

## L.8 Go-live checklist

1. `DEBUG=False`, fresh `SECRET_KEY`, `ALLOWED_HOSTS`/`CSRF_TRUSTED_ORIGINS` set, `manage.py check --deploy` clean.
2. Postgres, not SQLite. Verified `TIME_ZONE=Africa/Cairo` and that a lesson generated across the DST switch has correct local times.
3. `seed_roles`, `seed_settings` run; real admin created; every default password changed; demo data **absent**.
4. Card batch imported; 10 cards physically test-scanned end-to-end on the real hardware.
5. One real group loaded with real students; one lesson run live in parallel with paper as a fallback.
6. Backup taken **and restored** once before real data exists.
7. Staff trained on: enrollment, scanning, collecting payment, correcting a mistake (with reason), lost-card replacement.

## L.9 PythonAnywhere (free tier)

A free account has no PostgreSQL, no Redis and no nginx, so `prod.py` refuses to
start on it by design. `cms.settings.pythonanywhere` is the subset that runs
there: SQLite, the local-memory cache, PythonAnywhere's own static mapping.
Good enough for one center working on one screen; §L.1 still applies the moment
a scanning station and a cashier work at the same time.

**It is one standalone file: no `.env`, no environment variables, and no
import from `base.py`.** The web worker inherits nothing from your Bash console
— not your exports, not virtualenvwrapper's — which is the single most common
way a deploy here fails. So every value is written in the module itself, the way
Django's own generated `settings.py` does it. Editing two lines at the top is
the whole configuration:

```python
USERNAME = "Mmy"        # site is <USERNAME>.pythonanywhere.com
FORCE_HTTPS = False     # see below
```

`ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` are derived from `USERNAME`, so the
blank 400 and the CSRF failure cannot happen from a typo in two places.

Standing alone costs one thing: it can fall behind `base.py` — a new app, a new
middleware, a new context processor. `apps/core/tests/test_settings_pythonanywhere.py`
compares the 34 shared settings and fails the build when the two disagree, so
the copy cannot rot silently. Anything that changes in `base.py` goes here too.

The one thing the module cannot fix for you: **the virtualenv Python must be
3.12+ and must match the Web tab dropdown.** Django 6.1 does not run on 3.10,
and PythonAnywhere still offers older interpreters by default — so the module
refuses to start on one, with the command to rebuild it.

`SECRET_KEY` ships in the file, which means it is in your repository and anyone
who can read the code can forge a session cookie. Replace it once after the
first deploy (the comment above it has the command); changing it only signs
everyone out.

### Steps

```bash
# Bash console
git clone <repo> ~/Center_Management_System && cd ~/Center_Management_System
mkvirtualenv --python=/usr/bin/python3.13 cms      # Django 6.1 needs 3.12+
pip install -r requirements/pythonanywhere.txt
python -c "import django; print(django.__version__)"   # must print 6.1

# Edit USERNAME at the top of cms/settings/pythonanywhere.py, then:
export DJANGO_SETTINGS_MODULE=cms.settings.pythonanywhere   # this console only
python manage.py migrate
python manage.py seed_roles && python manage.py seed_settings
python manage.py createsuperuser
python manage.py collectstatic --noinput
python manage.py extract_messages -l en --compile   # only if locale/en/…/django.mo is missing
python manage.py check --deploy
```

`check --deploy` reports four HTTPS warnings while `FORCE_HTTPS = False`; they
clear when you turn it on, except the HSTS one, which is off on purpose — HSTS
is a year-long promise, not something to make on a free subdomain.

**Web tab** → *Add a new web app* → *Manual configuration* (not the Django
wizard: it writes its own project).

- **Virtualenv**: `/home/<username>/.virtualenvs/cms`
- **Source code**: `/home/<username>/Center_Management_System`
- **WSGI configuration file** — replace its contents with:

```python
import os
import sys

path = "/home/<username>/Center_Management_System"
if path not in sys.path:
    sys.path.insert(0, path)

os.environ["DJANGO_SETTINGS_MODULE"] = "cms.settings.pythonanywhere"

from django.core.wsgi import get_wsgi_application
application = get_wsgi_application()
```

- **Static files**: `/static/` → `/home/<username>/Center_Management_System/staticfiles`
  and `/media/` → `/home/<username>/Center_Management_System/media`
- **Reload** the web app. Errors land in the *Error log* on the same tab.

Turn on *Force HTTPS* in the Web tab, then set `FORCE_HTTPS = True` in
`cms/settings/pythonanywhere.py` and reload. In that order — the flag alone,
without the toggle, loops the browser.

### What the free tier costs you

| Limit | Effect |
|---|---|
| SQLite on a network filesystem | One writer at a time; the scan and payment paths are short transactions, so a single busy screen is fine, two are not. MySQL is *not* an alternative: it ignores partial unique indexes, and the "one active card per student" and "one attendance per lesson" guarantees are partial unique indexes. |
| Per-process local-memory cache | Settings changed in the UI reach the worker that served the request. On free there is effectively one worker, so this is invisible — until it isn't (§L.1 uses Redis). |
| No always-on tasks | Nightly backups and any scheduled generation must be a *Scheduled task* (one per day on free) or run by hand. |
| Outbound internet whitelisted | Irrelevant here — nothing in the app calls out. Fonts, Bootstrap and the QR library are all bundled. |
| App sleeps after ~3 months idle | Click the button in the Web tab to keep it alive. |

Card sheets, receipts and reports are all generated in-process with ReportLab —
no Chromium, so PDF export works on the free tier exactly as it does locally.

### When it fails

| What you see | What it is |
|---|---|
| `Bad Request (400)`, blank page | `USERNAME` does not match the host you typed, or the WSGI file is on the wrong settings module. `DEBUG=False` gives no other clue; the error log says `Invalid HTTP_HOST header`. |
| `SECRET_KEY setting must not be empty` | The WSGI file is not pointing at `cms.settings.pythonanywhere` — it fell back to `base`/`dev`, which read the environment the worker does not have. |
| `ImproperlyConfigured: … Python 3.12+` | The virtualenv was built with an older interpreter. Rebuild it and change the Web tab's Python version to match. |
| `ModuleNotFoundError: cms` | The WSGI file never added the project directory to `sys.path`, or added the wrong one. |
| Pages load unstyled | `collectstatic` was not run, or the `/static/` mapping does not point at `staticfiles/`. |
| Endless redirect | `FORCE_HTTPS = True` in the module without *Force HTTPS* enabled in the Web tab. |
| `pip install -r requirements.txt` reads as garbage | The frozen file was written by PowerShell as UTF-16. Use `requirements/pythonanywhere.txt`. |

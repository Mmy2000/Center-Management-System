# 12 — Deploying on PythonAnywhere (free tier)

## What the free tier can and cannot do

Read this before the steps, because one of these decides the shape of the whole deployment.

| | Free tier | What that means here |
|---|---|---|
| Hostnames | exactly one: `<username>.pythonanywhere.com` | **one hostname = one center.** There is no second host to route a second client to |
| Wildcard subdomains | no | `<slug>.yourapp.com` routing is impossible |
| Custom domains | no (paid only) | a client cannot bring `portal.theircenter.com` |
| PostgreSQL | no | SQLite, which the schema already supports (07 §L.9) |
| Redis | no | LocMemCache — fine at one worker, and there is exactly one |
| Always-on tasks / cron | no (paid only) | `enforce_subscriptions` and `purge_tenants` are run by hand |
| HTTPS | yes, on the free subdomain | `FORCE_HTTPS` in the settings file |

**So: the free tier hosts one center well, and cannot host a multi-tenant platform at all.** That is not a limitation to code around — it is arithmetic. Multi-tenant routing needs one hostname per client, and you have one hostname.

What you *do* get is the whole product for that one center, plus the console to administer it, which is enough to demo the platform and to run a first real client.

## The console on one hostname

The console normally lives on its own hostname (`admin.yourapp.com`) with its own URLconf, so a client's site has no route to it at all. With one hostname there is no second host, so `cms/settings/pythonanywhere.py` sets:

```python
CONSOLE_HOST = ""              # no hostname to give it
CONSOLE_PATH_PREFIX = "platform"   # so it is served from /platform/ instead
```

**Be clear about what that costs.** The console and the center now share an origin, so the structural wall is gone; `platform_staff_required` is the only one left. Concretely:

- a center's staff **cannot** read a single row of console data — the tenant-scoped user manager cannot even see them there, so they arrive anonymous and meet a sign-in their credentials do not open
- they **can** tell the console exists, because they get that login page rather than a flat 404

That is the trade. It is fine for a single center you run yourself. It is not what you would ship for a platform holding other people's data — for that, use the topology in [10 §N.11](10-multi-tenancy.md): a VPS, a wildcard certificate, and the console on its own host. Leave `CONSOLE_PATH_PREFIX` empty there.

---

## Steps

### 1. Set your username

Two lines at the top of `cms/settings/pythonanywhere.py`:

```python
USERNAME = "Mmy"        # your PythonAnywhere account name
FORCE_HTTPS = False     # flip to True *after* step 7
```

Everything else — `ALLOWED_HOSTS`, the CSRF origin, `TENANT_BASE_DOMAIN` — is derived from that one name, lowercased, so they cannot disagree.

### 2. Upload the code

In a PythonAnywhere **Bash console**:

```bash
git clone https://github.com/<you>/<repo>.git ~/cms
cd ~/cms
```

(Or upload a zip through **Files** and unpack it.)

### 3. Virtualenv and dependencies

```bash
mkvirtualenv --python=/usr/bin/python3.13 cms
pip install -r requirements/pythonanywhere.txt
```

If 3.13 is not offered on your account, use the newest Python the **Web** tab lists — Django 6.1 needs 3.12 or later.

### 4. A real secret key

The key committed in that file is public by definition. Generate one and paste it in:

```bash
python -c "from django.core.management.utils import get_random_secret_key as k; print(k())"
```

### 5. Migrate and set the site up

```bash
export DJANGO_SETTINGS_MODULE=cms.settings.pythonanywhere
export DEFAULT_TENANT_HOST=<username>.pythonanywhere.com   # only if importing existing data

python manage.py migrate
python manage.py collectstatic --noinput
python manage.py setup_single_host --host <username>.pythonanywhere.com --name "اسم السنتر"
```

`setup_single_host` seeds the plans, points the hostname at the single center — creating it if the database is empty, adopting it if you brought data — and asks for a console operator password. It refuses to run on a database holding more than one client, because pointing one hostname at one of them would silently strand the rest.

**Copy the center admin password it prints.** It is shown once and stored nowhere.

### 6. The Web tab

Create a web app: **Manual configuration**, matching Python version. Then set:

| Field | Value |
|---|---|
| Source code | `/home/<username>/cms` |
| Working directory | `/home/<username>/cms` |
| Virtualenv | `/home/<username>/.virtualenvs/cms` |
| WSGI file | edit it (below) |

Replace the WSGI file's contents entirely:

```python
import os
import sys

path = "/home/<username>/cms"
if path not in sys.path:
    sys.path.insert(0, path)

os.environ["DJANGO_SETTINGS_MODULE"] = "cms.settings.pythonanywhere"

from django.core.wsgi import get_wsgi_application
application = get_wsgi_application()
```

Static file mappings, both needed:

| URL | Directory |
|---|---|
| `/static/` | `/home/<username>/cms/staticfiles` |
| `/media/` | `/home/<username>/cms/media` |

Press **Reload**.

### 7. HTTPS

Turn on **Force HTTPS** in the Web tab **first**, then set `FORCE_HTTPS = True` in the settings file and reload. The other order loops the browser: Django redirects to HTTPS, PythonAnywhere serves HTTP, repeat.

### 8. Sign in

| | URL |
|---|---|
| The center | `https://<username>.pythonanywhere.com/accounts/login/` |
| The console | `https://<username>.pythonanywhere.com/platform/login/` |

---

## Running the scheduled work by hand

The free tier has no cron, and two commands are meant to run daily. Neither is urgent to the hour, but both matter over weeks:

```bash
python manage.py enforce_subscriptions   # trial/subscription expiry -> PAST_DUE -> SUSPENDED
python manage.py purge_tenants           # deletes archived clients past their retention window
```

`enforce_subscriptions` is idempotent, so running it whenever you happen to open a console is safe and catching up several days at once behaves correctly.

## Check these after the first reload

Three things the test suite cannot cover from a laptop, so give them a minute
on the real host:

- **A PDF receipt.** Collect a payment and download it. ReportLab renders the
  Arabic through a bundled font; a missing font file shows up here and nowhere
  else.
- **An Excel export.** `openpyxl` is installed only on deploy targets, so this
  path never runs locally — the code degrades gracefully if it is missing, but
  "gracefully" still means no file.
- **A card scan end-to-end** on the real hardware, over HTTPS. The camera
  scanner needs a secure context; a barcode reader does not.

## Backups

`db.sqlite3` is the whole database. Download it from **Files**, or:

```bash
python manage.py export_tenant --slug center --out ~/backup-$(date +%F).zip
```

That zip holds the data *and* the media, and imports into a fresh install — which is also how you would move off PythonAnywhere later.

The free tier has no automated backup and no point-in-time recovery. Take one before anything you would not want to redo.

## When you outgrow it

The moment there is a **second client**, this tier is finished — not degraded, finished, because there is no second hostname to give them. What that upgrade looks like:

1. A VPS (2 vCPU / 4 GB is ample — 07 §L.1), PostgreSQL, Redis.
2. Wildcard DNS `*.yourapp.com` and a wildcard certificate (DNS-01), plus `admin.yourapp.com`.
3. `CONSOLE_HOST=admin.yourapp.com`, `TENANT_BASE_DOMAIN=yourapp.com`, and **`CONSOLE_PATH_PREFIX` empty** — that restores the structural wall.
4. `export_tenant` here, `import_tenant` there.

Nothing in the application changes. The tenancy the free tier runs with one client is the same tenancy that runs with fifty; only the hostnames and the database differ.

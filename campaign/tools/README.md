# Re-shooting the campaign screenshots

Four scripts. They build a disposable demo center in a **separate database** and
drive the real app in Chromium. Your `db.sqlite3` is never opened.

| Script | Does |
|---|---|
| `seed_demo.py` | Builds the center: 320 students, 31 groups, 703 lessons, ~12k attendance rows, 3 months of billing. Run once. |
| `make_live.py` | Puts four of today's lessons in session **around the current clock**, with a scan feed. Re-run before every capture. |
| `refresh_ids.py` | Picks the fullest lesson / group / student to photograph and a spare card to scan. |
| `capture.mjs` | Drives Chromium, signs in, walks the shot list, writes PNGs. |

## Why `make_live.py` must be re-run

Lesson check-in windows are frozen at creation, and the live-session screens
compare them against the wall clock. Screenshots taken hours after seeding would
show a closed window and an empty scan board. `make_live.py` retimes the chosen
lessons to straddle "now".

It also **restarts matter**: the lesson snapshot cache is per-process
`LocMemCache`, so a running server keeps serving the old lesson times. Always
start the server *after* `make_live.py`, which is what the pipeline below does.

## Full run

```sh
export SCRATCH=/some/scratch/dir            # anywhere outside the repo
export DATABASE_URL="sqlite:///$SCRATCH/demo.sqlite3"
export DJANGO_SETTINGS_MODULE=cms.settings.dev
export PYTHONIOENCODING=utf-8

# once
python manage.py migrate --noinput
python manage.py setup_local_dev            # twice: the second run wires
python manage.py setup_local_dev            # localhost to the demo tenant
python campaign/tools/seed_demo.py

# every capture
python campaign/tools/make_live.py
python campaign/tools/refresh_ids.py
python manage.py runserver 8712 --noreload &
node campaign/tools/capture.mjs campaign/screenshots ar "$SCRATCH/ids.json"
node campaign/tools/capture.mjs campaign/screenshots en "$SCRATCH/ids.json"
```

`capture.mjs` needs `npm install playwright && npx playwright install chromium`.
The scripts hard-code the project path in their `sys.path.insert` line — adjust
it if the repo moves.

## Notes

- Each language run **really scans a card**, which is a real write. `refresh_ids.py`
  hands each run a different unused card; it warns if fewer than two are spare.
- The hero lesson is deliberately set to start a few minutes in the *future*, so
  the scan verdict reads as genuinely on time. `late_minutes` counts from the
  scheduled start regardless of the lateness threshold, so a scan into an
  already-running lesson can never show a clean on-time result.
- Sign-in is `admin` / `demo12345678` on the demo tenant.

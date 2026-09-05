# Campaign screenshots

54 screenshots of the running system — 27 per language, `ar/` (RTL) and `en/` (LTR).

- **Resolution:** 1560 × 980 CSS px captured at 2× → **3120 × 1960 px** PNG. Large
  enough for print; downscale for web rather than upscaling.
- **Theme:** light, default teal accent (`#1f6f8b`), comfortable density.
- **Signed in as:** أ. مصطفى عبد الحميد — center admin, so every menu and action
  button is visible. Screens shot as a cashier or scan operator would show fewer.

Everything on screen is real output from the real app driving a real database —
nothing is mocked up or retouched.

## The demo center

A fictional center, **سنتر النخبة التعليمي**, built for these shots:

| | |
|---|---|
| Students | 320 |
| Groups | 31, across 15 grade/subject offerings |
| Instructors | 7 |
| Lessons | 703 over 10 weeks, 4 in session at capture time |
| Attendance records | ~12,400 |
| Cards | 200 issued, 60 blank in stock |
| Monthly charges | 2,084 across July–September 2026 |

Names, phones and schools are invented. No real student data appears anywhere.

## Shot list

Numbered in campaign order — roughly the order a center owner discovers the product.

### The hook

| File | What it shows | Use it for |
|---|---|---|
| `01-login` | Split-screen sign-in with the center's name and three value bullets | Ad landing, first slide |
| `03b-scan-accepted` | **Hero shot.** A card scanned, accepted in 16 ms, student named in green, live feed of 26 check-ins beside it | The one image to lead with |
| `02-dashboard` | Today's KPIs, this month's money, sessions open right now | "Everything at a glance" |

### Attendance — the core loop

| File | What it shows | Use it for |
|---|---|---|
| `03-scanner-live` | The scan screen waiting for a card, counters above | "No mouse needed" |
| `04-scanner-picker` | Choosing which session to scan into | Setup story |
| `11-lesson-attendance` | A finished lesson: 38 expected, 33 present, 4 late, 1 absent, with check-in and check-out times | "You know exactly who was in the room" |
| `10-lessons` | The lesson calendar and its statuses | Scheduling |

### Students

| File | What it shows | Use it for |
|---|---|---|
| `05-students-list` | 320 students, filters by stage/grade/status | Scale |
| `06-student-profile` | Profile header and overview | Context shot |
| `06b-student-subjects` | One student's subject subscriptions | "Multi-subject students, handled" |
| `06c-student-attendance` | That student's attendance history | Parent conversations |
| `06d-student-ledger` | 3 months × 4 subjects of charges, paid/partial/unpaid, plus receipts | **Strongest money shot** |
| `07-student-new` | The enrolment form | "Registration takes a minute" |

### Money

| File | What it shows | Use it for |
|---|---|---|
| `12-payments` | The cashier workspace: expected / collected / outstanding, per-charge collect buttons | Owner's headline concern |
| `15-report-outstanding` | Who owes what, with guardian phone numbers, exportable | "Chase arrears in one click" |
| `17-report-finance` | Revenue by group | Management reporting |

### Groups and structure

| File | What it shows |
|---|---|
| `08-groups` | All groups with fill and status |
| `09-group-roster` | One group: 95% attendance, roster, weekly timetable, month's collection |
| `18-academics` | Stages |
| `18b-academics-offerings` | Grade/subject offerings and their monthly fees — the pivot the whole system hangs off |
| `18c-academics-teachers` | Instructors and their subjects |

### Cards, reports, administration

| File | What it shows |
|---|---|
| `13-cards` | Card inventory: issued, blank stock, batches, lost/replace actions |
| `14-reports-index` | All 13 built-in reports |
| `16-report-attendance` | Daily attendance with totals, CSV/Excel/PDF export |
| `19-users-roles` | Staff accounts and roles |
| `20-settings` | Center identity, attendance and billing policy |
| `21-audit-log` | Who changed what, when — the trust shot |

## Three things worth fixing before print

1. **The selected item in the reports sidebar renders in stock Bootstrap blue**
   (visible in `15-`, `16-`, `17-`). `.list-group-item.active` is the one
   component the theme never overrides, so it ignores the teal accent. A
   one-line CSS fix; ask and it can be done, then these three re-shot.
2. **Subject names stay Arabic in the English UI** (`الفيزياء` beside
   `Group A` in `en/02-dashboard`). `Subject.__str__` returns `name_ar or name`,
   so the English `name` column is never used. Worth fixing if the English set
   is going to a non-Arabic audience.
3. **The login hero heading is dark navy on dark teal** (`01-login`, both
   languages) — it nearly disappears against its own background, and this is the
   first thing a prospect sees. Lightening that one heading would make the best
   brand shot in the set actually readable.

## Re-shooting

`../tools/` holds the three scripts that produced all of this, plus notes on
running them. The demo database is disposable and lives outside the repo — your
own `db.sqlite3` was never touched.

# 04 — Payment Architecture

## F.1 The billing model in one line

> For every (student, subject-offering, month) the center issues **one obligation**; against it the student posts **many transactions**; the balance and the status are always **derived**, never typed.

```text
Student ──< MonthlyCharge (student, grade_subject, billing_month)  UNIQUE
                  │  amount_due  discount_amount  total_paid  balance(generated)  status(derived)
                  └──< Payment (kind=PAYMENT|REFUND, amount>0, method, receipt_no, collected_by)
```

Nothing here touches attendance, and nothing in attendance touches this (Principle 3). The single, deliberate, *optional* bridge is a read-only balance check inside the scan service, gated by `payments.enforce_on_attendance` (default off).

## F.2 Charge generation

**Source of truth for "who should be billed":** the set of `StudentGroupAssignment` rows with `status=ACTIVE` overlapping the billing month, joined to `Group.monthly_fee`.

```python
generate_monthly_charges(billing_month: date, *, only_group=None, dry_run=False, actor=None) -> Report
```
1. `billing_month` is normalised to the 1st.
2. For each active assignment whose `start_date ≤ month_end` and (`end_date is null` or `end_date ≥ month_start`):
   - `amount_due = assignment.group.monthly_fee` (fee snapshot, A3),
   - `due_date = billing_month + (billing.due_day_of_month − 1)`,
   - `MonthlyCharge.objects.get_or_create(student, grade_subject, billing_month, defaults=…)`.
3. `get_or_create` on the unique key makes the command **idempotent** — re-running it never double-bills; it only reports what already existed.
4. Runs three ways: `python manage.py generate_charges 2026-08`, a Celery Beat job on `billing.auto_generate_day`, or a "Generate charges" button (Cashier/Admin) which always shows a **dry-run preview first** (students, count, total EGP) before committing.
5. Writes one `AuditLog` per created charge plus a summary row.

Students who join mid-month get a charge the moment their assignment is created (`post_save` hook calls the same service for the current month) — with the full fee unless an admin applies a discount (A2).

## F.3 Posting a payment

```python
record_payment(charge, amount, *, method, collected_by, paid_at=None, reference="", notes="") -> Payment
```
```text
transaction.atomic():
    charge = MonthlyCharge.objects.select_for_update().get(pk=charge.pk)   # row lock: serialises concurrent cashiers
    validate: amount > 0
              charge.status not in (CANCELLED, WAIVED)
              if not settings.allow_overpayment: amount ≤ charge.balance   → ERR_OVERPAYMENT
    Payment.objects.create(kind=PAYMENT, amount=amount, receipt_number=next_receipt_no(), …)
    charge.total_paid = F("total_paid") + amount        # arithmetic in SQL, not in Python
    charge.save(update_fields=["total_paid", "updated_at"])
    charge.refresh_from_db(fields=["total_paid", "balance"])
    recalculate_status(charge)
    AuditLog(action="PAYMENT_CREATED", changes={…}, reason=notes)
```

Why both a `select_for_update` **and** `F()` arithmetic? The `F()` expression alone prevents a lost update on `total_paid`; the row lock additionally makes the *read* used for the overpayment check and the status recomputation consistent. Two cashiers taking 300 EGP each on a 500 EGP charge: one commits, the second re-reads `balance = 200` inside its lock and is rejected (or accepted as overpayment if the policy allows). On SQLite (dev) the lock is a no-op — the `F()` expression still keeps `total_paid` correct, and the overpayment check is merely best-effort. This is one of the two reasons production must be Postgres.

`receipt_number` comes from a dedicated sequence service (`payments.services.next_receipt_no()` using `select_for_update` on a counter row, or a Postgres `SEQUENCE`) so receipts are gapless-ish and unique — a `UNIQUE` index is the final guarantee.

### Multiple payments per month (brief §28)
```text
Charge: Physics, Aug 2026, due 500.00
  Payment #R-000191  01/08  200.00  CASH   → total_paid 200  balance 300  PARTIALLY_PAID
  Payment #R-000240  10/08  150.00  CASH   → total_paid 350  balance 150  PARTIALLY_PAID
  Payment #R-000318  20/08  150.00  WALLET → total_paid 500  balance   0  PAID
```
The ledger is the history; no row is ever mutated.

## F.4 Status derivation (never typed by a human)

```python
def recalculate_status(charge):
    if charge.status in ("CANCELLED", "WAIVED"):
        return charge.status                 # administrative states are sticky
    net = charge.amount_due - charge.discount_amount
    paid = charge.total_paid
    if paid <= 0:        charge.status = "UNPAID"
    elif paid < net:     charge.status = "PARTIALLY_PAID"
    elif paid == net:    charge.status = "PAID"
    else:                charge.status = "OVERPAID"
```
- `WAIVED` and `CANCELLED` are set only by an explicit admin action with a reason, and are excluded from "expected revenue" (`WAIVED`) / from all totals (`CANCELLED`).
- `status` is stored (so it can be indexed and filtered cheaply) but is **write-only from this function**. Forms never expose it. A nightly `recalculate_charges` command re-derives every open month and reports drift — drift should always be zero; if it isn't, something bypassed the service and that is a bug worth an alert.

## F.5 Refunds and corrections

| Situation | Action | Result |
|---|---|---|
| Cashier typed 500 instead of 50 | post `REFUND` 500 with `reverses=<original payment>`, reason required | both rows visible; `total_paid` back to prior value; audit shows who and why |
| Student leaves, money returned | post `REFUND` with reason `STUDENT_LEFT` | `total_paid` drops; status recomputes (likely `PARTIALLY_PAID`/`UNPAID`) |
| Charge issued by mistake | `cancel_charge(charge, reason)` — only allowed when `total_paid == 0` | `status=CANCELLED`, excluded from reports |
| Free student (scholarship, staff child) | `waive_charge(charge, reason)` | `status=WAIVED`, `balance` still shown but excluded from expected revenue |

`total_paid` is always `Σ PAYMENT − Σ REFUND`; a refund uses the same locked-row flow as a payment with the sign inverted at the aggregation step. **No `Payment` row is ever updated or deleted** — the admin registers it with `has_delete_permission = False` and read-only fields.

## F.6 Outstanding balance and reporting queries

`balance` is a stored generated column, so every "who owes us money" query is an index scan:
```python
MonthlyCharge.objects.filter(billing_month=m, balance__gt=0)                       # ix_charge_outstanding
    .exclude(status__in=["CANCELLED", "WAIVED"])
    .select_related("student", "grade_subject__subject", "group")

# Center-wide monthly summary — one query, no Python loops
MonthlyCharge.objects.filter(billing_month=m).aggregate(
    expected=Sum("amount_due") - Sum("discount_amount"),
    collected=Sum("total_paid"),
    outstanding=Sum("balance"),
    fully_paid=Count("id", filter=Q(status="PAID")),
    partial=Count("id", filter=Q(status="PARTIALLY_PAID")),
    unpaid=Count("id", filter=Q(status="UNPAID")),
)

# Daily collection (cashier close-out) — from the ledger, not from charges
Payment.objects.filter(paid_at__date=today).values("method", "collected_by").annotate(
    total=Sum("amount", filter=Q(kind="PAYMENT")) - Sum("amount", filter=Q(kind="REFUND")),
    count=Count("id"),
)
```

Student financial dashboard (brief §33) is one query per student:
```python
MonthlyCharge.objects.filter(student=s, billing_month=m).select_related("grade_subject__subject")
# → subject | due | discount | paid | remaining | status
```

## F.7 Payment ⟂ Attendance, and the one optional bridge

All six combinations in brief §31 are representable and none is inferred:

| | PRESENT | ABSENT |
|---|---|---|
| **PAID** | normal | student paid and skipped — chase them |
| **PARTIALLY_PAID** | normal, warn at the desk | |
| **UNPAID** | allowed by default (A10) | |

The bridge, when `payments.enforce_on_attendance = true`:
```text
gate 7 (03 §D.5):
   charge = MonthlyCharge for (student, lesson.group.grade_subject, current billing month)
   if no charge                      → allow (nothing has been billed yet)
   if today ≤ billing_month + grace_days → allow (grace period)
   if balance ≤ 0                    → allow
   if status == PARTIALLY_PAID       → payments.allow_partial_attendance ? WARN : block/approval
   if status == UNPAID               → payments.allow_unpaid_attendance  ? WARN : block/approval
```
`block` returns `ERR_PAYMENT_BLOCKED`; `approval` returns `NEEDS_APPROVAL_*`, and a supervisor override (permission `attendance.approve_exceptional`) creates the attendance with `attendance_type=EXCEPTIONAL`, `requires_approval=True`, `approved_by` set and a mandatory reason. The screen always shows due / paid / remaining so the receptionist can collect on the spot — the scanner has a "استلام دفعة" shortcut that opens the payment modal for that exact charge.

## F.8 Money-handling invariants (enforced, not hoped for)

1. `amount > 0` — DB `CHECK`.
2. Amounts are `Decimal`; `DECIMAL_SEPARATOR` handling done by Django forms; **never** `float`.
3. `total_paid` is a cache of the ledger; `recalculate_charges` verifies it nightly.
4. `balance = amount_due − discount_amount − total_paid`, computed by the database.
5. `collected_by` is `PROTECT` and non-null — every EGP is attributable to a person.
6. Every payment, refund, waiver, cancellation and discount writes an `AuditLog` with a reason.
7. Deleting a student, group, charge or payment is impossible (`PROTECT`); deactivation is the only "removal".

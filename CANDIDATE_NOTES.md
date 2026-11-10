# Candidate Notes — Task B: Monthly Billing Run

## 1. What I built and why

I opted for Task B as it seemed the most aligned with the initial position I'd expressed interest in.

I implemented the three stubbed endpoints: a `BillingRun` audit record, an `Invoice` per
Company × Freelancer × month with `InvoiceLineItem` snapshots, and email delivery to both the
company's `billing_email` and the freelancer.

**One note on the brief:** the current README is less detailed than it used to be, I checked the git history of the rep and commit `e48c330`
removed some of the more explicit requirements and deliberately made them more vague. I hope that's not considered as an attempt to
circumvent the test! It's natural habit I think ingrained from my workplace to have a bit of a dig when presented with a digital artefact
like a git repo etc. I did instruct the model to take heed of the removed detail since I felt it pertinent to the overall result.

## 2. Key implementation notes

**Idempotency is enforced at the database level**, specifically enforced by
`UniqueConstraint(company, freelancer, period_start)`, with `period_start` a real `Date` (always
the 1st) rather than a `"2026-05"` string. The run lets the insert raise `IntegrityError` and
treats it as "already billed" — a `get_or_create` or `exists()` pre-check is correct in serial use
but loses the race under concurrency. The `try/except` wraps the `atomic()` block rather than
sitting inside it, since a broken transaction can't be caught and continued.

**We allow for partial months by only worrying about the date value**
from the TimesheetEntry object _not_ the underlying contract. Honestly I struggle the most with **the implications** of date math
operations and before I started most of my thinking time was taken up by wondering about this and
how it might work in practice, of course once I started fleshing out the data model everything falls into place.

**Transaction boundary:** invoice rows commit first, then email is attempted, then
`delivery_status` (`pending → sent | failed`) is written separately. I've made this mistake before and coupled
delivery state status with overall object success status and it is a **pain**. Much rather that we handle the
email operations outside of the database transaction of the invoice so they can't interfere with each other.

**Line items are snapshots, not joins.** Each stores its own `hours`, `daily_rate`, `amount` and
`date`, with a nullable FK back to the entry for traceability. An issued invoice must not change
because someone later edits a timesheet. I don't typically work with such immutable business concepts so I erred
on the side of caution and didn't link them. In fact I also ensured rows are protected from cascading deletes
w.r.t the `Company` model etc.

**Email:** two separate messages rather than one with two recipients, so neither party sees the
other's address. Again just to err on the side of caution. Part of me thinks its fine but if I have the option why not?

**Tested:** 39 cases in the billing app (80 total, all green) — the constraint firing, the skip
path, partial months from both directions, an entry dated _after_ its contract ended (where
entry-date and contract-date filtering actually diverge), exact `Decimal` totals,
rate-change-after-the-fact, recipient privacy, and the email-failure path.

## 3. Workflow

I used the **pi coding agent** (and deepseek-v4.1-flash) alongside a written implementation plan. The workflow looks like:

- I wrote the task brief out in my own words and asked for an implementation plan in simple HTML, structured step by step so that I — the one in the driving seat — could work through it with the model one step at a time.
- I then in a fresh session read that plan and start going through it with the model step by step, encouraging the use of diffs and snippets, and we mark
  each step as done in the plan once I approve the models eventual suggestion (which we come to after I ask it questions about why of whats it done as well as any potential red flags that I observe.)

Two things that came out of the step by step that wasn't flagged in the initial plan that I decided to deviate towards were:

- The plan declared a `BillingRun.STATUS_FAILED` state nothing could ever transition into. I kept
  the value as a declared-but-not-yet-writable state rather than inventing a failure path I hadn't
  designed or deleting a state I expect to need.
- The plan had `admin.site.register(BillingRun)` with no customisation, which would let anyone
  _edit_ an audit record. I tightened it to read-only — a run record is evidence of what happened,
  not state you mutate.

## 4. Next steps

- **Retry/backoff for failed deliveries.** `delivery_status` exists for this; the worker doesn't.
  A `manage.py` command plus a scheduled job, rather than pulling in Celery for one use case.
- **Lock the run.** Two admins clicking at once is safe for invoice creation (the constraint
  handles it) but still produces two `BillingRun` rows.
- **Void / credit notes.** `Invoice.status` has `void` and nothing can reach it — same shape of
  problem as `STATUS_FAILED`.
- **VAT.** `tax` is `0.00` and `total = subtotal + tax`; the field exists so adding real rates
  doesn't need a totals migration - the model added this as a "just in case" and much like sending two separate emails I ignored YAGNI and went with adding it.
- **`DEFAULT_FROM_EMAIL`** was added because Django's fallback is `webmaster@localhost` — the one
  change outside the billing app.

## 5. Thoughts

I LOVE Django, I mean it does so much heavy lifting that I have never once appreciated. The ORM is a joy (looking at you Tortoise ORM...), the user and admin management out of the box is incredible, honestly what a treat. If somewhat intimidating with my little Django experience (I primarily use FastAPI/NiceGUI these days).

Regardless of the interview outcome I'm probably going to start messing around with Django and look to incorporate it into some future project!

As for the test itself, love how easy it was to setup and shout out to whoever had the idea of packing a Nix flake. My only and main critique is that when posing whether or not to use AI tools I would have rather instead a smaller brief with an explicit AI ban, since I was left very uncertain about how/when to use AI with this task and ultimately decided to use the exact approach I use at work with AI assistance. Which is read through it all myself, then generate a plan to read through myself, then go through the plan step by step.

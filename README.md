# Banyan Deal Intelligence System

A single-file web app for underwriting, tracking and analyzing a commercial real estate
portfolio. Supabase for storage, vanilla JS for everything else. No build step, no
framework, no dependencies.

**Live:** https://rsbanyan.github.io/banyan-deal-intelligence
**Repo:** https://github.com/rsbanyan/banyan-deal-intelligence

---

## What this is for

Two books of business live on one dashboard:

- **Banyan** — the 50/50 JV with the Chase family (S&A Properties). Twelve properties.
  Industrial, covered-land-play strategy: buy cheap buildings in good locations, collect
  locked-in NOI, wait for lease expiry, mark rents to market.
- **SRE** — own-money deals going forward. First acquisition is 1079 Woodleys Way,
  Columbia SC.

Plus prospected deals, Chase deals and market comps as separate categories. The portfolio
selector in the header scopes every view.

---

## Running it

The whole app is `index.html`. To change it:

1. Edit `index.html`
2. Commit to `main`
3. GitHub Pages redeploys in ~60 seconds
4. Hard refresh (Cmd+Shift+R); if cache persists, use an incognito window

Sanity check that new code actually loaded — in the browser console:

```js
typeof MARKET_DISCOUNT_RATE   // 'number' if current, 'undefined' if stale
```

There is a client-side password gate. It is decorative: the constant is in the source of a
public repo. See **Security** below.

---

## Architecture

Everything is in `index.html`, roughly in this order: styles, password screen, app markup
with one `<div class="view">` per screen, then one `<script>` block.

### Views

| View | Function | What it does |
|---|---|---|
| Portfolio | `loadDashboard()` | KPI strip, action flags, all-deals table |
| Deal detail | `showDeal(dealId)` | Full record, value matrix, since-acquisition, event timeline |
| Rent Gap | `loadRentGap()` | In-place vs market rent, locked NOI, latent value |
| Markets | `loadMarkets()` | Manufacturing migration scores |
| Analyzer | `runAnalyzer()` | Live BTIRR/ATIRR, duration, sensitivity grid, freeze panel |
| Add Deal | `submitDeal()` | Manual entry, writes straight to Supabase |

### Portfolio scoping

`window.currentPortfolio` holds the selected category. `portfolioParam()` turns it into a
PostgREST filter fragment; `switchPortfolio()` resets the load flags and re-renders.
Dashboard and Rent Gap both go through `portfolioParam()` — **do not hardcode
`deal_category=eq.Banyan` in a new view.** That was the original bug that made 34
prospected deals invisible for months.

### Supabase access

Three thin helpers wrap `fetch`: `supabase(table, params)`, `supabasePost(table, body)`,
`supabasePatch(table, params, body)`. PostgREST query syntax throughout.

---

## The IRR engine

This is the part that matters most, and it is not what you'd guess from the name.

The Excel deal model's **"IROR" is not Excel's `IRR()`**. It is a hand-rolled solve for the
discount rate `r` where:

```
NPV(monthly payments) + NPV(exit) − NPV(tax on sale) = price
```

Reverse-engineered from the Carrier/Raleigh workbook formulas in May 2026 and reimplemented
in JavaScript. Verified to four decimals against Raleigh, Elkhart and Canvas.

Mechanics worth knowing before touching `buildCashflows()` or `calcIRR()`:

- **Payment column is a recursion.** Each month equals the prior month, stepped up by the
  active regime increase when `mod(m, freq) == mod(start, freq)` and `m >= start`.
  A regime with `start = 0` is parked at `lease_term + 1` (off). `start >= 1000` is
  disabled. A first regime with `freq = 1000` means a single one-time step.
- **Exit is cost-basis appreciation, not a cap-rate terminal.** Each improvement bucket and
  the land grows at its own appreciation rate, summed, times `(1 − commission)`. The
  workbook also contains a "Terminal Market Value from Cap" cell, but the exit formula does
  not reference it — it is an occasional manual override, read by hand, not by the engine.
- **BT vs AT differ only in discounting and sale tax.** Before-tax discounts the payment at
  the BT rate and ignores tax on sale. After-tax subtracts
  `(payment − depreciation) × incomeTax`, discounts at the AT rate, and nets the sale tax.
  Capital gain adds depreciation back for recapture.

**Duration** is Macaulay, stored two ways per deal: `duration_btirr` (discounted at the
deal's own BTIRR) and `duration_market` (discounted at 7%, the industrial benchmark). A deal
with duration 8 loses roughly 8% of value per 100bp move in cap rates. This is the language
that lets real estate be compared to bonds.

```
Duration = Σ [t × CF_t / (1+r)^t] / Σ [CF_t / (1+r)^t]
```

Backfilled durations used placeholder assumptions — 7-year hold, 2.5% escalation, 7% exit
cap, 30% land ratio. They are replaced with deal-specific values only once the `leases`
table is populated, which has not happened yet. **Treat duration spread across the portfolio
as artificially tight until that lands.**

---

## Database

Ten tables. `deals` is the core, one row per property.

| Table | Purpose |
|---|---|
| `deals` | One row per property. Everything hangs off this. |
| `deal_snapshots` | Immutable frozen underwriting. See below. |
| `tenants` | Tenant companies |
| `leases` | One per lease; a deal can have several. **Not yet populated.** |
| `industries` / `deal_industries` | Lookup + many-to-many |
| `markets` | One row per market, with intelligence scores |
| `deal_events` | Timeline per deal |
| `comps` | Comparable sales and leases |
| `contacts` / `deal_contacts` | Brokers, attorneys, accountants + many-to-many |

### Columns and gotchas

- `deal_category` is governed by a **check constraint** (`deals_deal_category_check`).
  Adding a portfolio value means rebuilding that constraint, not just inserting. Rebuild it
  from the table's own distinct values so nothing existing gets locked out.
- `ownership_pct` — your share. `1.00` wholly owned, `0.50` for a JV. Added Sep 2026
  because dollar rollups previously assumed 100%. Woodleys is `0.50`.
- `banyan_equity` is a **dollars-in** column, not a percentage. Badly named now that there
  are two books; relabel in the UI rather than renaming the column, which is referenced in
  several places.
- Several columns are Banyan-portfolio-specific and are correctly NULL for prospected deals:
  `settlement_date`, `market`, `submarket`, `est_value_low/high`, `equity_multiple`,
  `noi_banyan_share` and others. Dashboard filters using them will hide prospected deals,
  which is intended.
- **Default to `numeric`, not `integer`.** Anything derived from acres × 43,560, or any tax
  or discount multiplication, produces decimals. `land_sf` had to be widened after silently
  failing twelve commits on the first bulk load.

### Frozen underwriting

`deal_snapshots` stores the inputs and outputs a deal was underwritten on, as an immutable
record. Rows cannot be updated or deleted — a trigger raises, and no UPDATE/DELETE policy
exists. One `acquisition` snapshot per deal is enforced by a partial unique index; `annual`
and `revision` snapshots are unlimited.

The point is the **annual join**: each year, re-run the model with actuals substituted for
the years elapsed, compare against the frozen acquisition-date underwriting, and identify
which assumption missed and by how much. The freeze is worthless without that follow-up.

Freeze from the Analyzer: enter the deal at your real conventions, pick the target deal and
snapshot type, click Freeze.

---

## Importers

Three standalone HTML tools, **not in this repo** and never folded into the app. If they are
lost, the specs below are enough to rebuild.

- **`banyan-import-test-v3.html`** — single-file importer. Label-anchored .xlsx parser via
  SheetJS in the browser. Extracts summary plus full engine inputs: four improvement buckets
  with depreciation periods and per-deal appreciation rates, land derived as
  `price − Σ improvements`, four regime rows, commission, tax rates, 1031 probability.
  Reconciles against an independent JS reimplementation of the IROR engine, solving against
  the model's own saved exit value.
- **`banyan-add-deal-autocalc.html`** — manual entry with four live derived fields.
- **`banyan-batch-importer.html`** — the multi-file drag-and-drop. Reuses the v3 parser
  verbatim. Convergence-aware: reads the model's Delta cell and widens IROR tolerance to 1%
  where `|Delta|/price ≥ 0.1%`, because the Excel solver was frequently left un-iterated and
  the stated IROR is the *previous* iteration. Stores the converged engine value as canonical
  `atirr`/`btirr`; the stated value goes to the `model_atirr`/`model_btirr` audit pair.
  Dup-skips by `deal_name`, so re-running is safe.

**Design rule:** prospected models are pre-finalized decisions. The saved exit value *is* the
decision. The importer records; the analyzer explores. Don't re-derive, don't infer.

### Lessons from the first bulk load

1. **Test-load-of-one is non-negotiable.** Catching city contamination and a NULL
   `price_per_sf` on row one saved 33 polluted rows.
2. **Confirm the schema before any bulk load.** Silent column-name drops
   (`price_psf` vs `price_per_sf`) would have corrupted 30+ rows.
3. **Unconverged models are common,** and the engine's value is the more correct one.
4. **Filenames are acceptable deal names** for prospected deals. Clean later; don't block
   the load on cosmetics.

---

## Data state

As of September 2026:

- 12 Banyan deals, fully populated
- 34 prospected deals loaded May 2026
- 1 SRE deal (Woodleys Way)
- 4 more prospected deals synced and queued but never committed — Cleveland OH, Huntley IL,
  Hajoca PA, Kayak-CT
- 16 prospected models incomplete, missing Building SF / NOI / lease term in the source
  workbook. That's Excel work, not a tool change.
- `leases` table empty
- No event timelines, industry links or contact mappings on prospected deals — they are
  summary-level only

Full load would be roughly 66 deals. The comp engine (z-scoring a new deal against the comp
set) needs about 50 to be useful and 150 to be sharp.

---

## Useful queries

```sql
-- Portfolio overview with duration
select deal_name, city, state, entry_cap_rate, atirr,
       rent_psf, market_rent_psf, duration_btirr, duration_market, est_value_high
from deals where deal_category = 'Banyan' and status != 'Sold'
order by atirr desc nulls last;

-- Locked NOI, biggest gap first
select deal_name, annual_noi,
       round((market_rent_psf * building_sf)::numeric, 0) as market_potential,
       round((market_rent_psf * building_sf - annual_noi)::numeric, 0) as locked_noi
from deals where deal_category = 'Banyan' and status = 'Active'
  and market_rent_psf > 0 and building_sf > 0
order by locked_noi desc;

-- Value-weighted portfolio duration
select sum(duration_market * est_value_high) / sum(est_value_high) as wavg_duration,
       count(*) as deals_included
from deals where deal_category = 'Banyan' and status != 'Sold'
  and duration_market is not null and est_value_high is not null;

-- What is actually in the database, by book
select deal_category, count(*) as deals, count(btirr) as have_btirr
from deals group by deal_category order by deals desc;
```

---

## Security

The Supabase anon key and the password constant are both in this file, in a public repo.
RLS allows public read on all tables and public insert/update on `deals`.

This was an acceptable trade when the app held Banyan portfolio metrics behind a password
nobody was looking for. It is a different question now that a second book of business lives
here, and a worse one if documents or dollar flows are ever added.

If hardening: rotate the anon key (the current one has been public and is in git history),
move to real Supabase Auth with per-user policies, and make the repo private — noting that
GitHub Pages from a private repo requires a paid plan.

---

## Known gaps

- `leases` table empty — duration spread is artificially tight until it isn't
- IRR engine consistency check between the live app and the verified Excel engine was
  flagged in June 2026 and its status is unconfirmed. **Read the code before assuming;
  do not infer the engine from output values.**
- Action flags on the dashboard are hardcoded text, not driven by lease dates
- Importers never folded into the app
- No lease expiry ladder
- Deploy is still a manual copy-paste into the GitHub web editor

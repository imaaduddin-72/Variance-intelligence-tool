# Variance Intelligence Tool

An automated FP&A variance analysis pipeline built in Python and SQLite.
Replaces manual monthly Excel variance reporting with a 9-stage pipeline
that runs in under 0.5 seconds.

Built as a portfolio project to demonstrate FP&A automation skills.
The pipeline is designed to be reusable — swap in any business's budget
CSVs, update the metadata file with that business's rules, and the full
analysis regenerates automatically.

---

## What it does

- Loads budget vs actual data from CSV into a structured SQLite database
- Validates data integrity with 5 automated checks before calculating anything
- Calculates budget variance, year-over-year variance, and a 4-state status
  flag (Favorable / Unfavorable / On Plan / Deviation) for every line item
- Detects consecutive-month trends using SQL window functions (LAG)
- Flags chronic issues — line items that have been unfavorable for 3 or
  more months in a row
- Calculates three financial risk metrics: concentration risk, cash
  conversion cycle, and volatility scoring
- Generates plain-English commentary sentences for all flagged items
- Outputs a formatted Excel board pack with Summary, Detail, and
  Flagged tabs
- Exports a flat Power BI ready feed with a commentary column included
- Archives every run in a snapshot table and diffs flagged items vs the
  previous run, classifying each as New, Resolved, or Recurring

---

## Key findings from illustrative data

- product_sales averages 45.4% of total revenue (38.9%–48.1% by month) —
  HIGH concentration risk in 17 of 18 months
- Cash conversion cycle averages 14.3 days and stays within 12–17 days
  every month — healthy and stable
- Professional fees is the hardest line to forecast (21.1% month-to-month
  volatility); every other line item is below 14%
- COGS shipping has been over budget for 4 consecutive months
  (Mar–Jun 2025) and is flagged as chronic — the most actionable finding
- Engineering headcount has deviated from plan for 3 consecutive months
- 5 material variances in total; the largest is a ₹204K product_sales
  shortfall in August 2024

---

## Pipeline stages

| Stage | Module | What it does |
|-------|--------|--------------|
| 1 | database.py | Load CSVs into SQLite |
| 2 | data_quality.py | 5 automated data integrity checks |
| 3 | variance_calculator.py | Budget and YoY variances, materiality and status flags |
| 4 | trend_analysis.py | LAG() window function streak detection |
| 5 | financial_risk.py | Concentration, liquidity, and volatility metrics |
| 6 | commentary_generator.py | Auto-generated plain-English narratives |
| 7 | report_generator.py | Formatted Excel board pack |
| 8 | export_for_model.py | Power BI and EA model feed |
| 9 | snapshot_manager.py | Run history and diff vs last run |

---

## Data quality checks (Stage 2)

Before calculating anything, the pipeline runs 5 checks on the raw data.
If any critical check fails, the pipeline stops immediately with a clear
error message rather than producing a report with wrong numbers.
Check 3 is non-critical: it logs a warning but lets the run continue.

**1. No duplicate rows** (critical)
Checks that the same line item does not appear twice in the same month.
A duplicate row would silently double the variance for that item. The
input tables also enforce this with UNIQUE constraints, so a CSV with
duplicate keys is rejected at load time with a clear error.

**2. All categories have business rules** (critical)
Every category in the data (revenue, opex, etc.) must have a matching
entry in the metadata file that defines its rules — materiality threshold,
expected direction, risk level. Missing entries would silently get default
rules instead of the correct ones.

**3. Prior year data exists for current line items** (warning only)
Every current-year row needs a matching prior-year row to calculate
year-over-year growth. Rows without a match produce blank YoY columns.
A warning is logged if coverage falls below 90%, since new line items
legitimately have no prior-year history.

**4. No missing or negative amounts** (critical)
Budget and actual amounts must exist and be non-negative (except working
capital, which can legitimately go negative). Catches corrupted or
incomplete source files before they reach the calculation stage.

**5. Every month has the same number of line items** (critical)
If January has 23 rows and June has 18, five line items are missing from
June. This check flags incomplete months so they do not look like zeros
in the variance report.

The principle: a wrong report is worse than no report.

---

## How to run

Requires Python 3.10 or later. CI runs the tests on Python 3.10 and 3.12,
on both Windows and Linux.

```bash
pip install -r requirements.txt
python run_pipeline.py
```

Run the tests:

```bash
python -m unittest discover tests
```

---

## Input data format

Three CSVs required in the project folder:

- `budget_actuals.csv` — columns: id, period (YYYY-MM), category,
  subcategory, budget_amount, actual_amount
- `prior_year_data.csv` — columns: id, period, category, subcategory,
  actual_amount
- `category_metadata.csv` — columns: category, category_type,
  expected_direction, materiality_threshold_inr, risk_weight

---

## Tech stack

Python · SQLite · pandas · openpyxl · SQL window functions (LAG, ROW_NUMBER)

---

## Design decisions worth noting

**Exact year matching in JOINs**
Prior year data uses exact year-minus-one matching rather than matching
only on month number. This prevents a silent bug where months from
multiple years would match the same current-year row, inflating YoY
figures without any error message.

**4-state variance status**
Standard variance analysis uses Favorable and Unfavorable. This pipeline
adds On Plan and Deviation for stable categories (headcount, capex and
working capital), where a deviation is not inherently good or bad. The
tolerance band is configurable in config.py (default ±2%). The
is_favorable flag is derived from the status (Favorable or On Plan), so
the two never disagree.

**Unbudgeted spend stays visible**
A line with a zero budget still gets a variance (actual minus zero) and
can be flagged as material; only the percentage is left blank.

**Append-only snapshot table**
The variance_analysis table is wiped and rebuilt every run so it always
reflects current data. The variance_snapshots table is never wiped,
accumulating history across runs. This separation means the pipeline
is idempotent — running it twice gives the same current result — while
still enabling run-to-run comparison.

**Fail-fast validation**
Data quality checks run before any calculation. If a critical check
fails, the pipeline stops with a clear message rather than producing
silently wrong numbers.

**Set-based SQL where it matters**
The prior-year join, trend streak detection (LAG and a running SUM over
window functions), and the financial risk metrics are computed in
single SQL statements. Variance flags are applied in a Python loop over
the joined rows so each business rule stays a small, testable function.
The snapshot diff finds the previous run with ROW_NUMBER() and compares
flagged items with Python set operations.

**Volatility measured per line item**
Volatility is the month-to-month coefficient of variation for each
subcategory, so large and small lines in the same category (e.g.
salaries and utilities) are not pooled together.

---

## Limitations

- Uses illustrative synthetic data, not real company financials
- Single entity only — no multi-currency or multi-entity consolidation
- No forecasting or reforecast tracking (planned extension)
- Designed for monthly data; sub-monthly periods not tested

---

## Sample output

The `sample_output/` folder contains:
- `variance_report.xlsx` — the formatted Excel board pack
- `variance_commentary.txt` — the plain-English exceptions narrative

---

## License

MIT — see [LICENSE](LICENSE).

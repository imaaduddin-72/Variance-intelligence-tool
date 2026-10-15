# Variance-intelligence-tool.

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

- product_sales represents 45.4% of total revenue — HIGH concentration risk
- Cash conversion cycle averages 14.3 days — healthy and improving over time
- OpEx is the hardest category to forecast (91.3% month-to-month volatility)
- 6 line items show 18-month deviation streaks in CapEx and working capital,
  suggesting the budget baseline is wrong rather than execution being off
- COGS raw materials has been consistently over budget since March 2025 —
  the most immediately actionable finding

---

## Pipeline stages

| Stage | Module | What it does |
|-------|--------|--------------|
| 1 | database.py | Load CSVs into SQLite |
| 2 | data_quality.py | 5 automated data integrity checks |
| 3 | variance_calculator.py | SQL variance calculation, one INSERT per row |
| 3.5 | trend_analysis.py | LAG() window function streak detection |
| 3.6 | financial_risk.py | Concentration, liquidity, and volatility metrics |
| 4 | commentary_generator.py | Auto-generated plain-English narratives |
| 5 | report_generator.py | Formatted Excel board pack |
| 6 | export_for_model.py | Power BI and EA model feed |
| 7 | snapshot_manager.py | Run history and diff vs last run |

---

## Data quality checks (Stage 2)

Before calculating anything, the pipeline runs 5 checks on the raw data.
If any critical check fails, the pipeline stops immediately with a clear
error message rather than producing a report with wrong numbers.

**1. No duplicate rows**
Checks that the same line item does not appear twice in the same month.
A duplicate row would silently double the variance for that item.

**2. All categories have business rules**
Every category in the data (revenue, opex, etc.) must have a matching
entry in the metadata file that defines its rules — materiality threshold,
expected direction, risk level. Missing entries would silently get default
rules instead of the correct ones.

**3. Prior year data exists for every current line item**
Every current-year row needs a matching prior-year row to calculate
year-over-year growth. Rows without a match produce blank YoY columns.
This check catches that before it becomes a silent gap in the report.

**4. No missing or negative amounts**
Budget and actual amounts must exist and be non-negative (except working
capital, which can legitimately go negative). Catches corrupted or
incomplete source files before they reach the calculation stage.

**5. Every month has the same number of line items**
If January has 23 rows and June has 18, five line items are missing from
June. This check flags incomplete months so they do not look like zeros
in the variance report.

The principle: a wrong report is worse than no report.

---

## How to run

```bash
pip install pandas openpyxl
python run_pipeline.py
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
adds On Plan and Deviation for stable categories like headcount and
working capital, where a small deviation is not inherently bad. The
tolerance band is configurable in config.py (default ±2%).

**Append-only snapshot table**
The variance_analysis table is wiped and rebuilt every run so it always
reflects current data. The variance_snapshots table is never wiped,
accumulating history across runs. This separation means the pipeline
is idempotent — running it twice gives the same current result — while
still enabling run-to-run comparison.

**Fail-fast validation**
Data quality checks run before any calculation. If checks fail, the
pipeline stops with a clear message rather than producing silently wrong
numbers.

**SQL over Python loops**
All calculations push as much logic as possible into SQL rather than
iterating in Python. The trend analysis and snapshot comparison use
fully set-based single-pass SQL (LAG window functions and ROW_NUMBER).

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

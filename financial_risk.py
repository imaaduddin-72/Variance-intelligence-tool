"""
financial_risk.py
=================
Calculates revenue concentration risk, cash conversion cycle (DSO, DIO,
DPO, CCC) and per-category volatility scores.

Inputs:  budget_actuals table
Outputs: risk results returned to the pipeline and logged to pipeline.log
"""

import config
from database import BudgetDatabase
from logger_setup import get_logger

log = get_logger(__name__)


# ─────────────────────────────────────────────────────────────
# POLICY CONSTANTS
# Stored here (not config.py) — internal business logic only.
#
# FINANCIAL ANALOGY: fund mandate rules like "no single holding
# above 40% of NAV" — set by the risk committee, used only by
# the risk team, not a general pipeline setting.
# ─────────────────────────────────────────────────────────────
CONCENTRATION_HIGH   = 40.0   # % of total revenue — HIGH risk flag
CONCENTRATION_MEDIUM = 25.0   # %                  — MEDIUM risk flag
                               # below MEDIUM       — LOW risk
DAYS_PER_PERIOD = 30          # calendar days per accounting period


class FinancialRiskAnalyzer:
    """
    Three risk-analysis lenses applied to the budget_actuals data.

    FINANCIAL ANALOGY: the risk department's monthly review of the
    same underlying data the CFO already saw in variance_analysis,
    but asking different questions:

      Treasury:   "How concentrated are our revenue streams?"
      Working cap: "How long is cash locked in the operating cycle?"
      FP&A:       "Which lines are hardest to forecast reliably?"
    """

    def __init__(self, db: BudgetDatabase):
        self.db = db

    # ─────────────────────────────────────────────────────────
    # METHOD 1: CONCENTRATION RISK
    # ─────────────────────────────────────────────────────────

    def concentration_risk(self):
        """
        Revenue subcategory share of total — flags high concentration.

        QUERY STRUCTURE (two CTEs + final SELECT):

        CTE 1 — revenue_totals
            Aggregate all revenue subcategories to one total per period.
            This is the denominator for every concentration ratio.
            FINANCIAL ANALOGY: the "total portfolio value" row at the
            bottom of a fund holdings report.

        CTE 2 — subcategory_sums
            JOIN each individual revenue row to its period's total.
            After this JOIN, every row carries both its own amount AND
            the period total — enough to compute the % in the final SELECT.
            FINANCIAL ANALOGY: adding a "% of portfolio" column by
            dividing each holding value by the total NAV.

        FINAL SELECT
            pct_of_total = actual_amount / total_revenue × 100.
            CASE thresholds mirror portfolio concentration limits:
              > 40% → HIGH   (single stream dominates; systemic risk if lost)
              > 25% → MEDIUM (significant but not dominant; monitor closely)
              else  → LOW    (diversified revenue mix; lower structural risk)

        NULLIF(total_revenue, 0):
            Guards against division-by-zero in the edge case where a
            period has zero total revenue.  Returns NULL rather than
            crashing — the same pattern used in variance_calculator.py.
        """
        query = """
            -- ── CTE 1: revenue_totals ─────────────────────────
            -- One row per period: sum of ALL revenue subcategories.
            -- Period denominator for every concentration ratio.
            WITH revenue_totals AS (
                SELECT
                    period,
                    SUM(actual_amount) AS total_revenue
                FROM budget_actuals
                WHERE category = 'revenue'
                GROUP BY period
            ),

            -- ── CTE 2: subcategory_sums ────────────────────────
            -- Each individual revenue row, enriched with the period's
            -- total revenue via a JOIN on period.
            -- After this, every row knows both its own amount AND
            -- the period total — the two values needed for the % calc.
            subcategory_sums AS (
                SELECT
                    ba.period,
                    ba.subcategory,
                    ba.actual_amount,
                    rt.total_revenue
                FROM budget_actuals ba
                JOIN revenue_totals rt
                    ON ba.period = rt.period
                WHERE ba.category = 'revenue'
            )

            -- ── FINAL SELECT ────────────────────────────────────
            -- Compute the % share and apply the risk thresholds.
            -- NULLIF prevents division-by-zero on zero-revenue periods.
            SELECT
                period,
                subcategory,
                ROUND(actual_amount,  2)                              AS actual_amount,
                ROUND(total_revenue,  2)                              AS total_revenue,
                ROUND(
                    actual_amount * 100.0 / NULLIF(total_revenue, 0),
                    2
                )                                                     AS pct_of_total,
                CASE
                    WHEN actual_amount * 100.0
                         / NULLIF(total_revenue, 0) > {high}
                        THEN 'HIGH'
                    WHEN actual_amount * 100.0
                         / NULLIF(total_revenue, 0) > {medium}
                        THEN 'MEDIUM'
                    ELSE 'LOW'
                END                                                   AS concentration_risk
            FROM subcategory_sums
            ORDER BY period, pct_of_total DESC
        """.format(high=CONCENTRATION_HIGH, medium=CONCENTRATION_MEDIUM)

        rows = self.db.execute_query(query)
        results = [dict(row) for row in rows]

        high_count = sum(1 for r in results if r["concentration_risk"] == "HIGH")
        log.info(
            f"Concentration risk: {len(results)} subcategory-periods, "
            f"{high_count} HIGH flags"
        )
        return results

    # ─────────────────────────────────────────────────────────
    # METHOD 2: CASH CONVERSION CYCLE
    # ─────────────────────────────────────────────────────────

    def cash_conversion_cycle(self):
        """
        DSO, DIO, DPO and CCC per period.

        DEFINITIONS:
            DSO (Days Sales Outstanding)
                = Receivables / Revenue × 30
                Days to collect cash from customers after a sale.
                LOWER is better — cash arrives sooner.

            DIO (Days Inventory Outstanding)
                = Inventory / COGS × 30
                Days inventory sits before it is consumed or shipped.
                LOWER is better — cash is locked up less.

            DPO (Days Payable Outstanding)
                = Payables / COGS × 30
                Days we take before paying suppliers.
                HIGHER is better — we keep cash longer before paying out.

            CCC (Cash Conversion Cycle)
                = DSO + DIO − DPO
                Total days each rupee is unavailable to the business.
                LOWER (or negative) is better.  Negative CCC means
                suppliers are effectively financing our operations
                (Amazon, Walmart, most SaaS models achieve this).

        DATA MAPPING:
            budget_actuals: working_capital / receivables → AR proxy
            budget_actuals: working_capital / inventory   → inventory proxy
            budget_actuals: working_capital / payables    → AP proxy
            SUM of budget_actuals: revenue / *            → monthly revenue
            SUM of budget_actuals: cogs    / *            → monthly COGS

        QUERY STRUCTURE (three CTEs + final SELECT):

        CTE 1 — revenue_per_period
            Sum all revenue subcategories to one row per period.
            Used as the denominator for DSO.

        CTE 2 — cogs_per_period
            Sum all COGS lines to one row per period.
            Used as the denominator for both DIO and DPO.

        CTE 3 — wc_pivot
            CONDITIONAL AGGREGATION: pivot the three working_capital
            subcategories from three rows into three columns.
            SUM(CASE WHEN subcategory = 'x' THEN actual_amount ELSE 0 END)
            is the SQL equivalent of Excel's SUMIF / a PIVOT table:
            "sum only rows where subcategory = 'x'; treat all others
             as zero."  One table scan, handles absent subcategories
             gracefully (returns 0, not NULL).

        FINAL SELECT
            JOIN all three CTEs on period and apply the DSO/DIO/DPO/CCC
            formulas scaled to DAYS_PER_PERIOD.  NULLIF guards against
            division-by-zero if a period has zero revenue or COGS.

        CCC INTERPRETATION GUIDE:
            < 30 days  = healthy; cash cycles quickly
            30–60 days = typical for B2B businesses
            > 90 days  = working capital strain; escalate
            < 0 days   = supplier-financed model (very efficient)
        """
        query = f"""
            -- ── CTE 1: revenue_per_period ─────────────────────
            -- One row per period: total revenue from P&L.
            -- Denominator for DSO.
            WITH revenue_per_period AS (
                SELECT
                    period,
                    SUM(actual_amount) AS total_revenue
                FROM budget_actuals
                WHERE category = 'revenue'
                GROUP BY period
            ),

            -- ── CTE 2: cogs_per_period ─────────────────────────
            -- One row per period: total cost of goods sold.
            -- Denominator for DIO and DPO.
            cogs_per_period AS (
                SELECT
                    period,
                    SUM(actual_amount) AS total_cogs
                FROM budget_actuals
                WHERE category = 'cogs'
                GROUP BY period
            ),

            -- ── CTE 3: wc_pivot ────────────────────────────────
            -- Pivot working_capital from three rows per period into
            -- three columns per period using conditional aggregation.
            --
            -- Without this pivot the final SELECT would need three
            -- separate self-JOINs on budget_actuals — one per subcategory.
            -- Conditional aggregation achieves the same result in one
            -- GROUP BY scan:
            --   SUM(CASE WHEN subcategory = 'receivables'
            --            THEN actual_amount ELSE 0 END)
            -- picks only the receivables row's value; the ELSE 0 means
            -- all other working_capital rows contribute nothing to that
            -- specific SUM, so the grouped result equals just that one row.
            wc_pivot AS (
                SELECT
                    period,
                    SUM(CASE WHEN subcategory = 'receivables'
                             THEN actual_amount ELSE 0 END) AS receivables,
                    SUM(CASE WHEN subcategory = 'inventory'
                             THEN actual_amount ELSE 0 END) AS inventory,
                    SUM(CASE WHEN subcategory = 'payables'
                             THEN actual_amount ELSE 0 END) AS payables
                FROM budget_actuals
                WHERE category = 'working_capital'
                GROUP BY period
            )

            -- ── FINAL SELECT ────────────────────────────────────
            -- JOIN the three CTEs on period and apply textbook CCC
            -- formulas scaled to {DAYS_PER_PERIOD} calendar days.
            -- NULLIF prevents crashing on zero-revenue / zero-COGS
            -- periods (same guard used throughout variance_calculator.py).
            SELECT
                r.period,
                ROUND(w.receivables,   2)                              AS receivables,
                ROUND(w.inventory,     2)                              AS inventory,
                ROUND(w.payables,      2)                              AS payables,
                ROUND(r.total_revenue, 2)                              AS total_revenue,
                ROUND(c.total_cogs,    2)                              AS total_cogs,
                -- DSO: days to collect from customers
                ROUND(
                    w.receivables / NULLIF(r.total_revenue, 0)
                    * {DAYS_PER_PERIOD}, 1
                )                                                      AS dso,
                -- DIO: days inventory is held before use
                ROUND(
                    w.inventory / NULLIF(c.total_cogs, 0)
                    * {DAYS_PER_PERIOD}, 1
                )                                                      AS dio,
                -- DPO: days before we pay suppliers
                ROUND(
                    w.payables / NULLIF(c.total_cogs, 0)
                    * {DAYS_PER_PERIOD}, 1
                )                                                      AS dpo,
                -- CCC = DSO + DIO − DPO
                ROUND(
                    (  w.receivables / NULLIF(r.total_revenue, 0)
                     + w.inventory   / NULLIF(c.total_cogs,    0)
                     - w.payables    / NULLIF(c.total_cogs,    0)
                    ) * {DAYS_PER_PERIOD}, 1
                )                                                      AS ccc
            FROM revenue_per_period r
            JOIN cogs_per_period    c ON r.period = c.period
            JOIN wc_pivot           w ON r.period = w.period
            ORDER BY r.period
        """

        rows = self.db.execute_query(query)
        results = [dict(row) for row in rows]

        if results:
            # Average CCC across all periods is the headline metric
            avg_ccc = (
                sum(r["ccc"] for r in results if r["ccc"] is not None)
                / len(results)
            )
            log.info(
                f"Cash conversion cycle: {len(results)} periods, "
                f"avg CCC = {avg_ccc:.1f} days"
            )
        else:
            log.info("Cash conversion cycle: no working_capital data found")
        return results

    # ─────────────────────────────────────────────────────────
    # METHOD 3: VOLATILITY SCORING
    # ─────────────────────────────────────────────────────────

    def volatility_scoring(self):
        """
        Population standard deviation and volatility_pct per category.

        WHY MANUAL STDEV?
            SQLite has no STDEV() aggregate function (PostgreSQL and
            MySQL have it; SQLite does not).  We use the computational
            formula for population standard deviation:

                σ = SQRT( AVG(x²) − AVG(x)² )

            This is algebraically identical to the definitional form:
                SQRT( AVG( (x − mean)² ) )
            but needs only ONE GROUP BY pass — no subquery to pre-compute
            the mean, because the variance identity lets us express
            everything in terms of AVG(x) and AVG(x²).

            FINANCIAL ANALOGY: this is the σ (sigma) your calculator
            uses for population stdev, as opposed to s (sample stdev
            with Bessel's N−1 correction).  For 12 monthly observations
            the difference is < 5% — not material for FP&A purposes.

        MAX(0, ...) GUARD:
            Floating-point arithmetic can produce tiny negative values
            (e.g. −1e-12) when AVG(x²) and AVG(x)² are nearly equal.
            SQRT of a negative is undefined; MAX(0, ...) clamps these
            to zero before SQRT runs.  This is a precision guard, not
            a data correction.

        VOLATILITY_PCT (Coefficient of Variation):
            volatility_pct = σ / AVG(actual) × 100
            Normalises stdev as a % of the mean so different-scale
            categories are comparable.  Revenue (₹1.5M/month) and
            utilities (₹33K/month) can both be assessed on the same
            "how volatile?" scale.

            INTERPRETATION GUIDE:
                < 5%   = very stable   (high forecast confidence)
                5–15%  = normal range  (standard budget error)
                15–30% = elevated      (use wider forecast ranges)
                > 30%  = high          (difficult to forecast reliably)
        """
        query = """
            SELECT
                category,
                COUNT(*)                        AS data_points,
                ROUND(AVG(actual_amount), 2)    AS avg_actual,

                -- ── Manual STDEV via computational formula ──────
                -- Step 1: compute variance = AVG(x²) − AVG(x)²
                -- Step 2: MAX(0, ...) clamps floating-point negatives
                -- Step 3: SQRT(variance) = population standard deviation
                ROUND(
                    SQRT(
                        MAX(0,
                            AVG(actual_amount * actual_amount)
                            - AVG(actual_amount) * AVG(actual_amount)
                        )
                    ), 2
                )                               AS std_dev,

                -- ── Coefficient of Variation ─────────────────────
                -- Same STDEV formula, then divided by the mean × 100.
                -- NULLIF guards against division-by-zero on zero-mean
                -- categories (headcount is near zero by ₹ scale but
                -- the absolute value is already small, so the flag
                -- still surfaces the right insight).
                ROUND(
                    SQRT(
                        MAX(0,
                            AVG(actual_amount * actual_amount)
                            - AVG(actual_amount) * AVG(actual_amount)
                        )
                    ) / NULLIF(AVG(actual_amount), 0) * 100, 2
                )                               AS volatility_pct

            FROM budget_actuals
            WHERE actual_amount IS NOT NULL
            GROUP BY category
            ORDER BY volatility_pct DESC
        """

        rows = self.db.execute_query(query)
        results = [dict(row) for row in rows]

        if results:
            # Most volatile category is the headline risk signal
            top = results[0]
            log.info(
                f"Volatility scoring: {len(results)} categories, "
                f"highest = {top['category']} "
                f"({top['volatility_pct']:.1f}%)"
            )
        else:
            log.info("Volatility scoring: no data found")
        return results

    # ─────────────────────────────────────────────────────────
    # SUMMARY REPORT
    # ─────────────────────────────────────────────────────────

    def summary_report(self):
        """
        Formatted console output for all three risk analyses.

        FINANCIAL ANALOGY: the "Risk Dashboard" page in a CFO pack —
        three separate risk lenses synthesised onto one printed page,
        before the commentary and Excel report stages run.
        Each section maps to a different audience:
            [1] → Strategy / Investor Relations (revenue concentration)
            [2] → Treasury / Working Capital team (CCC days)
            [3] → FP&A / Budget team (forecast difficulty by line)
        """
        print("\n" + "=" * 65)
        print("FINANCIAL RISK ANALYSIS SUMMARY")
        print("=" * 65)

        # ── Section 1: Revenue Concentration ─────────────────
        print("\n[1] REVENUE CONCENTRATION RISK")
        print(f"  {'Period':<10} {'Subcategory':<25} {'% of Total':>12} {'Risk':>8}")
        print("  " + "-" * 58)

        conc = self.concentration_risk()
        for row in conc:
            flag = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢"}.get(
                row["concentration_risk"], "  "
            )
            print(
                f"  {row['period']:<10} {row['subcategory']:<25} "
                f"{row['pct_of_total']:>11.1f}%  "
                f"{flag} {row['concentration_risk']}"
            )

        # ── Section 2: Cash Conversion Cycle ─────────────────
        print("\n[2] CASH CONVERSION CYCLE (days per period)")
        print(f"  {'Period':<10} {'DSO':>6} {'DIO':>6} {'DPO':>6} {'CCC':>7}  Risk")
        print("  " + "-" * 48)

        ccc_rows = self.cash_conversion_cycle()
        for row in ccc_rows:
            ccc_val = row["ccc"] or 0.0
            # CCC risk thresholds align with the interpretation guide in the docstring
            if ccc_val > 90:
                ccc_flag = "🔴 HIGH"
            elif ccc_val > 60:
                ccc_flag = "🟡 MEDIUM"
            else:
                ccc_flag = "🟢 OK"
            print(
                f"  {row['period']:<10} "
                f"{(row['dso'] or 0.0):>5.1f}  "
                f"{(row['dio'] or 0.0):>5.1f}  "
                f"{(row['dpo'] or 0.0):>5.1f}  "
                f"{ccc_val:>6.1f}  {ccc_flag}"
            )

        # ── Section 3: Volatility Scoring ────────────────────
        print("\n[3] VOLATILITY SCORING (by category)")
        print(
            f"  {'Category':<20} {'Avg Actual':>14} "
            f"{'Std Dev':>12} {'Volatility %':>13}"
        )
        print("  " + "-" * 63)

        vol_rows = self.volatility_scoring()
        for row in vol_rows:
            vol_pct = row["volatility_pct"] or 0.0
            if vol_pct > 30:
                vol_flag = "🔴"
            elif vol_pct > 15:
                vol_flag = "🟡"
            else:
                vol_flag = "🟢"
            print(
                f"  {row['category']:<20} "
                f"₹{row['avg_actual']:>12,.0f}  "
                f"₹{row['std_dev']:>10,.0f}  "
                f"{vol_pct:>11.1f}%  {vol_flag}"
            )

        print("\n" + "=" * 65)


# ─────────────────────────────────────────────────────────────
# MODULE-LEVEL CONVENIENCE FUNCTIONS
#
# Thin wrappers so callers can use concentration_risk(db) directly
# without instantiating the class manually.
#
# FINANCIAL ANALOGY: individual "run this one report" buttons on
# a dashboard, alongside the "run all reports" button below.
# ─────────────────────────────────────────────────────────────

def concentration_risk(db: BudgetDatabase):
    """Revenue subcategory concentration. Returns list of dicts."""
    return FinancialRiskAnalyzer(db).concentration_risk()


def cash_conversion_cycle(db: BudgetDatabase):
    """DSO, DIO, DPO and CCC per period. Returns list of dicts."""
    return FinancialRiskAnalyzer(db).cash_conversion_cycle()


def volatility_scoring(db: BudgetDatabase):
    """Standard deviation and volatility_pct per category. Returns list of dicts."""
    return FinancialRiskAnalyzer(db).volatility_scoring()


def run_financial_risk(db: BudgetDatabase):
    """
    Run all three risk analyses and print the formatted summary report.
    Entry point called by run_pipeline.py (Stage 5/9).

    Creates a FinancialRiskAnalyzer and calls summary_report(), which
    internally runs concentration_risk, cash_conversion_cycle, and
    volatility_scoring — logging a one-line summary for each.
    """
    analyzer = FinancialRiskAnalyzer(db)
    analyzer.summary_report()


# ─────────────────────────────────────────────────────────────
# STANDALONE TEST
# Run directly with:  python financial_risk.py
# Requires budget_actuals and variance_analysis to be populated.
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    db = BudgetDatabase()
    try:
        db.connect()
        run_financial_risk(db)
        log.info("Module 3.6 complete")
    except RuntimeError as e:
        log.error(str(e))
    finally:
        db.close()

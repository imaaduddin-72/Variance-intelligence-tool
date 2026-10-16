"""
financial_risk.py
=================
Calculates revenue concentration risk, cash conversion cycle (DSO, DIO,
DPO, CCC) and per-category volatility scores.

Inputs:  budget_actuals table
Outputs: risk results returned to the pipeline and logged to pipeline.log
"""

from database import BudgetDatabase
from logger_setup import get_logger

log = get_logger(__name__)


# ─────────────────────────────────────────────────────────────
# POLICY CONSTANTS
# ─────────────────────────────────────────────────────────────
CONCENTRATION_HIGH   = 40.0   # % of total revenue — HIGH risk flag
CONCENTRATION_MEDIUM = 25.0   # %                  — MEDIUM risk flag
                               # below MEDIUM       — LOW risk
DAYS_PER_PERIOD = 30          # calendar days per accounting period


class FinancialRiskAnalyzer:
    """
    Revenue concentration, cash conversion cycle and volatility
    analyses over budget_actuals.
    """

    def __init__(self, db: BudgetDatabase):
        self.db = db

    # ─────────────────────────────────────────────────────────
    # METHOD 1: CONCENTRATION RISK
    # ─────────────────────────────────────────────────────────

    def concentration_risk(self):
        """
        Each revenue subcategory's share of total revenue per period.

        pct_of_total > CONCENTRATION_HIGH   → HIGH
        pct_of_total > CONCENTRATION_MEDIUM → MEDIUM
        otherwise                           → LOW

        Returns a list of dicts ordered by period, share descending.
        """
        query = """
            -- Total revenue per period (denominator)
            WITH revenue_totals AS (
                SELECT
                    period,
                    SUM(actual_amount) AS total_revenue
                FROM budget_actuals
                WHERE category = 'revenue'
                GROUP BY period
            ),

            -- Each revenue line with its period total
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

            -- Share of total and risk band (NULLIF avoids divide-by-zero)
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

            DSO = receivables / revenue × DAYS_PER_PERIOD
            DIO = inventory   / COGS    × DAYS_PER_PERIOD
            DPO = payables    / COGS    × DAYS_PER_PERIOD
            CCC = DSO + DIO − DPO

        Receivables, inventory and payables come from the
        working_capital subcategories; revenue and COGS are summed
        per period. Periods with zero revenue or COGS return NULL.

        Returns a list of dicts ordered by period.
        """
        query = f"""
            -- Total revenue per period (DSO denominator)
            WITH revenue_per_period AS (
                SELECT
                    period,
                    SUM(actual_amount) AS total_revenue
                FROM budget_actuals
                WHERE category = 'revenue'
                GROUP BY period
            ),

            -- Total COGS per period (DIO/DPO denominator)
            cogs_per_period AS (
                SELECT
                    period,
                    SUM(actual_amount) AS total_cogs
                FROM budget_actuals
                WHERE category = 'cogs'
                GROUP BY period
            ),

            -- Working capital lines pivoted to one column each
            -- (missing subcategories become 0)
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

            -- Day metrics scaled to {DAYS_PER_PERIOD} days per period
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
            # Average CCC across periods where it could be calculated
            ccc_values = [r["ccc"] for r in results if r["ccc"] is not None]
            avg_ccc = sum(ccc_values) / len(ccc_values) if ccc_values else None
            avg_text = f"{avg_ccc:.1f} days" if avg_ccc is not None else "n/a"
            log.info(
                f"Cash conversion cycle: {len(results)} periods, "
                f"avg CCC = {avg_text}"
            )
        else:
            log.info("Cash conversion cycle: no working_capital data found")
        return results

    # ─────────────────────────────────────────────────────────
    # METHOD 3: VOLATILITY SCORING
    # ─────────────────────────────────────────────────────────

    def volatility_scoring(self):
        """
        Month-to-month volatility of each line item's actuals.

        Measured per (category, subcategory) so that lines of different
        size within a category are not pooled together. SQLite has no
        STDEV aggregate, so population σ is computed as
        SQRT(AVG(x²) − AVG(x)²), clamped at 0 to absorb floating-point
        error. volatility_pct = σ / AVG(actual) × 100 (coefficient of
        variation), so line items of different scale are comparable.

        Returns a list of dicts ordered by volatility_pct descending.
        """
        query = """
            SELECT
                ba.category,
                ba.subcategory,
                cm.category_type,
                COUNT(*)                        AS data_points,
                ROUND(AVG(actual_amount), 2)    AS avg_actual,

                -- Population std dev: SQRT(MAX(0, AVG(x²) − AVG(x)²))
                ROUND(
                    SQRT(
                        MAX(0,
                            AVG(actual_amount * actual_amount)
                            - AVG(actual_amount) * AVG(actual_amount)
                        )
                    ), 2
                )                               AS std_dev,

                -- Coefficient of variation; NULL for zero-mean categories
                ROUND(
                    SQRT(
                        MAX(0,
                            AVG(actual_amount * actual_amount)
                            - AVG(actual_amount) * AVG(actual_amount)
                        )
                    ) / NULLIF(AVG(actual_amount), 0) * 100, 2
                )                               AS volatility_pct

            FROM budget_actuals ba
            LEFT JOIN category_metadata cm
                ON ba.category = cm.category
            WHERE actual_amount IS NOT NULL
            GROUP BY ba.category, ba.subcategory
            ORDER BY volatility_pct DESC
        """

        rows = self.db.execute_query(query)
        results = [dict(row) for row in rows]

        if results:
            # Most volatile category is the headline risk signal
            top = results[0]
            log.info(
                f"Volatility scoring: {len(results)} line items, "
                f"highest = {top['category']}/{top['subcategory']} "
                f"({(top['volatility_pct'] or 0.0):.1f}%)"
            )
        else:
            log.info("Volatility scoring: no data found")
        return results

    # ─────────────────────────────────────────────────────────
    # SUMMARY REPORT
    # ─────────────────────────────────────────────────────────

    def summary_report(self):
        """
        Run all three analyses and print them to the console.
        CCC above 90 days is flagged HIGH, above 60 MEDIUM; volatility
        above 30% is flagged red, above 15% amber.
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
        print("\n[3] VOLATILITY SCORING (month-to-month, by line item)")
        print(
            f"  {'Category':<16} {'Subcategory':<22} {'Avg Actual':>12} "
            f"{'Std Dev':>10} {'Volatility %':>13}"
        )
        print("  " + "-" * 78)

        vol_rows = self.volatility_scoring()
        for row in vol_rows:
            vol_pct = row["volatility_pct"] or 0.0
            if vol_pct > 30:
                vol_flag = "🔴"
            elif vol_pct > 15:
                vol_flag = "🟡"
            else:
                vol_flag = "🟢"
            # Headcount and other metrics are counts, not rupees
            if row["category_type"] == "metric":
                avg_text = f"{row['avg_actual']:,.1f}"
                std_text = f"{row['std_dev']:,.1f}"
            else:
                avg_text = f"₹{row['avg_actual']:,.0f}"
                std_text = f"₹{row['std_dev']:,.0f}"
            print(
                f"  {row['category']:<16} {row['subcategory']:<22} "
                f"{avg_text:>12} {std_text:>10} "
                f"{vol_pct:>12.1f}%  {vol_flag}"
            )

        print("\n" + "=" * 65)


# ─────────────────────────────────────────────────────────────
# MODULE-LEVEL WRAPPERS
# ─────────────────────────────────────────────────────────────

def concentration_risk(db: BudgetDatabase):
    """Revenue subcategory concentration. Returns list of dicts."""
    return FinancialRiskAnalyzer(db).concentration_risk()


def cash_conversion_cycle(db: BudgetDatabase):
    """DSO, DIO, DPO and CCC per period. Returns list of dicts."""
    return FinancialRiskAnalyzer(db).cash_conversion_cycle()


def volatility_scoring(db: BudgetDatabase):
    """Month-to-month std dev and volatility_pct per line item. Returns list of dicts."""
    return FinancialRiskAnalyzer(db).volatility_scoring()


def run_financial_risk(db: BudgetDatabase):
    """
    Pipeline entry point: run all three risk analyses and print
    the summary report.
    """
    analyzer = FinancialRiskAnalyzer(db)
    analyzer.summary_report()


# ─────────────────────────────────────────────────────────────
# STANDALONE RUN (requires a populated budget_actuals)
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    db = BudgetDatabase()
    try:
        db.connect()
        run_financial_risk(db)
        log.info("Financial risk analysis complete")
    except RuntimeError as e:
        log.error(str(e))
    finally:
        db.close()

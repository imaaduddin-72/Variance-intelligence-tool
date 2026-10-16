"""
variance_calculator.py
======================
Calculates budget and year-over-year variances (amount and %), applies
materiality and favorability rules per category, and assigns a variance status.

Inputs:  budget_actuals, prior_year_actuals, category_metadata tables
Outputs: variance_analysis table
"""

import config
from database import BudgetDatabase
from logger_setup import get_logger

log = get_logger(__name__)


# ─────────────────────────────────────────────────────────────
# HELPER FUNCTIONS
# ─────────────────────────────────────────────────────────────

def calc_variance(actual, budget):
    """
    Budget variance = actual minus budget.
    Positive = over budget (bad for costs, good for revenue).
    A zero budget still yields a variance, so unbudgeted spend is visible.
    """
    if actual is None or budget is None:
        return None
    return round(actual - budget, 2)


def calc_variance_pct(actual, budget):
    """
    Variance as a percentage of budget. None when budget is zero.
    """
    if actual is None or budget is None or budget == 0:
        return None
    return round(((actual - budget) / budget) * 100, 2)


def calc_yoy_variance(current, prior):
    """Year-over-year change in absolute terms."""
    if current is None or prior is None:
        return None
    return round(current - prior, 2)


def calc_yoy_pct(current, prior):
    """Year-over-year growth percentage. None when prior is zero."""
    if current is None or prior is None or prior == 0:
        return None
    return round(((current - prior) / prior) * 100, 2)


def is_material(variance, threshold):
    """
    Flag a variance whose absolute value exceeds the category's
    materiality threshold.

    Returns: 1 (material) or 0 (not material)
    """
    if variance is None or threshold is None:
        return 0
    # ABS because a -₹60K variance is just as material as +₹60K
    return 1 if abs(variance) > threshold else 0


def calc_variance_status(variance, expected_direction, budget=None):
    """
    Status used by commentary and reporting layers:
        increase (revenue):           Favorable if variance > 0, else Unfavorable
        decrease (cogs, opex):        Favorable if variance < 0, else Unfavorable
        stable (headcount, capex, working capital):
            On Plan within ±STABLE_TOLERANCE_PCT of budget, else Deviation
    A zero variance is always On Plan.
    """
    if variance is None or variance == 0:
        return "On Plan"
    if expected_direction == "stable":
        within = budget and abs(variance / budget) * 100 <= config.STABLE_TOLERANCE_PCT
        return "On Plan" if within else "Deviation"
    if expected_direction == "increase":
        return "Favorable" if variance > 0 else "Unfavorable"
    # decrease (costs)
    return "Favorable" if variance < 0 else "Unfavorable"


def is_favorable(variance_status):
    """
    1 if the status is Favorable or On Plan, else 0 - kept consistent
    with calc_variance_status so both flags always agree.
    """
    return 1 if variance_status in ("Favorable", "On Plan") else 0


# ─────────────────────────────────────────────────────────────
# MAIN CALCULATOR CLASS
# ─────────────────────────────────────────────────────────────

class VarianceCalculator:
    """
    Reads budget, prior-year and metadata tables, calculates
    variances and flags, and writes them to variance_analysis.
    """

    def __init__(self, db: BudgetDatabase):
        self.db = db

    def load_category_rules(self):
        """
        Load materiality thresholds and expected directions.
        Returns a dict keyed by category name.
        """
        rows = self.db.execute_query(
            "SELECT category, expected_direction, materiality_threshold_inr, risk_weight "
            "FROM category_metadata"
        )
        # e.g. rules["revenue"] → {expected_direction: "increase", ...}
        rules = {}
        for row in rows:
            rules[row["category"]] = {
                "expected_direction":      row["expected_direction"],
                "materiality_threshold":   row["materiality_threshold_inr"],
                "risk_weight":             row["risk_weight"],
            }
        return rules

    def calculate_all(self):
        """
        Calculate variances for every row in budget_actuals and
        rebuild variance_analysis.
        """
        log.info("Starting variance calculation...")

        rules = self.load_category_rules()
        log.info(f"Loaded rules for {len(rules)} categories")

        # Match each line to the same month one year earlier
        # ('2024-01' -> '2023-01'). LEFT JOIN keeps lines with no
        # prior-year row; their prior_year_actual is NULL.
        query = """
            SELECT
                ba.period,
                ba.category,
                ba.subcategory,
                ba.budget_amount,
                ba.actual_amount,
                py.actual_amount AS prior_year_actual
            FROM budget_actuals ba
            LEFT JOIN prior_year_actuals py
                ON ba.category   = py.category
                AND ba.subcategory = py.subcategory
                AND py.period = (CAST(SUBSTR(ba.period, 1, 4) AS INTEGER) - 1)
                || '-' || SUBSTR(ba.period, 6)
            ORDER BY ba.period, ba.category, ba.subcategory
        """

        rows = self.db.execute_query(query)
        log.info(f"Processing {len(rows)} rows...")

        # Clear previous results so we can re-run safely
        self.db.conn.execute("DELETE FROM variance_analysis")

        processed = 0
        for row in rows:
            period       = row["period"]
            category     = row["category"]
            subcategory  = row["subcategory"]
            budget       = row["budget_amount"]
            actual       = row["actual_amount"]
            prior        = row["prior_year_actual"]

            # Categories missing from metadata fall back to config defaults
            cat_rules = rules.get(category, {})
            direction  = cat_rules.get("expected_direction", config.DEFAULT_EXPECTED_DIRECTION)
            threshold  = cat_rules.get("materiality_threshold", config.DEFAULT_MATERIALITY_THRESHOLD)
            risk       = cat_rules.get("risk_weight", config.DEFAULT_RISK_WEIGHT)

            bv      = calc_variance(actual, budget)
            bv_pct  = calc_variance_pct(actual, budget)
            yoy     = calc_yoy_variance(actual, prior)
            yoy_pct = calc_yoy_pct(actual, prior)

            material  = is_material(bv, threshold)
            vstatus   = calc_variance_status(bv, direction, budget)
            favorable = is_favorable(vstatus)

            self.db.conn.execute("""
                INSERT OR REPLACE INTO variance_analysis (
                    period, category, subcategory,
                    budget_amount, actual_amount,
                    budget_variance, budget_variance_pct,
                    prior_year_actual, yoy_variance, yoy_variance_pct,
                    is_material, is_favorable, risk_level, variance_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                period, category, subcategory,
                budget, actual,
                bv, bv_pct,
                prior, yoy, yoy_pct,
                material, favorable, risk, vstatus
            ))

            processed += 1

        self.db.conn.commit()
        log.info(f"Written {processed} rows to variance_analysis")

    def summary_report(self):
        """
        Print headline counts and top variances after calculation.
        """
        print("\n" + "=" * 65)
        print("VARIANCE ANALYSIS SUMMARY")
        print("=" * 65)

        # ── Total rows written ────────────────────────────────
        count = self.db.execute_query(
            "SELECT COUNT(*) as n FROM variance_analysis"
        )[0]["n"]
        print(f"\nTotal rows calculated: {count:,}")

        # ── Material variances by category ────────────────────
        print("\nMaterial variances by category (needs attention):")
        print(f"  {'Category':<20} {'Material Items':>15} {'Risk':>8}")
        print("  " + "-" * 45)

        rows = self.db.execute_query("""
            SELECT category, COUNT(*) as cnt, risk_level
            FROM variance_analysis
            WHERE is_material = 1
            GROUP BY category, risk_level
            ORDER BY CASE risk_level WHEN 'HIGH' THEN 1 WHEN 'MEDIUM' THEN 2 ELSE 3 END,
                     cnt DESC
        """)

        for row in rows:
            flag = "🔴" if row["risk_level"] == "HIGH" else "🟡"
            print(f"  {row['category']:<20} {row['cnt']:>15,}  {flag} {row['risk_level']}")

        # ── Variance status breakdown ─────────────────────────
        print("\nVariance status split:")
        split = self.db.execute_query("""
            SELECT variance_status, COUNT(*) as n
            FROM variance_analysis
            GROUP BY variance_status
            ORDER BY CASE variance_status
                WHEN 'Favorable' THEN 1 WHEN 'On Plan' THEN 2
                WHEN 'Unfavorable' THEN 3 ELSE 4 END
        """)
        for row in split:
            print(f"  {row['variance_status']:<20} {row['n']:>6,} rows")

        # ── Biggest budget misses ─────────────────────────────
        print("\nTop 5 biggest unfavorable budget variances (all periods):")
        print(f"  {'Period':<10} {'Subcategory':<25} {'Variance':>15} {'Risk':>8}")
        print("  " + "-" * 62)

        top_misses = self.db.execute_query("""
            SELECT period, subcategory, budget_variance, risk_level
            FROM variance_analysis
            WHERE variance_status = 'Unfavorable'
            ORDER BY ABS(budget_variance) DESC
            LIMIT 5
        """)

        for row in top_misses:
            flag = "🔴" if row["risk_level"] == "HIGH" else "🟡"
            print(f"  {row['period']:<10} {row['subcategory']:<25} "
                  f"₹{row['budget_variance']:>13,.0f}  {flag}")

        # ── Best YoY performers ───────────────────────────────
        print("\nTop 5 YoY growth leaders (current vs prior year):")
        print(f"  {'Period':<10} {'Subcategory':<25} {'YoY %':>10}")
        print("  " + "-" * 48)

        top_yoy = self.db.execute_query("""
            SELECT period, subcategory, yoy_variance_pct
            FROM variance_analysis
            WHERE category = 'revenue'
              AND yoy_variance_pct IS NOT NULL
            ORDER BY yoy_variance_pct DESC
            LIMIT 5
        """)

        for row in top_yoy:
            print(f"  {row['period']:<10} {row['subcategory']:<25} "
                  f"{row['yoy_variance_pct']:>9.1f}%")

        print("\n" + "=" * 65)


# ─────────────────────────────────────────────────────────────
# STANDALONE RUN
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    db = BudgetDatabase()
    try:
        db.connect()
        calc = VarianceCalculator(db)
        calc.calculate_all()
        calc.summary_report()
        log.info("Variance calculation complete")
    finally:
        db.close()

"""
Module 2: variance_calculator.py
=================================

PURPOSE: Calculate budget and year-over-year variances, apply business
         logic flags, and write results into the variance_analysis table.

FINANCIAL ANALOGY:
    Module 1 = Built the general ledger system (data storage)
    Module 2 = The variance analysis engine (automated analyst)

WHAT THIS MODULE DOES, STEP BY STEP:
    1. Pull every row from budget_actuals
    2. For each row, look up the same row in prior_year_actuals
    3. Look up the category's business rules in category_metadata
    4. Calculate:
        - budget_variance       = actual - budget (₹)
        - budget_variance_pct   = variance / budget × 100
        - yoy_variance          = current actual - prior year actual (₹)
        - yoy_variance_pct      = yoy_variance / prior actual × 100
    5. Apply business logic:
        - is_material?  abs(variance) > materiality threshold
        - is_favorable? depends on category type (revenue up = good,
                        cost up = bad)
    6. Write all of this into variance_analysis table
"""

import config
from database import BudgetDatabase


# ─────────────────────────────────────────────────────────────
# HELPER FUNCTIONS
# Each does ONE clearly-named job.
# Financial analogy: like named formulas in Excel (but reusable)
# ─────────────────────────────────────────────────────────────

def calc_variance(actual, budget):
    """
    Budget variance = actual minus budget.
    Positive = over budget (bad for costs, good for revenue).
    """
    if budget is None or budget == 0:
        return None
    return round(actual - budget, 2)


def calc_variance_pct(actual, budget):
    """
    Variance as a percentage of budget.
    Financial analogy: the '% variance' column in every board pack.
    """
    if budget is None or budget == 0:
        return None
    return round(((actual - budget) / budget) * 100, 2)


def calc_yoy_variance(current, prior):
    """Year-over-year change in absolute terms."""
    if prior is None or prior == 0:
        return None
    return round(current - prior, 2)


def calc_yoy_pct(current, prior):
    """Year-over-year growth percentage."""
    if prior is None or prior == 0:
        return None
    return round(((current - prior) / prior) * 100, 2)


def is_material(variance, threshold):
    """
    Should this variance be flagged for review?

    FINANCIAL ANALOGY: The materiality threshold in your audit policy.
    A ₹500 variance in a ₹10M revenue line isn't worth a CFO's time.
    A ₹500 variance in a ₹2000 utilities budget IS worth flagging.

    Returns: 1 (flag it) or 0 (ignore it)
    """
    if variance is None or threshold is None:
        return 0
    # ABS because a -₹60K variance is just as material as +₹60K
    return 1 if abs(variance) > threshold else 0


def calc_variance_status(variance, expected_direction, budget=None):
    """
    Human-readable status used by commentary and reporting layers.
    Stable categories (headcount, working_capital) get "Deviation" /
    "On Plan" instead of "Favorable" / "Unfavorable" because a
    headcount difference isn't inherently good or bad.
    """
    if variance is None:
        return "On Plan"
    if expected_direction == "stable":
        return "On Plan" if budget and abs(variance / budget) * 100 <= config.STABLE_TOLERANCE_PCT else "Deviation"
    if expected_direction == "increase":
        return "Favorable" if variance > 0 else "Unfavorable"
    # decrease (costs)
    return "Favorable" if variance < 0 else "Unfavorable"


def is_favorable(variance, expected_direction):
    """
    Is this variance GOOD or BAD?

    This is where business logic matters. The same sign means different
    things for different categories:

    REVENUE (expected: increase)
        +₹50K variance = came in ABOVE target = FAVORABLE ✓
        -₹50K variance = missed target = UNFAVORABLE ✗

    COSTS: opex, cogs, capex (expected: decrease)
        -₹50K variance = spent LESS than budget = FAVORABLE ✓
        +₹50K variance = overspent = UNFAVORABLE ✗

    HEADCOUNT, WORKING CAPITAL (expected: stable)
        Any variance = worth investigating, not simply good/bad

    Returns: 1 (favorable) or 0 (unfavorable)
    """
    if variance is None:
        return 0

    if expected_direction == "increase":
        # Revenue: positive variance = good
        return 1 if variance > 0 else 0

    elif expected_direction == "decrease":
        # Costs: negative variance = good (spent less)
        return 1 if variance < 0 else 0

    else:
        # Stable (headcount, working capital): flag anything non-zero
        return 1 if variance == 0 else 0


# ─────────────────────────────────────────────────────────────
# MAIN CALCULATOR CLASS
# ─────────────────────────────────────────────────────────────

class VarianceCalculator:
    """
    Reads raw data, calculates variances, writes results.

    FINANCIAL ANALOGY: Like a senior analyst who:
      - Opens the budget vs actual report
      - Pulls last year's numbers alongside
      - Applies the materiality policy
      - Flags items that need attention
      - Saves the annotated output
    """

    def __init__(self, db: BudgetDatabase):
        self.db = db

    def load_category_rules(self):
        """
        Load materiality thresholds and expected directions.
        Returns a dict keyed by category name for fast lookups.

        FINANCIAL ANALOGY: Loading your company's accounting policies —
        the rulebook you apply when reviewing variances.
        """
        rows = self.db.execute_query(
            "SELECT category, expected_direction, materiality_threshold_inr, risk_weight "
            "FROM category_metadata"
        )
        # Build a dict so we can look up rules by category in O(1)
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
        Main entry point. Processes every row in budget_actuals.

        WHAT HAPPENS:
        1. Load category rules (once — efficient)
        2. For each budget row, JOIN to prior year and metadata
        3. Calculate all metrics
        4. Insert into variance_analysis
        """
        print("Starting variance calculation...")

        rules = self.load_category_rules()
        print(f"  Loaded rules for {len(rules)} categories")

        # ── SQL EXPLANATION ───────────────────────────────────
        # This query does a LEFT JOIN between budget_actuals and
        # prior_year_actuals.
        #
        # LEFT JOIN means: "give me every row from budget_actuals,
        # and if there's a matching prior year row, include it.
        # If not, give me NULL for prior year columns."
        #
        # The SUBSTR trick: periods are '2024-01' and '2023-01'.
        # SUBSTR(period, 6) extracts just '01' (the month part),
        # so January 2024 matches January 2023.
        #
        # FINANCIAL ANALOGY: This is the comparative P&L query:
        # "Show me this year's actuals alongside last year's."
        # ──────────────────────────────────────────────────────
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
        print(f"  Processing {len(rows)} rows...")

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

            # Get this category's rules (or empty dict if missing)
            cat_rules = rules.get(category, {})
            direction  = cat_rules.get("expected_direction", "stable")
            threshold  = cat_rules.get("materiality_threshold", 0)
            risk       = cat_rules.get("risk_weight", "LOW")

            # Calculate all metrics using our helper functions
            bv      = calc_variance(actual, budget)
            bv_pct  = calc_variance_pct(actual, budget)
            yoy     = calc_yoy_variance(actual, prior) if prior else None
            yoy_pct = calc_yoy_pct(actual, prior)      if prior else None

            material  = is_material(bv, threshold)
            favorable = is_favorable(bv, direction)
            vstatus   = calc_variance_status(bv, direction, budget)

            # ── INSERT EXPLANATION ────────────────────────────
            # INSERT OR REPLACE handles re-runs cleanly.
            # If a row for this period+category+subcategory exists,
            # it replaces it. Otherwise it inserts new.
            #
            # The ? placeholders prevent SQL injection —
            # Python fills them in safely from the tuple below.
            # ──────────────────────────────────────────────────
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
        print(f"  ✓ Written {processed} rows to variance_analysis")

    def summary_report(self):
        """
        Quick sanity check after calculation.

        FINANCIAL ANALOGY: The cover page of your variance report —
        headline numbers before you dive into the detail.
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
            ORDER BY risk_level DESC, cnt DESC
        """)

        for row in rows:
            flag = "🔴" if row["risk_level"] == "HIGH" else "🟡"
            print(f"  {row['category']:<20} {row['cnt']:>15,}  {flag} {row['risk_level']}")

        # ── Favorable vs Unfavorable breakdown ────────────────
        print("\nFavorable vs unfavorable split:")
        fav = self.db.execute_query(
            "SELECT is_favorable, COUNT(*) as n FROM variance_analysis GROUP BY is_favorable"
        )
        for row in fav:
            label = "Favorable ✓" if row["is_favorable"] else "Unfavorable ✗"
            print(f"  {label:<20} {row['n']:>6,} rows")

        # ── Biggest budget misses ─────────────────────────────
        print("\nTop 5 biggest unfavorable budget variances (all periods):")
        print(f"  {'Period':<10} {'Subcategory':<25} {'Variance':>15} {'Risk':>8}")
        print("  " + "-" * 62)

        top_misses = self.db.execute_query("""
            SELECT period, subcategory, budget_variance, risk_level
            FROM variance_analysis
            WHERE is_favorable = 0
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
# RUN MODULE 2
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    db = BudgetDatabase("budget_analysis.db")
    db.connect()

    calc = VarianceCalculator(db)
    calc.calculate_all()
    calc.summary_report()

    db.close()
    print("\n✓ Module 2 complete")

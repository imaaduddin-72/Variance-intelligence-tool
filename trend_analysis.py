"""
trend_analysis.py
=================
Finds the current streak of consecutive months with the same variance status
for each category/subcategory, and flags 3+ month Unfavorable or Deviation
streaks as chronic.

Inputs:  variance_analysis table
Outputs: trend_analysis table
"""

import config
from database import BudgetDatabase
from logger_setup import get_logger

log = get_logger(__name__)


# ─────────────────────────────────────────────────────────────
# POLICY CONSTANT
# Consecutive adverse months before a streak is flagged as chronic.
# ─────────────────────────────────────────────────────────────
CHRONIC_THRESHOLD = 3


class TrendAnalyzer:
    """
    Identifies the current variance streak for every (category,
    subcategory) line item. A streak is the run of consecutive
    periods ending at the latest period that share the same
    variance_status; its length and status are written to
    trend_analysis.
    """

    def __init__(self, db: BudgetDatabase):
        self.db = db

    def calculate_streaks(self):
        """
        Compute current streaks and rebuild trend_analysis.
        Returns the number of rows written.
        """
        self.db.conn.execute("DELETE FROM trend_analysis")

        # CHRONIC_THRESHOLD is a module constant, so the f-string
        # interpolation below is safe.
        self.db.conn.execute(f"""
            INSERT INTO trend_analysis (
                category, subcategory, current_status,
                streak_length, is_chronic, latest_period, streak_direction
            )

            -- ordered: previous period's status per line item
            -- ('YYYY-MM' sorts chronologically; NULL for the first period)
            WITH ordered AS (
                SELECT
                    category,
                    subcategory,
                    period,
                    variance_status,
                    LAG(variance_status) OVER (
                        PARTITION BY category, subcategory
                        ORDER BY period
                    ) AS prev_status
                FROM variance_analysis
            ),

            -- boundaries: 1 where a new run starts (first period or
            -- status changed), else 0
            boundaries AS (
                SELECT
                    category,
                    subcategory,
                    period,
                    variance_status,
                    CASE
                        WHEN prev_status IS NULL
                          OR variance_status != prev_status
                        THEN 1
                        ELSE 0
                    END AS is_boundary
                FROM ordered
            ),

            -- islands: running sum of boundaries gives every row in
            -- the same run a shared island_id
            islands AS (
                SELECT
                    category,
                    subcategory,
                    period,
                    variance_status,
                    SUM(is_boundary) OVER (
                        PARTITION BY category, subcategory
                        ORDER BY period
                    ) AS island_id
                FROM boundaries
            ),

            -- latest_periods: most recent period per line item
            latest_periods AS (
                SELECT
                    category,
                    subcategory,
                    MAX(period) AS latest_period
                FROM islands
                GROUP BY category, subcategory
            ),

            -- current_island: island_id of the latest period, i.e. the
            -- current streak
            current_island AS (
                SELECT
                    i.category,
                    i.subcategory,
                    i.island_id   AS current_island_id,
                    lp.latest_period
                FROM islands i
                JOIN latest_periods lp
                    ON  i.category    = lp.category
                    AND i.subcategory = lp.subcategory
                    AND i.period      = lp.latest_period
            )

            -- streak_length: rows in the current island
            -- is_chronic: streak of {CHRONIC_THRESHOLD}+ months that is
            --   Unfavorable or Deviation
            -- streak_direction: 'Over' if actual > budget in every month
            --   of the streak, 'Under' if below in every month, else 'Mixed'
            SELECT
                i.category,
                i.subcategory,
                i.variance_status                              AS current_status,
                COUNT(*)                                       AS streak_length,
                CASE
                    WHEN COUNT(*) >= {CHRONIC_THRESHOLD}
                     AND i.variance_status IN ('Unfavorable', 'Deviation')
                    THEN 1
                    ELSE 0
                END                                            AS is_chronic,
                ci.latest_period,
                CASE
                    WHEN MIN(va.budget_variance) > 0 THEN 'Over'
                    WHEN MAX(va.budget_variance) < 0 THEN 'Under'
                    ELSE 'Mixed'
                END                                            AS streak_direction
            FROM islands i
            JOIN current_island ci
                ON  i.category    = ci.category
                AND i.subcategory = ci.subcategory
                AND i.island_id   = ci.current_island_id
            JOIN variance_analysis va
                ON  i.category    = va.category
                AND i.subcategory = va.subcategory
                AND i.period      = va.period
            GROUP BY
                i.category,
                i.subcategory,
                i.variance_status,
                ci.latest_period
        """)

        self.db.conn.commit()

        n = self.db.execute_query(
            "SELECT COUNT(*) AS n FROM trend_analysis"
        )[0]["n"]
        log.info(f"Trend streaks computed: {n} line items written")
        return n

    def summary_report(self):
        """
        Print chronic issues and streak distribution to the terminal.
        """
        print("\n" + "=" * 65)
        print("TREND ANALYSIS - STREAK SUMMARY")
        print("=" * 65)

        total = self.db.execute_query(
            "SELECT COUNT(*) AS n FROM trend_analysis"
        )[0]["n"]
        print(f"\nLine items tracked: {total}")

        # ── Chronic issues (longest streak first) ─────────────
        chronic = self.db.execute_query("""
            SELECT category, subcategory, current_status,
                   streak_length, latest_period, streak_direction
            FROM trend_analysis
            WHERE is_chronic = 1
            ORDER BY streak_length DESC, category, subcategory
        """)

        print(f"\nChronic issues ({CHRONIC_THRESHOLD}+ consecutive months "
              f"Unfavorable or Deviation): {len(chronic)}")

        if chronic:
            print(f"\n  {'Category':<14} {'Subcategory':<25} "
                  f"{'Status':<12} {'Streak':>6}  {'Direction':<9}  {'Through'}")
            print("  " + "-" * 82)
            for row in chronic:
                flag = "🔴" if row["current_status"] == "Unfavorable" else "🟡"
                direction = row["streak_direction"] or "N/A"
                dir_flag = {"Over": "▲", "Under": "▼", "Mixed": "~"}.get(direction, "")
                print(f"  {row['category']:<14} {row['subcategory']:<25} "
                      f"{flag} {row['current_status']:<12} "
                      f"{row['streak_length']:>4} mo  "
                      f"{dir_flag} {direction:<8}  {row['latest_period']}")
        else:
            print("  None.")

        # ── Streak distribution by current status ─────────────
        print("\nCurrent streak distribution (all line items):")
        dist = self.db.execute_query("""
            SELECT
                current_status,
                COUNT(*)                     AS items,
                ROUND(AVG(streak_length), 1) AS avg_streak,
                MAX(streak_length)           AS max_streak
            FROM trend_analysis
            GROUP BY current_status
            ORDER BY items DESC
        """)

        print(f"\n  {'Status':<14} {'Items':>6}  {'Avg Streak':>11}  "
              f"{'Max Streak':>10}")
        print("  " + "-" * 47)
        for row in dist:
            print(f"  {row['current_status']:<14} {row['items']:>6}  "
                  f"{row['avg_streak']:>10.1f}  {row['max_streak']:>10}")

        print("\n" + "=" * 65)
        return chronic


# ─────────────────────────────────────────────────────────────
# PIPELINE ENTRY POINT
# ─────────────────────────────────────────────────────────────

def run_trend_stage(db: BudgetDatabase):
    """Convenience wrapper for run_pipeline.py."""
    n = db.execute_query(
        "SELECT COUNT(*) AS n FROM variance_analysis"
    )[0]["n"]
    if n == 0:
        raise RuntimeError(
            "variance_analysis is empty - run variance_calculator.py first.")

    analyzer = TrendAnalyzer(db)
    analyzer.calculate_streaks()
    analyzer.summary_report()


# ─────────────────────────────────────────────────────────────
# STANDALONE RUN (requires a populated variance_analysis)
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    db = BudgetDatabase()
    try:
        db.connect()
        run_trend_stage(db)
        log.info("Trend analysis complete")
    except RuntimeError as e:
        log.error(str(e))
    finally:
        db.close()

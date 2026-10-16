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
#
# How many consecutive adverse months before a streak is "chronic".
# Stored here (not in config.py) because it's internal business
# logic for this module only.
#
# FINANCIAL ANALOGY: a credit-risk policy rule - "flag any account
# with 3+ consecutive late payments for escalation review."
# ─────────────────────────────────────────────────────────────
CHRONIC_THRESHOLD = 3


class TrendAnalyzer:
    """
    Identifies the current variance streak for every (category,
    subcategory) line item using the SQL gaps-and-islands pattern.

    GAPS AND ISLANDS - THE FULL CONCEPT:
        Imagine 12 months of variance_status for one line item:

          Jan   Feb   Mar   Apr   May   Jun   Jul   Aug   Sep   Oct   Nov   Dec
          Unf   Unf   Fav   Fav   Fav   Unf   Unf   Unf   Unf   Unf   Fav   Fav
          |-- island 1 --|  |--- island 2 ---|  |------ island 3 -----|  |- 4 -|

        We want island 4 (the CURRENT streak ending at Dec), its
        length (2 months), and its status (Favorable) - without
        storing historical islands, using only SQL.

    HOW THE RUNNING-SUM TRICK WORKS (step by step):
        Step 1  LAG()       - for each row, peek at last month's status.
        Step 2  is_boundary - flag rows where the status CHANGED (= 1)
                              or stayed the same (= 0).
        Step 3  SUM(OVER)   - running cumulative sum of boundary flags.
                              Stays flat inside a run, increments at each
                              boundary → every row in a run shares the
                              same integer (island_id).
        Step 4  latest_period - find the most recent month per line item.
        Step 5  current_island - look up WHICH island_id that month has.
        Step 6  COUNT(*)    - count all rows with that island_id
                              = the current streak length.
    """

    def __init__(self, db: BudgetDatabase):
        self.db = db

    def calculate_streaks(self):
        """
        Run the gaps-and-islands query and populate trend_analysis.

        Structured as five CTEs so each logical step is readable
        on its own.  The final SELECT aggregates the current island
        into one output row per (category, subcategory).

        Returns the number of rows written.
        """
        self.db.conn.execute("DELETE FROM trend_analysis")

        # ── INSERT ... WITH ... SELECT ────────────────────────
        #
        # SQLite supports CTEs directly inside an INSERT statement,
        # so we compute and write in a single round-trip to the DB.
        # The f-string embeds CHRONIC_THRESHOLD (a module constant
        # integer, not user input) so there is no injection risk.
        # ─────────────────────────────────────────────────────
        self.db.conn.execute(f"""
            INSERT INTO trend_analysis (
                category, subcategory, current_status,
                streak_length, is_chronic, latest_period, streak_direction
            )

            -- ── CTE 1: ordered ──────────────────────────────────
            -- Attach the PREVIOUS period's variance_status to each row
            -- using LAG().
            --
            -- LAG(variance_status) OVER (PARTITION BY ... ORDER BY ...)
            --
            --   PARTITION BY category, subcategory
            --       Each line item gets its own independent window.
            --       (revenue, product_sales) January's "previous row"
            --       is (revenue, product_sales) December, NOT the row
            --       above it belonging to a different subcategory.
            --
            --   ORDER BY period
            --       Defines the sequence within each partition.
            --       'YYYY-MM' text sorts identically to date order,
            --       so this is chronological without a CAST.
            --
            --   For the FIRST row in each partition (earliest period),
            --   no previous row exists, so LAG() returns NULL.
            --
            -- FINANCIAL ANALOGY: a comparative P&L that automatically
            -- shows "last month's value" in a column next to each row,
            -- for every line item simultaneously.
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

            -- ── CTE 2: boundaries ────────────────────────────────
            -- Mark rows where the variance_status CHANGED versus
            -- the previous month.  These boundary flags drive the
            -- island grouping in CTE 3.
            --
            --   is_boundary = 1  →  a new island starts here
            --   is_boundary = 0  →  same island continues
            --
            -- Two conditions produce is_boundary = 1:
            --   A) prev_status IS NULL  (first-ever period for this
            --      line item; nothing to compare against, so it
            --      starts its own island by definition)
            --   B) variance_status != prev_status  (status flipped)
            --
            -- FINANCIAL ANALOGY: imagine marking the first month of
            -- each new trend on a KPI dashboard.
            --   Jan Unfav (new → flag=1), Feb Unfav (same → 0),
            --   Mar Fav   (new → flag=1), Apr Fav   (same → 0).
            -- The flags mark where each "run" begins.
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

            -- ── CTE 3: islands ───────────────────────────────────
            -- Convert the boundary flags into island group IDs using
            -- a running SUM() window function.
            --
            -- SUM(is_boundary) OVER (PARTITION BY ... ORDER BY period)
            --
            --   The cumulative sum increments by 1 at each boundary,
            --   then stays flat until the next boundary.  Rows sharing
            --   the same cumulative sum belong to the same island.
            --
            -- Concrete trace for one line item:
            --
            --   Period    Status        is_boundary  island_id (running sum)
            --   2024-01   Unfavorable       1            1   <- boundary, sum +1
            --   2024-02   Unfavorable       0            1   <- flat, same island
            --   2024-03   Favorable         1            2   <- boundary, sum +1
            --   2024-04   Favorable         0            2   <- flat, same island
            --   2024-05   Unfavorable       1            3   <- boundary, sum +1
            --   2024-06   Unfavorable       0            3   <- flat, same island
            --   2024-07   Unfavorable       0            3   <- flat, same island
            --
            --   Island 3 now has three rows.  COUNT(*) WHERE island_id=3
            --   gives the streak length: 3.
            --
            -- FINANCIAL ANALOGY: numbering each distinct "trend period"
            -- on a rolling forecast.  Trend #1 = early growth phase,
            -- Trend #2 = mid-year dip, Trend #3 = recovery.  All months
            -- in Trend #3 share the number 3 no matter how long it runs.
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

            -- ── CTE 4: latest_periods ────────────────────────────
            -- Find the most recent period for each (category,
            -- subcategory).  This anchors "current streak" - we want
            -- the island that ENDS at the latest month, not islands
            -- from months past.
            --
            -- MAX(period) works correctly because 'YYYY-MM' text sorts
            -- in the same order as calendar dates.
            --
            -- FINANCIAL ANALOGY: the "as-of date" of the most recent
            -- month-end close - the reference point for what "current"
            -- means when we report.
            latest_periods AS (
                SELECT
                    category,
                    subcategory,
                    MAX(period) AS latest_period
                FROM islands
                GROUP BY category, subcategory
            ),

            -- ── CTE 5: current_island ────────────────────────────
            -- Join islands back to latest_periods to identify which
            -- island_id the most recent period belongs to.
            --
            -- Joining on:
            --   category + subcategory  (same line item)
            --   period = latest_period  (pick only the latest row)
            --
            -- This produces ONE row per (category, subcategory),
            -- carrying the island_id of the current streak.  The
            -- final SELECT uses this island_id to filter islands
            -- down to only the current streak's rows before counting.
            --
            -- FINANCIAL ANALOGY: looking up which trend number the
            -- current month belongs to, so you know which rows to
            -- count when you ask "how long has this trend been running?"
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

            -- ── FINAL SELECT: count the current island's rows ────
            -- Join ALL islands rows to current_island, keeping only
            -- those whose island_id matches the current streak's ID.
            -- COUNT(*) on that filtered set = streak length.
            --
            -- is_chronic:
            --     1 if the streak is BOTH long enough ({CHRONIC_THRESHOLD}+
            --     months) AND an adverse status (Unfavorable or Deviation).
            --     A long Favorable streak is good news, not a flag.
            --     A two-month Unfavorable run is a watch item, not yet chronic.
            --
            -- GROUP BY i.variance_status is safe here because every row
            -- in the current island shares the same status by definition
            -- (it's what made them a single island in the first place).
            --
            -- FINANCIAL ANALOGY: the chronic flag is a credit-risk rule -
            -- "escalate any line item that has missed its target for
            -- {CHRONIC_THRESHOLD} or more consecutive months."
            -- ── streak_direction ─────────────────────────────────
            -- For each period in the current island, budget_variance
            -- (actual - budget) tells us direction:
            --   > 0  →  actual exceeded budget  (Over)
            --   < 0  →  actual was below budget (Under)
            -- MIN/MAX across all island rows detects mixing:
            --   all positive → MIN > 0                  → 'Over'
            --   all negative → MAX < 0                  → 'Under'
            --   otherwise                               → 'Mixed'
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

        FINANCIAL ANALOGY: the "persistent issues" section of a
        monthly CFO memo - items that have appeared on the exceptions
        list for multiple months running and need escalation, not
        just routine monitoring.
        """
        print("\n" + "=" * 65)
        print("TREND ANALYSIS - STREAK SUMMARY")
        print("=" * 65)

        total = self.db.execute_query(
            "SELECT COUNT(*) AS n FROM trend_analysis"
        )[0]["n"]
        print(f"\nLine items tracked: {total}")

        # ── Chronic issues ────────────────────────────────────
        # Sorted longest streak first so the most entrenched problems
        # appear at the top - the same order a CFO expects.
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

        # ── Streak distribution ───────────────────────────────
        # Shows how statuses are spread across all line items at
        # the current moment - the "state of the portfolio" view.
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
# Called by run_pipeline.py - mirrors run_snapshot_stage() pattern.
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
# STANDALONE TEST
# Run directly with:  python trend_analysis.py
# Requires variance_analysis to have been populated first.
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    db = BudgetDatabase()
    try:
        db.connect()
        run_trend_stage(db)
        log.info("Module 5 complete")
    except RuntimeError as e:
        log.error(str(e))
    finally:
        db.close()

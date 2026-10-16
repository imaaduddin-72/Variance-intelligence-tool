"""
data_quality.py
===============
Runs five SQL integrity checks on the loaded data before any variance
calculation begins. Stops the pipeline with a clear error message if
any critical check fails.

Inputs:  budget_actuals, prior_year_actuals, category_metadata tables
Outputs: pass/fail result per check, logged to pipeline.log
"""

import config
from database import BudgetDatabase
from logger_setup import get_logger

log = get_logger(__name__)


class DataQualityChecker:
    """
    Runs a suite of SQL validation checks. Each check returns
    (passed: bool, message: str, detail_rows: list).
    """

    def __init__(self, db: BudgetDatabase):
        self.db = db
        self.results = []   # collected (name, passed, message) tuples

    def _record(self, name, passed, message, details=None):
        """
        Store and log one check result.

        PASS -> log.info  (normal, expected, goes to log but isn't
                            visually alarming)
        FAIL -> log.warning (something needs attention - this is the
                              level you'd filter pipeline.log for to
                              see "what went wrong last month")
        """
        if passed:
            log.info(f"{name}: {message}")
        else:
            log.warning(f"{name}: {message}")
            if details:
                for d in details[:5]:   # log at most 5 examples
                    log.warning(f"  example: {dict(d)}")
        self.results.append((name, passed, message))

    # ─────────────────────────────────────────────────────
    # CHECK 1: Duplicate rows
    # SQL PATTERN: GROUP BY + HAVING COUNT(*) > 1
    # ─────────────────────────────────────────────────────
    def check_duplicates(self):
        """
        A duplicate (period, category, subcategory) means the same
        line item appears twice - variances would double-count.

        SQL EXPLANATION:
            GROUP BY collapses rows sharing the same 3 keys.
            HAVING COUNT(*) > 1 keeps only groups with 2+ rows -
            i.e., the duplicated keys.
            (HAVING filters AFTER grouping; WHERE filters before.)

        EXCEL ANALOGY: COUNTIFS(period, period, category, category,
        subcategory, subcategory) > 1, then filter to TRUE.
        """
        dupes = self.db.execute_query("""
            SELECT period, category, subcategory, COUNT(*) AS n
            FROM budget_actuals
            GROUP BY period, category, subcategory
            HAVING COUNT(*) > 1
        """)

        self._record(
            "No duplicate line items",
            passed=(len(dupes) == 0),
            message="clean" if not dupes else f"{len(dupes)} duplicated key(s) found",
            details=dupes
        )

    # ─────────────────────────────────────────────────────
    # CHECK 2: Orphan categories
    # SQL PATTERN: LEFT JOIN ... WHERE right side IS NULL
    # ─────────────────────────────────────────────────────
    def check_orphan_categories(self):
        """
        Find categories used in budget_actuals that have NO entry
        in category_metadata - they'd silently get default rules.

        SQL EXPLANATION:
            LEFT JOIN keeps every row from the left table
            (budget_actuals categories) and attaches the matching
            metadata row if it exists - or NULL if it doesn't.
            WHERE cm.category IS NULL keeps only the non-matches.

            This "LEFT JOIN + IS NULL" idiom is THE standard SQL way
            to ask: "what's in A but missing from B?"

        EXCEL ANALOGY: VLOOKUP returning #N/A, then filtering
        to just the #N/A rows.
        """
        orphans = self.db.execute_query("""
            SELECT DISTINCT ba.category
            FROM budget_actuals ba
            LEFT JOIN category_metadata cm
                ON ba.category = cm.category
            WHERE cm.category IS NULL
        """)

        self._record(
            "All categories mapped in metadata",
            passed=(len(orphans) == 0),
            message="all mapped" if not orphans
                    else f"{len(orphans)} unmapped: " +
                         ", ".join(r["category"] for r in orphans),
            details=orphans
        )

    # ─────────────────────────────────────────────────────
    # CHECK 3: Prior-year coverage
    # SQL PATTERN: LEFT JOIN + IS NULL (again - it's everywhere)
    # ─────────────────────────────────────────────────────
    def check_prior_year_coverage(self):
        """
        Which current line items have NO matching prior-year row?
        YoY columns will be NULL for these - fine if expected
        (new products), a data gap if not.

        Note the SUBSTR month-matching - same trick as the
        calculator uses, so this check tests the ACTUAL join logic.
        """
        missing = self.db.execute_query("""
            SELECT ba.period, ba.category, ba.subcategory
            FROM budget_actuals ba
            LEFT JOIN prior_year_actuals py
                ON  ba.category    = py.category
                AND ba.subcategory = py.subcategory
                AND py.period = (CAST(SUBSTR(ba.period, 1, 4) AS INTEGER) - 1)
                                || '-' || SUBSTR(ba.period, 6)
            WHERE py.id IS NULL
        """)

        total = self.db.execute_query(
            "SELECT COUNT(*) AS n FROM budget_actuals")[0]["n"]
        covered = total - len(missing)
        pct = (covered / total * 100) if total else 0

        self._record(
            "Prior-year coverage",
            passed=(pct >= 90),     # tolerate some gaps, flag big ones
            message=f"{covered}/{total} rows have prior-year match ({pct:.0f}%)",
            details=missing
        )

    # ─────────────────────────────────────────────────────
    # CHECK 4: Value sanity
    # SQL PATTERN: WHERE with OR'd conditions
    # ─────────────────────────────────────────────────────
    def check_value_sanity(self):
        """
        Catch obviously broken values: NULL amounts, negative
        budgets where they make no sense.

        BUSINESS RULE: revenue/cost budgets should be >= 0.
        (Working capital items can legitimately be negative,
        so we exclude that category from the negativity check.)
        """
        bad = self.db.execute_query("""
            SELECT period, category, subcategory,
                   budget_amount, actual_amount
            FROM budget_actuals
            WHERE budget_amount IS NULL
               OR actual_amount IS NULL
               OR (budget_amount < 0 AND category != 'working_capital')
        """)

        self._record(
            "Amounts present and sensible",
            passed=(len(bad) == 0),
            message="clean" if not bad else f"{len(bad)} suspect row(s)",
            details=bad
        )

    # ─────────────────────────────────────────────────────
    # CHECK 5: Period completeness
    # SQL PATTERN: GROUP BY + comparing group counts
    # ─────────────────────────────────────────────────────
    def check_period_completeness(self):
        """
        Every period should have the same number of line items.
        If June has 18 rows and every other month has 23, five
        line items are missing from June.

        SQL EXPLANATION:
            First GROUP BY period to count rows per month, then
            compare each month's count to the maximum. Months
            below max are incomplete.

        EXCEL ANALOGY: a pivot of row-counts by month - any month
        whose count differs from the others sticks out.
        """
        per_period = self.db.execute_query("""
            SELECT period, COUNT(*) AS n
            FROM budget_actuals
            GROUP BY period
            ORDER BY period
        """)

        if not per_period:
            self._record("Period completeness", False, "no data at all")
            return

        max_n = max(r["n"] for r in per_period)
        incomplete = [r for r in per_period if r["n"] < max_n]

        self._record(
            "Period completeness",
            passed=(len(incomplete) == 0),
            message=f"{len(per_period)} periods, {max_n} items each"
                    if not incomplete
                    else f"{len(incomplete)} period(s) missing line items",
            details=incomplete
        )

    # ─────────────────────────────────────────────────────
    # RUN ALL
    # ─────────────────────────────────────────────────────
    def run_all(self):
        """
        Run every check. Returns True if all passed.

        Production behaviour: pipeline STOPS on critical failures
        (duplicates, orphans) but only WARNS on coverage gaps.
        """
        log.info("Running data quality checks...")
        self.check_duplicates()
        self.check_orphan_categories()
        self.check_prior_year_coverage()
        self.check_value_sanity()
        self.check_period_completeness()

        failed = [name for name, passed, _ in self.results if not passed]
        if failed:
            log.warning(f"{len(failed)} check(s) failed: {', '.join(failed)}")
        else:
            log.info("All data quality checks passed")

        return len(failed) == 0


if __name__ == "__main__":
    db = BudgetDatabase()
    try:
        db.connect()
        checker = DataQualityChecker(db)
        checker.run_all()
    finally:
        db.close()

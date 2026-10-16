"""
data_quality.py
===============
Runs five SQL integrity checks on the loaded data before any variance
calculation begins. Stops the pipeline with a clear error message if
any critical check fails.

Inputs:  budget_actuals, prior_year_actuals, category_metadata tables
Outputs: pass/fail result per check, logged to pipeline.log
"""

from database import BudgetDatabase
from logger_setup import get_logger

log = get_logger(__name__)


class DataQualityChecker:
    """
    Runs a suite of SQL validation checks. Critical checks stop the
    pipeline when they fail; non-critical checks only log a warning.
    """

    def __init__(self, db: BudgetDatabase):
        self.db = db
        self.results = []   # collected (name, passed, message, critical) tuples

    def _record(self, name, passed, message, details=None, critical=True):
        """
        Store and log one check result. Passes are logged at INFO,
        failures at WARNING (non-critical) or ERROR (critical) with up
        to five example rows.
        """
        if passed:
            log.info(f"{name}: {message}")
        else:
            report = log.error if critical else log.warning
            report(f"{name}: {message}")
            if details:
                for d in details[:5]:   # log at most 5 examples
                    report(f"  example: {dict(d)}")
        self.results.append((name, passed, message, critical))

    # ─────────────────────────────────────────────────────
    # CHECK 1: Duplicate rows
    # ─────────────────────────────────────────────────────
    def check_duplicates(self):
        """
        Flag any (period, category, subcategory) key that appears
        more than once - duplicates would double-count variances.
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
    # ─────────────────────────────────────────────────────
    def check_orphan_categories(self):
        """
        Find categories used in budget_actuals that have no entry
        in category_metadata - they would silently get default rules.
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
    # ─────────────────────────────────────────────────────
    def check_prior_year_coverage(self):
        """
        Count current line items with no matching prior-year row.
        YoY columns will be NULL for these - expected for new lines,
        a data gap otherwise. Uses the same period join as
        variance_calculator so it tests the real join logic.
        Non-critical: coverage below 90% is a warning only.
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
            details=missing,
            critical=False
        )

    # ─────────────────────────────────────────────────────
    # CHECK 4: Value sanity
    # ─────────────────────────────────────────────────────
    def check_value_sanity(self):
        """
        Flag NULL amounts and negative budgets or actuals. Working
        capital is excluded from the negativity rule since it can be
        negative.
        """
        bad = self.db.execute_query("""
            SELECT period, category, subcategory,
                   budget_amount, actual_amount
            FROM budget_actuals
            WHERE budget_amount IS NULL
               OR actual_amount IS NULL
               OR ((budget_amount < 0 OR actual_amount < 0)
                   AND category != 'working_capital')
        """)

        self._record(
            "Amounts present and sensible",
            passed=(len(bad) == 0),
            message="clean" if not bad else f"{len(bad)} suspect row(s)",
            details=bad
        )

    # ─────────────────────────────────────────────────────
    # CHECK 5: Period completeness
    # ─────────────────────────────────────────────────────
    def check_period_completeness(self):
        """
        Every period should have the same number of line items.
        Periods with fewer rows than the maximum are flagged as
        incomplete.
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
        Run every check. Returns True if no critical check failed
        (non-critical failures are logged as warnings only).
        """
        log.info("Running data quality checks...")
        self.check_duplicates()
        self.check_orphan_categories()
        self.check_prior_year_coverage()
        self.check_value_sanity()
        self.check_period_completeness()

        critical = [name for name, passed, _, crit in self.results if not passed and crit]
        warnings = [name for name, passed, _, crit in self.results if not passed and not crit]
        if critical:
            log.error(f"{len(critical)} critical check(s) failed: {', '.join(critical)}")
        if warnings:
            log.warning(f"{len(warnings)} non-critical check(s) failed: {', '.join(warnings)}")
        if not critical and not warnings:
            log.info("All data quality checks passed")

        return len(critical) == 0


if __name__ == "__main__":
    db = BudgetDatabase()
    try:
        db.connect()
        checker = DataQualityChecker(db)
        checker.run_all()
    finally:
        db.close()

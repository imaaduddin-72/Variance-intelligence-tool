"""
snapshot_manager.py
===================
Archives each run's variance_analysis into an append-only history and
compares flagged items against the previous run (new, resolved, recurring).

Inputs:  variance_analysis, variance_snapshots tables
Outputs: new rows in variance_snapshots; run-over-run diff logged to pipeline.log
"""

import uuid
from datetime import datetime, timezone

import config
from database import BudgetDatabase
from logger_setup import get_logger

log = get_logger(__name__)


class SnapshotManager:

    def __init__(self, db: BudgetDatabase):
        self.db = db

    def save_snapshot(self):
        """
        Copy the current variance_analysis into variance_snapshots,
        tagged with a unique run_id and timestamp.

        SQL EXPLANATION:
            INSERT INTO variance_snapshots (...)
            SELECT <run_id>, <timestamp>, <columns...>
            FROM variance_analysis

            Same "INSERT INTO ... SELECT" pattern as Module 2 - but
            instead of calculating new values, we copy existing rows
            with two extra constant columns (run_id, run_timestamp)
            stamped onto every row.

        PYTHON CONCEPTS:
            uuid.uuid4()  - generates a random unique ID, e.g.
                            'a1b2c3d4-...' - guarantees this run's
                            ID won't collide with any other run ever.
            datetime.now(timezone.utc) - current UTC time. UTC avoids
                            ambiguity if run from different timezones.
            .isoformat()  - converts to a sortable string like
                            '2026-06-12T15:30:00+00:00' - sorts
                            correctly as TEXT in SQLite.

        Returns the run_id (string).
        """
        run_id = str(uuid.uuid4())
        run_timestamp = datetime.now(timezone.utc).isoformat()

        self.db.conn.execute("""
            INSERT INTO variance_snapshots (
                run_id, run_timestamp,
                period, category, subcategory,
                budget_amount, actual_amount,
                budget_variance, budget_variance_pct,
                is_material, variance_status, risk_level
            )
            SELECT
                ?, ?,
                period, category, subcategory,
                budget_amount, actual_amount,
                budget_variance, budget_variance_pct,
                is_material, variance_status, risk_level
            FROM variance_analysis
        """, (run_id, run_timestamp))

        self.db.conn.commit()

        n = self.db.execute_query(
            "SELECT COUNT(*) AS n FROM variance_snapshots WHERE run_id = ?",
            (run_id,)
        )[0]["n"]

        log.info(f"Snapshot saved: run_id={run_id[:8]}... ({n} rows)")
        return run_id

    def get_previous_run_id(self, current_run_id):
        """
        Find the run_id of the run BEFORE current_run_id.

        SQL EXPLANATION - ROW_NUMBER() window function:

            SELECT run_id, run_timestamp FROM (
                SELECT run_id, run_timestamp,
                       ROW_NUMBER() OVER (ORDER BY run_timestamp DESC) AS rn
                FROM (SELECT DISTINCT run_id, run_timestamp FROM variance_snapshots)
            )
            WHERE rn = 2

            Three layers, innermost first:
              1. SELECT DISTINCT - collapse 414 rows-per-run down to
                 ONE row per run_id (run_id+run_timestamp is constant
                 across all 414 rows of a given run).
              2. ROW_NUMBER() OVER (ORDER BY run_timestamp DESC) - on
                 those collapsed run-rows, number them 1, 2, 3...
                 by recency. Newest run = rn 1, previous run = rn 2.
              3. WHERE rn = 2 - pick out just the previous run.

            SQLite doesn't allow filtering on a window-function alias
            in the SAME SELECT that defines it (the "rn = 2" filter
            has to happen in an OUTER query) - that's why there are
            three nested layers, not two.

        Returns None if this is the first-ever run.
        """
        rows = self.db.execute_query("""
            SELECT run_id, run_timestamp FROM (
                SELECT run_id, run_timestamp,
                       ROW_NUMBER() OVER (ORDER BY run_timestamp DESC) AS rn
                FROM (
                    SELECT DISTINCT run_id, run_timestamp
                    FROM variance_snapshots
                )
            )
            WHERE rn = 2
        """)

        if not rows:
            return None
        return rows[0]["run_id"]

    def compare_to_previous(self, current_run_id):
        """
        Compare this run's flagged (material) items to the previous
        run's flagged items. Classify each into:

            NEW       - flagged now, wasn't flagged last time
            RESOLVED  - flagged last time, not flagged now
            RECURRING - flagged in both runs (ongoing issue)

        PYTHON CONCEPT - sets:
            A "set" is a collection with no duplicates, supporting
            mathematical operations:
                current - previous   = items only in current  (NEW)
                previous - current   = items only in previous (RESOLVED)
                current & previous   = items in both           (RECURRING)

            This is Excel's "items in List A not in List B" problem
            (often solved with COUNTIF/VLOOKUP) - as a single
            operator instead of a formula per row.

        Returns a dict with three lists, or None if there's no
        previous run to compare against.
        """
        previous_run_id = self.get_previous_run_id(current_run_id)

        if previous_run_id is None:
            log.info("No previous run found - this is the first snapshot.")
            return None

        def flagged_set(run_id):
            rows = self.db.execute_query("""
                SELECT period, category, subcategory
                FROM variance_snapshots
                WHERE run_id = ? AND is_material = 1
            """, (run_id,))
            # Build a set of tuples - each tuple uniquely identifies
            # one line item. Tuples (not lists) because sets require
            # hashable elements - lists aren't hashable, tuples are.
            return {(r["period"], r["category"], r["subcategory"]) for r in rows}

        current_items = flagged_set(current_run_id)
        previous_items = flagged_set(previous_run_id)

        new_items       = current_items - previous_items
        resolved_items  = previous_items - current_items
        recurring_items = current_items & previous_items

        result = {
            "previous_run_id": previous_run_id,
            "new": sorted(new_items),
            "resolved": sorted(resolved_items),
            "recurring": sorted(recurring_items),
        }

        log.info(f"Diff vs previous run: "
                 f"{len(result['new'])} new, "
                 f"{len(result['resolved'])} resolved, "
                 f"{len(result['recurring'])} recurring")

        return result

    def print_diff_report(self, diff):
        """
        Human-readable diff report for the terminal.

        If diff is None (first run ever), say so clearly rather
        than printing empty sections.
        """
        print("\n" + "=" * 65)
        print("CHANGES SINCE LAST RUN")
        print("=" * 65)

        if diff is None:
            print("\n  This is the first recorded run - nothing to compare yet.")
            print("  Future runs will show new/resolved/recurring issues here.")
            print("=" * 65)
            return

        def fmt_items(items, label, empty_msg):
            print(f"\n{label} ({len(items)}):")
            if not items:
                print(f"  {empty_msg}")
                return
            for period, category, subcategory in items:
                print(f"  {period} | {category:<14} | {subcategory}")

        fmt_items(diff["new"], "NEW (flagged this run, not last run)",
                  "none - no new issues")
        fmt_items(diff["resolved"], "RESOLVED (flagged last run, not this run)",
                  "none - nothing resolved")
        fmt_items(diff["recurring"], "RECURRING (flagged in both runs)",
                  "none")

        print("\n" + "=" * 65)


def run_snapshot_stage(db: BudgetDatabase):
    """
    Convenience function for run_pipeline.py - does the full
    snapshot + diff in one call.
    """
    n = db.execute_query(
        "SELECT COUNT(*) AS n FROM variance_analysis")[0]["n"]
    if n == 0:
        raise RuntimeError(
            "variance_analysis is empty - run variance_calculator.py first.")

    mgr = SnapshotManager(db)
    run_id = mgr.save_snapshot()
    diff = mgr.compare_to_previous(run_id)
    mgr.print_diff_report(diff)
    return diff


if __name__ == "__main__":
    db = BudgetDatabase()
    try:
        db.connect()
        run_snapshot_stage(db)
        log.info("Snapshot stage complete")
    except RuntimeError as e:
        log.error(str(e))
    finally:
        db.close()

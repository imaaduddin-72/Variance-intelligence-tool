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

from database import BudgetDatabase
from logger_setup import get_logger

log = get_logger(__name__)


class SnapshotManager:

    def __init__(self, db: BudgetDatabase):
        self.db = db

    def save_snapshot(self):
        """
        Copy the current variance_analysis into variance_snapshots,
        tagged with a UUID run_id and a UTC ISO-8601 timestamp
        (sortable as text).

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
        Return the run_id of the second most recent run (the one
        before the run just saved), or None if this is the first run.
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
        Print the run-over-run diff. A None diff means this is the
        first run.
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
    Pipeline entry point: save a snapshot and print the diff
    against the previous run.
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

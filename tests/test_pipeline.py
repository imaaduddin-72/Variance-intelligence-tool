"""
test_pipeline.py
================
Unit tests for the variance helpers and end-to-end tests that run the
full pipeline against temporary copies of the input CSVs.

Run from the project root:  python -m unittest discover tests
"""

import contextlib
import csv
import io
import logging
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

import config                                   # noqa: E402
from database import BudgetDatabase             # noqa: E402
from data_quality import DataQualityChecker     # noqa: E402
from run_pipeline import run_pipeline           # noqa: E402
from variance_calculator import (               # noqa: E402
    calc_variance, calc_variance_pct, calc_yoy_variance, calc_yoy_pct,
    calc_variance_status, is_favorable, is_material,
)


def setUpModule():
    logging.disable(logging.CRITICAL)


def tearDownModule():
    logging.disable(logging.NOTSET)


class VarianceHelperTests(unittest.TestCase):

    def test_zero_budget_still_produces_variance(self):
        self.assertEqual(calc_variance(50000, 0), 50000)
        self.assertIsNone(calc_variance_pct(50000, 0))
        self.assertEqual(is_material(calc_variance(50000, 0), 30000), 1)
        self.assertEqual(calc_variance_status(50000, "decrease", 0), "Unfavorable")

    def test_zero_prior_year_still_produces_yoy_variance(self):
        self.assertEqual(calc_yoy_variance(100, 0), 100)
        self.assertIsNone(calc_yoy_pct(100, 0))

    def test_missing_values_return_none(self):
        self.assertIsNone(calc_variance(None, 100))
        self.assertIsNone(calc_yoy_variance(100, None))

    def test_zero_variance_is_on_plan_for_every_direction(self):
        for direction in ("increase", "decrease", "stable"):
            self.assertEqual(calc_variance_status(0, direction, 100), "On Plan")

    def test_directional_statuses(self):
        self.assertEqual(calc_variance_status(10, "increase", 100), "Favorable")
        self.assertEqual(calc_variance_status(-10, "increase", 100), "Unfavorable")
        self.assertEqual(calc_variance_status(-10, "decrease", 100), "Favorable")
        self.assertEqual(calc_variance_status(10, "decrease", 100), "Unfavorable")

    def test_stable_tolerance_band(self):
        within = config.STABLE_TOLERANCE_PCT / 2
        outside = config.STABLE_TOLERANCE_PCT * 2
        self.assertEqual(calc_variance_status(within, "stable", 100), "On Plan")
        self.assertEqual(calc_variance_status(outside, "stable", 100), "Deviation")
        self.assertEqual(calc_variance_status(5, "stable", 0), "Deviation")

    def test_is_favorable_agrees_with_status(self):
        self.assertEqual(is_favorable("Favorable"), 1)
        self.assertEqual(is_favorable("On Plan"), 1)
        self.assertEqual(is_favorable("Unfavorable"), 0)
        self.assertEqual(is_favorable("Deviation"), 0)


class PipelineTestCase(unittest.TestCase):
    """Runs against temporary copies of the CSVs and temporary outputs."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

        for name in ("budget_actuals.csv", "prior_year_data.csv",
                     "category_metadata.csv"):
            shutil.copy(PROJECT_DIR / name, self.tmp / name)

        overrides = {
            "DATA_DIR":               self.tmp,
            "BUDGET_ACTUALS_CSV":     self.tmp / "budget_actuals.csv",
            "PRIOR_YEAR_CSV":         self.tmp / "prior_year_data.csv",
            "CATEGORY_METADATA_CSV":  self.tmp / "category_metadata.csv",
            "DATABASE_PATH":          self.tmp / "test.db",
            "REPORT_OUTPUT_PATH":     self.tmp / "variance_report.xlsx",
            "COMMENTARY_OUTPUT_PATH": self.tmp / "variance_commentary.txt",
            "MODEL_FEED_CSV":         self.tmp / "model_feed.csv",
            "MODEL_FEED_XLSX":        self.tmp / "model_feed.xlsx",
        }
        for name, value in overrides.items():
            patcher = mock.patch.object(config, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_quietly(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return run_pipeline()

    def query(self, sql):
        with contextlib.closing(sqlite3.connect(config.DATABASE_PATH)) as conn:
            return conn.execute(sql).fetchall()

    def edit_budget_csv(self, edit):
        """Apply edit(rows) to the temporary budget_actuals.csv."""
        path = config.BUDGET_ACTUALS_CSV
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            fields, rows = reader.fieldnames, list(reader)
        edit(rows)
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    def load_only(self):
        db = BudgetDatabase()
        db.connect()
        self.addCleanup(db.close)
        db.create_schema()
        db.load_csv_data()
        return db


class EndToEndTests(PipelineTestCase):

    def test_pipeline_runs_and_writes_all_outputs(self):
        self.assertEqual(self.run_quietly(), 0)
        self.assertEqual(self.query("SELECT COUNT(*) FROM variance_analysis")[0][0], 414)
        self.assertEqual(self.query("SELECT COUNT(*) FROM trend_analysis")[0][0], 23)
        for name in ("REPORT_OUTPUT_PATH", "COMMENTARY_OUTPUT_PATH",
                     "MODEL_FEED_CSV", "MODEL_FEED_XLSX"):
            self.assertTrue(getattr(config, name).exists(), name)

    def test_status_and_favorable_flags_agree(self):
        self.run_quietly()
        mismatches = self.query("""
            SELECT COUNT(*) FROM variance_analysis
            WHERE is_favorable != (variance_status IN ('Favorable', 'On Plan'))
        """)[0][0]
        self.assertEqual(mismatches, 0)

    def test_rerun_keeps_snapshot_history(self):
        self.run_quietly()
        self.run_quietly()
        runs = self.query("SELECT COUNT(DISTINCT run_id) FROM variance_snapshots")[0][0]
        self.assertEqual(runs, 2)
        self.assertEqual(self.query("SELECT COUNT(*) FROM variance_analysis")[0][0], 414)

    def test_zero_budget_line_is_flagged(self):
        def zero_first_opex(rows):
            row = next(r for r in rows if r["category"] == "opex")
            row["budget_amount"] = "0"
            row["actual_amount"] = "50000"
        self.edit_budget_csv(zero_first_opex)

        self.assertEqual(self.run_quietly(), 0)
        status, material = self.query("""
            SELECT variance_status, is_material FROM variance_analysis
            WHERE category = 'opex' AND budget_amount = 0
        """)[0]
        self.assertEqual((status, material), ("Unfavorable", 1))


class DataQualityTests(PipelineTestCase):

    def test_duplicate_rows_rejected_on_load(self):
        self.edit_budget_csv(lambda rows: rows.append(dict(rows[0], id="9999")))
        with self.assertRaises(RuntimeError) as ctx:
            self.load_only()
        self.assertIn("duplicate", str(ctx.exception))

    def test_negative_actual_fails_critical_check(self):
        def negative_revenue(rows):
            next(r for r in rows if r["category"] == "revenue")["actual_amount"] = "-1"
        self.edit_budget_csv(negative_revenue)

        db = self.load_only()
        self.assertFalse(DataQualityChecker(db).run_all())
        self.assertEqual(self.run_quietly(), 1)

    def test_low_prior_year_coverage_only_warns(self):
        with open(config.PRIOR_YEAR_CSV, encoding="utf-8") as f:
            header = f.readline()
        with open(config.PRIOR_YEAR_CSV, "w", encoding="utf-8") as f:
            f.write(header)   # no prior-year rows at all

        db = self.load_only()
        checker = DataQualityChecker(db)
        self.assertTrue(checker.run_all())
        coverage = next(r for r in checker.results if r[0] == "Prior-year coverage")
        self.assertFalse(coverage[1])   # failed, but non-critical


if __name__ == "__main__":
    unittest.main()

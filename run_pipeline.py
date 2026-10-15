"""
run_pipeline.py
===============

PURPOSE: Run the entire variance analysis pipeline with ONE command:

    python run_pipeline.py

PIPELINE STAGES (in order, each depends on the previous):
    1. LOAD       database.py            CSVs -> SQLite tables
    2. VALIDATE   data_quality.py        SQL integrity checks
    3. CALCULATE  variance_calculator.py one-pass SQL variance calc
    4. TRENDS     trend_analysis.py      gaps-and-islands streak detection
    5. RISK       financial_risk.py      concentration, CCC, volatility
    6. COMMENT    commentary_generator.py plain-English narratives
    7. REPORT     report_generator.py    formatted Excel board pack
    8. EXPORT     export_for_model.py    flat feed for EA model/Power BI
    9. SNAPSHOT   snapshot_manager.py    archive this run + diff vs last run

PRODUCTION PATTERNS DEMONSTRATED HERE:
    - Fail fast: if a stage fails, later stages don't run
      (no point reporting on data that failed validation)
    - Timing: each stage is timed, so you can spot slowdowns
      as data grows
    - Single entry point: one command for the whole monthly
      refresh - this is what you'd schedule with Task Scheduler
      to run automatically every month

FINANCIAL ANALOGY: the month-end close checklist, automated.
Each step signs off before the next begins.
"""

import sys
import time

import config
from database import BudgetDatabase
from data_quality import DataQualityChecker
from variance_calculator import VarianceCalculator
from commentary_generator import CommentaryGenerator
from report_generator import ReportGenerator
from export_for_model import ModelFeedExporter
from trend_analysis import run_trend_stage
from financial_risk import run_financial_risk
from snapshot_manager import run_snapshot_stage
from logger_setup import get_logger

log = get_logger(__name__)


def stage(title):
    """Print a stage banner AND log it - banners are for the screen,
    the log entry is what lets you grep pipeline.log for
    'STAGE 3/6' across many historical runs."""
    print("\n" + "=" * 65)
    print(f"  {title}")
    print("=" * 65)
    log.info(f"--- {title} ---")


def run_pipeline():
    started = time.time()
    timings = []

    db = BudgetDatabase()

    try:
        # ── STAGE 1: LOAD ────────────────────────────────────
        stage("STAGE 1/9 - LOAD DATA")
        t0 = time.time()
        #
        # IMPORTANT CHANGE: we used to delete budget_analysis.db
        # entirely at the start of every run ("fresh slate"). But
        # that ALSO destroyed variance_snapshots - the very history
        # Stage 7 needs to compare against!
        #
        # New approach: db.create_schema() uses "CREATE TABLE IF
        # NOT EXISTS", so it's safe to call on an existing database -
        # it won't wipe variance_snapshots. load_csv_data() uses
        # if_exists="replace" for the 3 input tables (budget_actuals
        # etc.) and variance_calculator does "DELETE FROM
        # variance_analysis" before recalculating - so the CURRENT
        # data is still always fully refreshed. Only the APPEND-ONLY
        # snapshots table survives across runs, which is exactly
        # the point of it.
        db.connect()
        db.create_schema()
        db.load_csv_data()
        timings.append(("Load", time.time() - t0))

        # ── STAGE 2: VALIDATE ────────────────────────────────
        stage("STAGE 2/9 - DATA QUALITY CHECKS")
        t0 = time.time()
        checker = DataQualityChecker(db)
        all_passed = checker.run_all()
        timings.append(("Validate", time.time() - t0))

        if not all_passed:
            # Production decision: stop the pipeline on failed checks.
            # Better no report than a wrong report.
            raise RuntimeError(
                "Data quality checks failed - pipeline stopped. "
                "Fix the issues above and re-run.")

        # ── STAGE 3: CALCULATE ───────────────────────────────
        stage("STAGE 3/9 - VARIANCE CALCULATION")
        t0 = time.time()
        calc = VarianceCalculator(db)
        calc.calculate_all()
        calc.summary_report()
        timings.append(("Calculate", time.time() - t0))

        # ── STAGE 4: TREND ANALYSIS ──────────────────────────
        stage("STAGE 4/9 - TREND ANALYSIS")
        t0 = time.time()
        run_trend_stage(db)
        timings.append(("Trends", time.time() - t0))

        # ── STAGE 5: FINANCIAL RISK ───────────────────────────
        stage("STAGE 5/9 - FINANCIAL RISK ANALYSIS")
        t0 = time.time()
        run_financial_risk(db)
        timings.append(("FinRisk", time.time() - t0))

        # ── STAGE 6: COMMENTARY ──────────────────────────────
        stage("STAGE 6/9 - COMMENTARY GENERATION")
        t0 = time.time()
        gen = CommentaryGenerator(db)
        gen.generate_all()
        gen.write_narrative_file()
        timings.append(("Commentary", time.time() - t0))

        # ── STAGE 7: EXCEL REPORT ────────────────────────────
        stage("STAGE 7/9 - EXCEL REPORT")
        t0 = time.time()
        report = ReportGenerator(db)
        report.build_summary_sheet()
        report.build_detail_sheet()
        report.build_flagged_sheet()
        report.save()
        timings.append(("Report", time.time() - t0))

        # ── STAGE 8: MODEL FEED ──────────────────────────────
        stage("STAGE 8/9 - MODEL FEED EXPORT")
        t0 = time.time()
        exporter = ModelFeedExporter(db)
        exporter.export_all()
        timings.append(("Export", time.time() - t0))

        # ── STAGE 9: SNAPSHOT + DIFF ──────────────────────────
        stage("STAGE 9/9 - SNAPSHOT & DIFF VS LAST RUN")
        t0 = time.time()
        run_snapshot_stage(db)
        timings.append(("Snapshot", time.time() - t0))

        # ── DONE ─────────────────────────────────────────────
        total = time.time() - started
        stage("PIPELINE COMPLETE")
        print(f"\n  {'Stage':<12} {'Time':>8}")
        print("  " + "-" * 22)
        for name, secs in timings:
            print(f"  {name:<12} {secs:>7.2f}s")
        print("  " + "-" * 22)
        print(f"  {'TOTAL':<12} {total:>7.2f}s")

        print(f"""
  OUTPUTS:
    {config.DATABASE_PATH.name}        - database (all tables)
    {config.REPORT_OUTPUT_PATH.name}     - formatted board pack
    {config.COMMENTARY_OUTPUT_PATH.name} - exceptions narrative
    {config.MODEL_FEED_CSV.name}           - Power BI feed
    {config.MODEL_FEED_XLSX.name}          - EA model feed
""")
        log.info(f"Pipeline completed successfully in {total:.2f}s")
        return 0

    except (RuntimeError, FileNotFoundError) as e:
        print(f"\n✗ PIPELINE FAILED: {e}")
        log.error(f"Pipeline failed: {e}")
        return 1

    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(run_pipeline())

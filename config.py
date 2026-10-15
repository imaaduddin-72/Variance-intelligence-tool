"""
config.py
=========

PURPOSE: Single source of truth for all file paths and settings.

FINANCIAL ANALOGY:
    Before this file, if you wanted to change where your CSVs live,
    you'd have to edit database.py, hunt for the right line, and hope
    you didn't miss one.

    This is like having ONE "Assumptions" tab in a financial model that
    everything else references - instead of hardcoding the same number
    in 10 different formulas across 10 different sheets.

HOW TO USE THIS FILE:
    If you move your project folder, or rename your database, or want
    output files saved somewhere else - change it HERE, once, and
    every module (database.py, variance_calculator.py,
    report_generator.py) automatically picks up the new value.
"""

from pathlib import Path

# ─────────────────────────────────────────────────────────────
# BASE DIRECTORY
# ─────────────────────────────────────────────────────────────
# Path(__file__) = location of THIS config.py file
# .parent = the folder containing it
#
# This means: no matter where you copy this project folder
# (Desktop, OneDrive, USB drive), all the paths below adjust
# automatically because they're relative to config.py itself.
BASE_DIR = Path(__file__).resolve().parent


# ─────────────────────────────────────────────────────────────
# INPUT FILES (raw CSV data - never modified by the scripts)
# ─────────────────────────────────────────────────────────────
DATA_DIR = BASE_DIR  # CSVs live in the same folder as the scripts

BUDGET_ACTUALS_CSV   = DATA_DIR / "budget_actuals.csv"
PRIOR_YEAR_CSV       = DATA_DIR / "prior_year_data.csv"
CATEGORY_METADATA_CSV = DATA_DIR / "category_metadata.csv"


# ─────────────────────────────────────────────────────────────
# DATABASE
# ─────────────────────────────────────────────────────────────
DATABASE_PATH = BASE_DIR / "budget_analysis.db"


# ─────────────────────────────────────────────────────────────
# OUTPUT FILES
# ─────────────────────────────────────────────────────────────
REPORT_OUTPUT_PATH     = BASE_DIR / "variance_report.xlsx"
COMMENTARY_OUTPUT_PATH = BASE_DIR / "variance_commentary.txt"
MODEL_FEED_CSV         = BASE_DIR / "model_feed.csv"        # for EA model / Power BI
MODEL_FEED_XLSX        = BASE_DIR / "model_feed.xlsx"


# ─────────────────────────────────────────────────────────────
# STABLE-CATEGORY TOLERANCE
# ─────────────────────────────────────────────────────────────
# For categories whose expected_direction = 'stable' (headcount,
# working capital), favorable/unfavorable language doesn't apply.
# Instead: within +/- this % of budget = "On Plan", outside = "Deviation".
#
# FINANCIAL ANALOGY: like a control band on a KPI dashboard -
# headcount of 29 vs plan of 28 isn't "bad", it's within tolerance.
STABLE_TOLERANCE_PCT = 2.0   # within ±2% of budget counts as On Plan


# ─────────────────────────────────────────────────────────────
# BUSINESS RULES (fallback defaults)
# ─────────────────────────────────────────────────────────────
# If category_metadata.csv is ever missing a category, these
# defaults prevent the pipeline from crashing.
#
# FINANCIAL ANALOGY: "If a cost center isn't in our policy table,
# treat it as low-risk / stable until someone classifies it properly."
DEFAULT_MATERIALITY_THRESHOLD = 25000   # ₹
DEFAULT_EXPECTED_DIRECTION    = "stable"
DEFAULT_RISK_WEIGHT           = "LOW"


# ─────────────────────────────────────────────────────────────
# VALIDATION
# Run this at startup to catch missing files EARLY with a clear
# message, instead of a confusing crash 50 lines into the script.
# ─────────────────────────────────────────────────────────────
def validate_input_files():
    """
    Check that all required CSV files exist before we start.

    FINANCIAL ANALOGY: Like checking all source files are saved
    in the shared drive before you start building the consolidation -
    catch the problem before you've wasted time halfway through.

    Returns a list of (label, path) tuples for missing files.
    Empty list = everything is fine.
    """
    required = {
        "Budget Actuals":     BUDGET_ACTUALS_CSV,
        "Prior Year Data":    PRIOR_YEAR_CSV,
        "Category Metadata":  CATEGORY_METADATA_CSV,
    }

    missing = []
    for label, path in required.items():
        if not path.exists():
            missing.append((label, path))

    return missing


if __name__ == "__main__":
    # Quick self-test: run "python config.py" to see resolved paths
    print("Configuration check")
    print("=" * 60)
    print(f"Base directory:     {BASE_DIR}")
    print(f"Budget actuals CSV: {BUDGET_ACTUALS_CSV}")
    print(f"Prior year CSV:     {PRIOR_YEAR_CSV}")
    print(f"Category meta CSV:  {CATEGORY_METADATA_CSV}")
    print(f"Database path:      {DATABASE_PATH}")
    print(f"Report output path: {REPORT_OUTPUT_PATH}")
    print()

    missing = validate_input_files()
    if missing:
        print("⚠ MISSING FILES:")
        for label, path in missing:
            print(f"  - {label}: {path}")
    else:
        print("✓ All input files found")

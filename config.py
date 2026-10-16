"""
config.py
=========

Central configuration for file paths, business-rule defaults and
tolerances. All pipeline modules read their settings from here.

Inputs:  none
Outputs: path and setting constants; validate_input_files()
"""

from pathlib import Path

# ─────────────────────────────────────────────────────────────
# BASE DIRECTORY
# ─────────────────────────────────────────────────────────────
# All paths are resolved relative to this file, so the project
# folder can be moved without changing any settings.
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
STABLE_TOLERANCE_PCT = 2.0   # within ±2% of budget counts as On Plan


# ─────────────────────────────────────────────────────────────
# BUSINESS RULES (fallback defaults)
# ─────────────────────────────────────────────────────────────
# Applied to any category missing from category_metadata.csv:
# treated as stable and low-risk until it is classified.
DEFAULT_MATERIALITY_THRESHOLD = 25000   # ₹
DEFAULT_EXPECTED_DIRECTION    = "stable"
DEFAULT_RISK_WEIGHT           = "LOW"


# ─────────────────────────────────────────────────────────────
# VALIDATION
# ─────────────────────────────────────────────────────────────
def validate_input_files():
    """
    Check that all required CSV files exist before loading.

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

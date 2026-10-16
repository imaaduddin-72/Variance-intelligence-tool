"""
database.py
===========
Opens the SQLite database, creates the schema, and loads the source CSVs
after checking they exist and have the expected columns.

Inputs:  source CSV files and paths from config.py
Outputs: populated budget_actuals, prior_year_actuals, category_metadata tables
"""

import sqlite3
import pandas as pd
import config
from logger_setup import get_logger

log = get_logger(__name__)


class BudgetDatabase:

    def __init__(self, db_path=None):
        # If no path given, use the one defined in config.py
        self.db_path = str(db_path or config.DATABASE_PATH)
        self.conn = None

    def connect(self):
        """
        Open the database connection.

        ERROR HANDLING: sqlite3.connect() rarely fails outright, but
        if the folder doesn't exist or there's a permissions issue,
        we want a clear message instead of a raw traceback.
        """
        try:
            self.conn = sqlite3.connect(self.db_path)
            self.conn.row_factory = sqlite3.Row
            log.info(f"Connected: {self.db_path}")
        except sqlite3.Error as e:
            raise RuntimeError(f"Could not open database '{self.db_path}': {e}")

    def close(self):
        if self.conn:
            self.conn.close()

    def create_schema(self):
        cursor = self.conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS budget_actuals (
                id INTEGER PRIMARY KEY,
                period TEXT NOT NULL,
                category TEXT NOT NULL,
                subcategory TEXT NOT NULL,
                budget_amount REAL,
                actual_amount REAL,
                UNIQUE(period, category, subcategory)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS prior_year_actuals (
                id INTEGER PRIMARY KEY,
                period TEXT NOT NULL,
                category TEXT NOT NULL,
                subcategory TEXT NOT NULL,
                actual_amount REAL,
                UNIQUE(period, category, subcategory)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS category_metadata (
                category TEXT PRIMARY KEY,
                category_type TEXT NOT NULL,
                expected_direction TEXT NOT NULL,
                materiality_threshold_inr INTEGER NOT NULL,
                risk_weight TEXT NOT NULL
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS variance_analysis (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                period TEXT NOT NULL,
                category TEXT NOT NULL,
                subcategory TEXT NOT NULL,
                budget_amount REAL,
                actual_amount REAL,
                budget_variance REAL,
                budget_variance_pct REAL,
                prior_year_actual REAL,
                yoy_variance REAL,
                yoy_variance_pct REAL,
                is_material INTEGER,
                is_favorable INTEGER,
                risk_level TEXT,
                variance_status TEXT,
                commentary TEXT,
                UNIQUE(period, category, subcategory)
            )
        """)

        # ── SNAPSHOT HISTORY TABLE ─────────────────────────────
        #
        # PURPOSE: variance_analysis gets WIPED and rebuilt every
        # pipeline run (DELETE + re-INSERT). That's correct for
        # "current state" - but it means there's no history.
        #
        # variance_snapshots is the opposite: it NEVER gets wiped.
        # Every pipeline run APPENDS a full copy of variance_analysis,
        # tagged with run_timestamp and run_id.
        #
        # FINANCIAL ANALOGY: variance_analysis is this month's
        # working file - you overwrite it each refresh. snapshots
        # is the archived PDF you'd save to a dated folder every
        # month, so you can always go back and see what last
        # month's numbers said, even after this month overwrote them.
        #
        # NOTE: no UNIQUE constraint here - the same
        # (period, category, subcategory) legitimately appears in
        # MANY snapshots, once per run. run_id is what makes each
        # snapshot's rows distinct.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS variance_snapshots (
                snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                run_timestamp TEXT NOT NULL,
                period TEXT NOT NULL,
                category TEXT NOT NULL,
                subcategory TEXT NOT NULL,
                budget_amount REAL,
                actual_amount REAL,
                budget_variance REAL,
                budget_variance_pct REAL,
                is_material INTEGER,
                variance_status TEXT,
                risk_level TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trend_analysis (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                category      TEXT NOT NULL,
                subcategory   TEXT NOT NULL,
                current_status TEXT,
                streak_length  INTEGER,
                is_chronic     INTEGER,
                latest_period  TEXT,
                streak_direction TEXT,
                UNIQUE(category, subcategory)
            )
        """)

        # Migration: add streak_direction to pre-existing databases
        try:
            cursor.execute(
                "ALTER TABLE trend_analysis ADD COLUMN streak_direction TEXT"
            )
        except Exception:
            pass  # column already exists

        self.conn.commit()
        log.info("Schema ready")

    def load_csv_data(self):
        """
        Load the three source CSVs into their tables.

        ERROR HANDLING:
        1. config.validate_input_files() checks all 3 CSVs exist
           BEFORE we touch the database - fail fast with a clear list.
        2. Each pd.read_csv() is wrapped so a malformed CSV (e.g.
           wrong columns, corrupted file) gives a readable error
           instead of a wall of pandas traceback.
        """
        # ── Step 1: validate files exist ──────────────────────
        missing = config.validate_input_files()
        if missing:
            lines = "\n".join(f"  - {label}: {path}" for label, path in missing)
            raise FileNotFoundError(
                f"Cannot load data - missing CSV file(s):\n{lines}\n\n"
                f"Check that these files are in: {config.DATA_DIR}"
            )

        # ── Step 2: load budget_actuals ───────────────────────
        try:
            df_budget = pd.read_csv(config.BUDGET_ACTUALS_CSV)
            expected = {"id", "period", "category", "subcategory",
                        "budget_amount", "actual_amount"}
            if not expected.issubset(df_budget.columns):
                raise ValueError(
                    f"budget_actuals.csv is missing expected columns. "
                    f"Found: {list(df_budget.columns)}"
                )
            df_budget.to_sql("budget_actuals", self.conn, if_exists="replace", index=False)
            log.info(f"budget_actuals: {len(df_budget)} rows")
        except Exception as e:
            raise RuntimeError(f"Failed loading budget_actuals.csv: {e}")

        # ── Step 3: load prior_year_actuals ───────────────────
        try:
            df_prior = pd.read_csv(config.PRIOR_YEAR_CSV)
            expected = {"id", "period", "category", "subcategory", "actual_amount"}
            if not expected.issubset(df_prior.columns):
                raise ValueError(
                    f"prior_year_data.csv is missing expected columns. "
                    f"Found: {list(df_prior.columns)}"
                )
            df_prior.to_sql("prior_year_actuals", self.conn, if_exists="replace", index=False)
            log.info(f"prior_year_actuals: {len(df_prior)} rows")
        except Exception as e:
            raise RuntimeError(f"Failed loading prior_year_data.csv: {e}")

        # ── Step 4: load category_metadata ────────────────────
        try:
            df_meta = pd.read_csv(config.CATEGORY_METADATA_CSV)
            expected = {"category", "category_type", "expected_direction",
                        "materiality_threshold_inr", "risk_weight"}
            if not expected.issubset(df_meta.columns):
                raise ValueError(
                    f"category_metadata.csv is missing expected columns. "
                    f"Found: {list(df_meta.columns)}"
                )
            df_meta.to_sql("category_metadata", self.conn, if_exists="replace", index=False)
            log.info(f"category_metadata: {len(df_meta)} rows")
        except Exception as e:
            raise RuntimeError(f"Failed loading category_metadata.csv: {e}")

        self.conn.commit()

    def execute_query(self, query, params=()):
        cursor = self.conn.cursor()
        cursor.execute(query, params)
        return cursor.fetchall()


if __name__ == "__main__":
    import os

    if os.path.exists(config.DATABASE_PATH):
        os.remove(config.DATABASE_PATH)

    db = BudgetDatabase()
    try:
        db.connect()
        db.create_schema()
        db.load_csv_data()
        log.info("Module 1 ready")
    except (RuntimeError, FileNotFoundError) as e:
        log.error(str(e))
    finally:
        db.close()

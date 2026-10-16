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


# Table -> (config attribute holding the source CSV path, columns loaded)
INPUT_COLUMNS = {
    "budget_actuals": (
        "BUDGET_ACTUALS_CSV",
        ["id", "period", "category", "subcategory",
         "budget_amount", "actual_amount"],
    ),
    "prior_year_actuals": (
        "PRIOR_YEAR_CSV",
        ["id", "period", "category", "subcategory", "actual_amount"],
    ),
    "category_metadata": (
        "CATEGORY_METADATA_CSV",
        ["category", "category_type", "expected_direction",
         "materiality_threshold_inr", "risk_weight"],
    ),
}


class BudgetDatabase:

    def __init__(self, db_path=None):
        # If no path given, use the one defined in config.py
        self.db_path = str(db_path or config.DATABASE_PATH)
        self.conn = None

    def connect(self):
        """
        Open the database connection. Raises RuntimeError with the
        path if the database cannot be opened.
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

        # Input tables are reloaded from CSV every run. Recreate them so
        # databases built by older versions (which replaced these tables
        # via pandas and lost their constraints) get the declared schema.
        for table in INPUT_COLUMNS:
            cursor.execute(f"DROP TABLE IF EXISTS {table}")

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
        # Append-only history: each pipeline run adds a full copy of
        # variance_analysis tagged with run_id and run_timestamp.
        # No UNIQUE constraint - the same (period, category,
        # subcategory) appears once per run.
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
        existing = {r[1] for r in cursor.execute("PRAGMA table_info(trend_analysis)")}
        if "streak_direction" not in existing:
            cursor.execute(
                "ALTER TABLE trend_analysis ADD COLUMN streak_direction TEXT"
            )

        self.conn.commit()
        log.info("Schema ready")

    def load_csv_data(self):
        """
        Load the three source CSVs into their tables. Call
        create_schema() first: rows are appended into the declared
        tables so UNIQUE and NOT NULL constraints are enforced.

        Raises FileNotFoundError listing any missing CSVs, and
        RuntimeError naming the file if a CSV is malformed, lacks
        expected columns, or contains duplicate keys.
        """
        missing = config.validate_input_files()
        if missing:
            lines = "\n".join(f"  - {label}: {path}" for label, path in missing)
            raise FileNotFoundError(
                f"Cannot load data - missing CSV file(s):\n{lines}\n\n"
                f"Check that these files are in: {config.DATA_DIR}"
            )

        for table, (path_setting, columns) in INPUT_COLUMNS.items():
            path = getattr(config, path_setting)
            try:
                df = pd.read_csv(path)
                absent = [c for c in columns if c not in df.columns]
                if absent:
                    raise ValueError(
                        f"missing expected column(s) {absent}. "
                        f"Found: {list(df.columns)}"
                    )
                self.conn.execute(f"DELETE FROM {table}")
                df[columns].to_sql(table, self.conn, if_exists="append", index=False)
                log.info(f"{table}: {len(df)} rows")
            except sqlite3.IntegrityError as e:
                self.conn.rollback()
                raise RuntimeError(
                    f"Failed loading {path.name}: duplicate or missing key values ({e})"
                )
            except Exception as e:
                self.conn.rollback()
                raise RuntimeError(f"Failed loading {path.name}: {e}")

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
        log.info("Database ready")
    except (RuntimeError, FileNotFoundError) as e:
        log.error(str(e))
    finally:
        db.close()

"""
export_for_model.py
===================
Exports variance_analysis as a flat, unformatted table with stable column
names for the forecast model and Power BI.

Inputs:  variance_analysis table
Outputs: model_feed.csv, model_feed.xlsx
"""

import csv
import config
from database import BudgetDatabase
from logger_setup import get_logger
from openpyxl import Workbook
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo

log = get_logger(__name__)


EXPORT_QUERY = """
    SELECT
        period,
        -- First day of the month, for date hierarchies in Power BI/Excel
        period || '-01'                AS period_date,
        category,
        subcategory,
        budget_amount,
        actual_amount,
        budget_variance,
        budget_variance_pct,
        prior_year_actual,
        yoy_variance,
        yoy_variance_pct,
        is_material,
        is_favorable,
        variance_status,
        risk_level,
        commentary
    FROM variance_analysis
    ORDER BY period, category, subcategory
"""

COLUMNS = ["period", "period_date", "category", "subcategory",
           "budget_amount", "actual_amount",
           "budget_variance", "budget_variance_pct",
           "prior_year_actual", "yoy_variance", "yoy_variance_pct",
           "is_material", "is_favorable", "variance_status",
           "risk_level", "commentary"]


class ModelFeedExporter:

    def __init__(self, db: BudgetDatabase):
        self.db = db

    def fetch_rows(self):
        rows = self.db.execute_query(EXPORT_QUERY)
        if not rows:
            raise RuntimeError(
                "variance_analysis is empty - run the pipeline first.")
        return rows

    def export_csv(self, rows, path=None):
        """
        Write the feed as CSV with columns in COLUMNS order.
        """
        path = path or config.MODEL_FEED_CSV
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=COLUMNS)
                writer.writeheader()
                for row in rows:
                    writer.writerow(dict(row))
            log.info(f"CSV feed: {path} ({len(rows)} rows)")
        except OSError as e:
            raise RuntimeError(f"Could not write CSV '{path}': {e}")

    def export_xlsx(self, rows, path=None):
        """
        Write the feed as a single unformatted sheet holding an Excel
        Table named 'ModelFeed', so downstream formulas and Power Query
        can reference it by name regardless of row count.
        """
        path = path or config.MODEL_FEED_XLSX
        wb = Workbook()
        ws = wb.active
        ws.title = "ModelFeed"

        ws.append(COLUMNS)
        for row in rows:
            d = dict(row)
            ws.append([d[c] for c in COLUMNS])

        last_col = get_column_letter(len(COLUMNS))
        table = Table(displayName="ModelFeed",
                      ref=f"A1:{last_col}{len(rows) + 1}")
        table.tableStyleInfo = TableStyleInfo(name="TableStyleLight1",
                                              showRowStripes=False)
        ws.add_table(table)
        ws.freeze_panes = "A2"

        try:
            wb.save(path)
            log.info(f"Excel feed: {path} (table 'ModelFeed')")
        except PermissionError:
            raise RuntimeError(
                f"Could not save '{path}' - close it in Excel and re-run.")

    def export_all(self):
        log.info("Exporting model feed...")
        rows = self.fetch_rows()
        self.export_csv(rows)
        self.export_xlsx(rows)
        return len(rows)


if __name__ == "__main__":
    db = BudgetDatabase()
    try:
        db.connect()
        exporter = ModelFeedExporter(db)
        n = exporter.export_all()
        log.info(f"Model feed exported ({n} rows)")
        print("\nHOW TO CONNECT:")
        print("  EA model:  Data > Get Data > From Workbook > model_feed.xlsx")
        print("             then reference the 'ModelFeed' table")
        print("  Power BI:  Get Data > Text/CSV > model_feed.csv")
        print("             (or connect to budget_analysis.db via ODBC)")
    except RuntimeError as e:
        log.error(str(e))
    finally:
        db.close()

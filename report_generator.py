"""
report_generator.py
===================
Builds the formatted Excel variance report with Summary, Detail and
Flagged tabs.

Inputs:  variance_analysis table
Outputs: variance_report.xlsx
"""

import config
from database import BudgetDatabase
from logger_setup import get_logger
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo
from openpyxl.formatting.rule import CellIsRule

log = get_logger(__name__)


# ─────────────────────────────────────────────────────────────
# STYLE CONSTANTS
# ─────────────────────────────────────────────────────────────

HEADER_FILL  = PatternFill("solid", fgColor="1F4E78")
HEADER_FONT  = Font(color="FFFFFF", bold=True, size=11)
TITLE_FONT   = Font(bold=True, size=14, color="1F4E78")
LABEL_FONT   = Font(bold=True, size=11)
THIN_BORDER  = Border(bottom=Side(style="thin", color="CCCCCC"))

FAVORABLE_FILL    = PatternFill("solid", fgColor="C6EFCE")
UNFAVORABLE_FILL  = PatternFill("solid", fgColor="FFC7CE")
MATERIAL_FONT     = Font(bold=True, color="9C0006")

CURRENCY_FMT = '₹#,##0'
PCT_FMT      = '0.0%'


# ─────────────────────────────────────────────────────────────
# REPORT GENERATOR CLASS
# ─────────────────────────────────────────────────────────────

class ReportGenerator:

    def __init__(self, db: BudgetDatabase):
        self.db = db
        self.wb = Workbook()

    # ───────────────────────────────────────────────────────
    # SHEET 1: SUMMARY
    # ───────────────────────────────────────────────────────
    def build_summary_sheet(self):
        ws = self.wb.active
        ws.title = "Summary"

        ws["A1"] = "Budget Variance Analysis - Executive Summary"
        ws["A1"].font = TITLE_FONT
        ws.merge_cells("A1:F1")

        row = 3
        ws.cell(row=row, column=1, value="Totals by Category (Full Year)").font = LABEL_FONT
        row += 1

        headers = ["Category", "Total Budget", "Total Actual", "Total Variance",
                    "Variance %", "Material Items"]
        for col, h in enumerate(headers, start=1):
            c = ws.cell(row=row, column=col, value=h)
            c.fill = HEADER_FILL
            c.font = HEADER_FONT
            c.alignment = Alignment(horizontal="center")
        row += 1

        totals = self.db.execute_query("""
            SELECT
                category,
                SUM(budget_amount) AS total_budget,
                SUM(actual_amount) AS total_actual,
                SUM(budget_variance) AS total_variance,
                SUM(is_material) AS material_count
            FROM variance_analysis
            GROUP BY category
            ORDER BY category
        """)

        for r in totals:
            ws.cell(row=row, column=1, value=r["category"].title())
            ws.cell(row=row, column=2, value=r["total_budget"]).number_format = CURRENCY_FMT
            ws.cell(row=row, column=3, value=r["total_actual"]).number_format = CURRENCY_FMT
            var_cell = ws.cell(row=row, column=4, value=r["total_variance"])
            var_cell.number_format = CURRENCY_FMT

            pct = (r["total_variance"] / r["total_budget"]) if r["total_budget"] else 0
            ws.cell(row=row, column=5, value=pct).number_format = PCT_FMT

            mat_cell = ws.cell(row=row, column=6, value=r["material_count"])
            if r["material_count"] > 0:
                mat_cell.font = MATERIAL_FONT

            row += 1

        row += 2
        ws.cell(row=row, column=1, value="Favorable vs Unfavorable (All Items)").font = LABEL_FONT
        row += 1

        split = self.db.execute_query("""
            SELECT is_favorable, COUNT(*) AS n
            FROM variance_analysis
            GROUP BY is_favorable
        """)
        split_map = {r["is_favorable"]: r["n"] for r in split}

        ws.cell(row=row, column=1, value="Favorable")
        ws.cell(row=row, column=2, value=split_map.get(1, 0)).fill = FAVORABLE_FILL
        row += 1
        ws.cell(row=row, column=1, value="Unfavorable")
        ws.cell(row=row, column=2, value=split_map.get(0, 0)).fill = UNFAVORABLE_FILL
        row += 2

        ws.cell(row=row, column=1, value="High-Risk Material Variances").font = LABEL_FONT
        row += 1

        high_risk = self.db.execute_query("""
            SELECT COUNT(*) AS n FROM variance_analysis
            WHERE is_material = 1 AND risk_level = 'HIGH'
        """)[0]["n"]

        cell = ws.cell(row=row, column=1, value=f"{high_risk} item(s) flagged HIGH risk - see 'Flagged' tab")
        if high_risk > 0:
            cell.font = MATERIAL_FONT

        widths = [22, 16, 16, 16, 12, 15]
        for i, w in enumerate(widths, start=1):
            ws.column_dimensions[get_column_letter(i)].width = w

        log.info("Summary sheet built")

    # ───────────────────────────────────────────────────────
    # SHEET 2: DETAIL
    # ───────────────────────────────────────────────────────
    def build_detail_sheet(self):
        ws = self.wb.create_sheet("Detail")

        columns = [
            "period", "category", "subcategory",
            "budget_amount", "actual_amount",
            "budget_variance", "budget_variance_pct",
            "prior_year_actual", "yoy_variance", "yoy_variance_pct",
            "is_material", "variance_status", "risk_level", "commentary"
        ]
        display_headers = [
            "Period", "Category", "Subcategory",
            "Budget", "Actual",
            "Budget Variance", "Variance %",
            "Prior Year", "YoY Variance", "YoY %",
            "Material?", "Status", "Risk Level", "Commentary"
        ]

        for col, h in enumerate(display_headers, start=1):
            c = ws.cell(row=1, column=col, value=h)
            c.fill = HEADER_FILL
            c.font = HEADER_FONT
            c.alignment = Alignment(horizontal="center")

        rows = self.db.execute_query(f"""
            SELECT {', '.join(columns)}
            FROM variance_analysis
            ORDER BY period, category, subcategory
        """)

        for r_idx, r in enumerate(rows, start=2):
            for c_idx, col_name in enumerate(columns, start=1):
                value = r[col_name]

                if col_name == "is_material":
                    value = "Yes" if value == 1 else "No"

                cell = ws.cell(row=r_idx, column=c_idx, value=value)

                if col_name in ("budget_amount", "actual_amount",
                                 "budget_variance", "prior_year_actual", "yoy_variance"):
                    cell.number_format = CURRENCY_FMT

                if col_name in ("budget_variance_pct", "yoy_variance_pct"):
                    if value is not None:
                        cell.value = value / 100
                        cell.number_format = PCT_FMT

        last_row = len(rows) + 1
        last_col = len(columns)

        table_ref = f"A1:{get_column_letter(last_col)}{last_row}"
        table = Table(displayName="VarianceDetail", ref=table_ref)
        table.tableStyleInfo = TableStyleInfo(
            name="TableStyleMedium2", showRowStripes=True
        )
        ws.add_table(table)

        # Color-code the Status column by its four states
        status_col_letter = get_column_letter(columns.index("variance_status") + 1)
        status_range = f"{status_col_letter}2:{status_col_letter}{last_row}"

        ws.conditional_formatting.add(
            status_range,
            CellIsRule(operator="equal", formula=['"Unfavorable"'], fill=UNFAVORABLE_FILL)
        )
        ws.conditional_formatting.add(
            status_range,
            CellIsRule(operator="equal", formula=['"Favorable"'], fill=FAVORABLE_FILL)
        )
        ws.conditional_formatting.add(
            status_range,
            CellIsRule(operator="equal", formula=['"Deviation"'],
                       fill=PatternFill("solid", fgColor="FFEB9C"))
        )

        widths = [10, 14, 22, 14, 14, 14, 10, 14, 14, 10, 10, 12, 12, 60]
        for i, w in enumerate(widths, start=1):
            ws.column_dimensions[get_column_letter(i)].width = w

        ws.freeze_panes = "A2"

        log.info(f"Detail sheet built ({len(rows)} rows)")

    # ───────────────────────────────────────────────────────
    # SHEET 3: FLAGGED ITEMS
    # ───────────────────────────────────────────────────────
    def build_flagged_sheet(self):
        ws = self.wb.create_sheet("Flagged")

        ws["A1"] = "Flagged Items - Material Variances Requiring Review"
        ws["A1"].font = TITLE_FONT
        ws.merge_cells("A1:H1")

        headers = ["Period", "Category", "Subcategory", "Budget",
                   "Actual", "Variance", "Variance %", "Risk Level"]
        header_row = 3
        for col, h in enumerate(headers, start=1):
            c = ws.cell(row=header_row, column=col, value=h)
            c.fill = HEADER_FILL
            c.font = HEADER_FONT
            c.alignment = Alignment(horizontal="center")

        rows = self.db.execute_query("""
            SELECT period, category, subcategory, budget_amount,
                   actual_amount, budget_variance, budget_variance_pct, risk_level
            FROM variance_analysis
            WHERE is_material = 1
            ORDER BY
                CASE risk_level
                    WHEN 'HIGH' THEN 1
                    WHEN 'MEDIUM' THEN 2
                    ELSE 3
                END,
                ABS(budget_variance) DESC
        """)

        r_idx = header_row + 1
        for r in rows:
            ws.cell(row=r_idx, column=1, value=r["period"])
            ws.cell(row=r_idx, column=2, value=r["category"].title())
            ws.cell(row=r_idx, column=3, value=r["subcategory"])
            ws.cell(row=r_idx, column=4, value=r["budget_amount"]).number_format = CURRENCY_FMT
            ws.cell(row=r_idx, column=5, value=r["actual_amount"]).number_format = CURRENCY_FMT

            var_cell = ws.cell(row=r_idx, column=6, value=r["budget_variance"])
            var_cell.number_format = CURRENCY_FMT
            var_cell.font = MATERIAL_FONT

            pct_cell = ws.cell(row=r_idx, column=7, value=r["budget_variance_pct"] / 100)
            pct_cell.number_format = PCT_FMT

            risk_cell = ws.cell(row=r_idx, column=8, value=r["risk_level"])
            if r["risk_level"] == "HIGH":
                risk_cell.fill = UNFAVORABLE_FILL
                risk_cell.font = MATERIAL_FONT
            elif r["risk_level"] == "MEDIUM":
                risk_cell.fill = PatternFill("solid", fgColor="FFEB9C")

            r_idx += 1

        if not rows:
            ws.cell(row=header_row + 1, column=1,
                    value="No material variances found.")

        widths = [10, 16, 22, 14, 14, 14, 10, 12]
        for i, w in enumerate(widths, start=1):
            ws.column_dimensions[get_column_letter(i)].width = w

        ws.freeze_panes = "A4"

        log.info(f"Flagged sheet built ({len(rows)} flagged items)")

    # ───────────────────────────────────────────────────────
    # SAVE
    # ───────────────────────────────────────────────────────
    def save(self, filename=None):
        """
        Save the workbook. Raises RuntimeError if the file is locked
        (typically because it is open in Excel).
        """
        path = str(filename or config.REPORT_OUTPUT_PATH)
        try:
            self.wb.save(path)
            log.info(f"Report saved: {path}")
        except PermissionError:
            raise RuntimeError(
                f"Could not save '{path}' - the file is likely open in Excel. "
                f"Close it and run this script again."
            )


# ─────────────────────────────────────────────────────────────
# STANDALONE RUN
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    db = BudgetDatabase()

    try:
        db.connect()

        row_count = db.execute_query(
            "SELECT COUNT(*) as n FROM variance_analysis"
        )[0]["n"]

        if row_count == 0:
            raise RuntimeError(
                "variance_analysis table is empty. "
                "Run variance_calculator.py first."
            )

        log.info("Building Excel report...")
        report = ReportGenerator(db)
        report.build_summary_sheet()
        report.build_detail_sheet()
        report.build_flagged_sheet()
        report.save()

        log.info("Report generation complete")

    except RuntimeError as e:
        log.error(str(e))
    finally:
        db.close()

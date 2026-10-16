"""
commentary_generator.py
=======================
Generates plain-English commentary for material variances, worded
according to each line's variance status.

Inputs:  variance_analysis table
Outputs: variance_commentary.txt
"""

import config
from database import BudgetDatabase
from logger_setup import get_logger

log = get_logger(__name__)


# ─────────────────────────────────────────────────────────────
# FORMATTING HELPERS
# ─────────────────────────────────────────────────────────────

def fmt_currency(amount):
    return f"₹{abs(amount):,.0f}"


def fmt_pct(pct):
    return f"{abs(pct):.1f}%"


def period_to_words(period):
    year, month = period.split("-")
    months = ["", "January", "February", "March", "April", "May", "June",
              "July", "August", "September", "October", "November", "December"]
    return f"{months[int(month)]} {year}"


# ─────────────────────────────────────────────────────────────
# SENTENCE BUILDERS
# ─────────────────────────────────────────────────────────────

def build_headline(row):
    """What happened, how big, in which period."""
    period_str   = period_to_words(row["period"])
    subcat       = row["subcategory"].replace("_", " ")
    category     = row["category"]
    variance     = row["budget_variance"]
    variance_pct = row["budget_variance_pct"]

    amount_str = fmt_currency(variance)
    pct_str = fmt_pct(variance_pct) if variance_pct is not None else "n/a"

    if category == "revenue":
        verb = "exceeded budget" if variance > 0 else "fell short of budget"
        return (f"{subcat.title()} revenue {verb} by {amount_str} "
                f"({pct_str}) in {period_str}.")

    elif category in ("cogs", "opex", "capex"):
        verb = "exceeded budget" if variance > 0 else "came in under budget"
        return (f"{subcat.title()} costs {verb} by {amount_str} "
                f"({pct_str}) in {period_str}.")

    elif category == "headcount":
        direction = "above" if variance > 0 else "below"
        return (f"{subcat.title()} headcount was {abs(variance):.0f} "
                f"{direction} plan in {period_str}.")

    else:  # working_capital etc.
        direction = "higher" if variance > 0 else "lower"
        return (f"{subcat.title()} was {amount_str} ({pct_str}) {direction} "
                f"than budget in {period_str}.")


def build_assessment(row):
    """
    The "so what" sentence - now driven by variance_status, so
    stable categories get neutral language.
    """
    status = row["variance_status"]
    risk   = row["risk_level"]

    if risk == "HIGH":
        urgency = "flagged as high priority for review."
    elif risk == "MEDIUM":
        urgency = "flagged for routine review."
    else:
        urgency = "noted for awareness."

    if status == "Favorable":
        return f"This is a favorable variance, {urgency}"
    elif status == "Unfavorable":
        return f"This is an unfavorable variance, {urgency}"
    elif status == "Deviation":
        # Neutral language for stable categories - a deviation is
        # worth looking at, but isn't inherently good or bad.
        return f"This is a deviation from plan, {urgency}"
    else:  # On Plan - rarely material, but handle it
        return f"This remains within the planned tolerance band, {urgency}"


def build_yoy_context(row):
    """Optional YoY sentence; None if no prior-year data."""
    yoy_pct = row["yoy_variance_pct"]
    if yoy_pct is None:
        return None
    if yoy_pct > 0:
        return f"Compared to the same period last year, this is up {fmt_pct(yoy_pct)}."
    elif yoy_pct < 0:
        return f"Compared to the same period last year, this is down {fmt_pct(yoy_pct)}."
    return "This is flat compared to the same period last year."


def generate_commentary(row):
    parts = [build_headline(row), build_assessment(row)]
    yoy = build_yoy_context(row)
    if yoy:
        parts.append(yoy)
    return " ".join(parts)


# ─────────────────────────────────────────────────────────────
# MAIN CLASS
# ─────────────────────────────────────────────────────────────

class CommentaryGenerator:

    def __init__(self, db: BudgetDatabase):
        self.db = db

    def generate_all(self):
        """Generate commentary for every material row, write back to DB."""
        flagged_rows = self.db.execute_query("""
            SELECT id, period, category, subcategory,
                   budget_amount, actual_amount,
                   budget_variance, budget_variance_pct,
                   prior_year_actual, yoy_variance, yoy_variance_pct,
                   is_material, is_favorable, risk_level, variance_status
            FROM variance_analysis
            WHERE is_material = 1
        """)

        log.info(f"Generating commentary for {len(flagged_rows)} flagged items...")

        for row in flagged_rows:
            text = generate_commentary(row)
            self.db.conn.execute(
                "UPDATE variance_analysis SET commentary = ? WHERE id = ?",
                (text, row["id"])
            )

        self.db.conn.execute(
            "UPDATE variance_analysis SET commentary = NULL WHERE is_material = 0"
        )
        self.db.conn.commit()
        log.info(f"Commentary written for {len(flagged_rows)} rows")
        return flagged_rows

    def write_narrative_file(self, filepath=None):
        """Standalone exceptions-narrative text file, grouped by risk."""
        path = filepath or config.COMMENTARY_OUTPUT_PATH

        rows = self.db.execute_query("""
            SELECT period, category, subcategory, risk_level, commentary
            FROM variance_analysis
            WHERE is_material = 1
            ORDER BY
                CASE risk_level
                    WHEN 'HIGH' THEN 1
                    WHEN 'MEDIUM' THEN 2
                    ELSE 3
                END,
                period
        """)

        lines = ["BUDGET VARIANCE - EXCEPTIONS COMMENTARY",
                 "=" * 60, ""]

        current_risk = None
        for row in rows:
            if row["risk_level"] != current_risk:
                current_risk = row["risk_level"]
                lines.append(f"\n--- {current_risk} RISK ---\n")
            lines.append(f"[{row['period']} | {row['category'].title()} - "
                         f"{row['subcategory'].replace('_', ' ').title()}]")
            lines.append(row["commentary"])
            lines.append("")

        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            log.info(f"Narrative saved: {path}")
        except OSError as e:
            raise RuntimeError(f"Could not write commentary file '{path}': {e}")


if __name__ == "__main__":
    db = BudgetDatabase()
    try:
        db.connect()

        n = db.execute_query(
            "SELECT COUNT(*) AS n FROM variance_analysis")[0]["n"]
        if n == 0:
            raise RuntimeError(
                "variance_analysis is empty - run variance_calculator.py first.")

        gen = CommentaryGenerator(db)
        flagged = gen.generate_all()
        gen.write_narrative_file()

        print("\n" + "=" * 65)
        print("SAMPLE COMMENTARY")
        print("=" * 65)
        for row in flagged[:3]:
            print(f"\n[{row['period']} | {row['subcategory']} | "
                  f"{row['variance_status']}]")
            print(f"  {generate_commentary(row)}")

        log.info("Module 4 complete")
    except RuntimeError as e:
        log.error(str(e))
    finally:
        db.close()

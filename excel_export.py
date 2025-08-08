from datetime import datetime
import pandas as pd
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

NAVY = "2F455C"
WHITE = "FFFFFF"

def _style_headers(ws):
    max_col = ws.max_column
    for col in range(1, max_col + 1):
        cell = ws.cell(row=1, column=col)
        cell.font = Font(bold=True, color=WHITE)
        cell.fill = PatternFill(start_color=NAVY, end_color=NAVY, fill_type="solid")
        cell.alignment = Alignment(vertical="center")
        ws.column_dimensions[get_column_letter(col)].width = min(35, len(str(cell.value)) + 5)
    ws.freeze_panes = "A2"

def export_errors_multitab(errors_df: pd.DataFrame, summary_df: pd.DataFrame,
                           group_name: str = "", period: str = "") -> bytes:
    from io import BytesIO
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        meta = pd.DataFrame({
            "Field": ["Group Name", "Reporting Period", "Generated At"],
            "Value": [group_name, period, datetime.now().strftime("%Y-%m-%d %H:%M:%S")]
        })
        meta.to_excel(writer, sheet_name="Summary", index=False, startrow=0)
        summary_df.to_excel(writer, sheet_name="Summary", index=False, startrow=5)
        ws = writer.book["Summary"]
        _style_headers(ws)

        if errors_df.empty:
            pd.DataFrame({"Info": ["No errors found."]}).to_excel(writer, sheet_name="No Errors", index=False)
            _style_headers(writer.book["No Errors"])
        else:
            for etype, df in errors_df.groupby("Error Type", dropna=False):
                name = (str(etype) if etype else "Unknown")[:28]
                df.to_excel(writer, sheet_name=name, index=False)
                _style_headers(writer.book[name])
            errors_df.to_excel(writer, sheet_name="All Errors", index=False)
            _style_headers(writer.book["All Errors"])
    return output.getvalue()

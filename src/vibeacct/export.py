"""Render a report (title, notes, headers, rows) as CSV, Markdown, or HTML (for PDF).

Row values are str, int, or Decimal; Decimals are monetary amounts.
"""

import csv
import html
import io
from decimal import Decimal

NEG_HTML = "#be1e1e"
POS_HTML = "#149628"


def _money(value):
    """Signed $ amount: exported files may be printed or viewed without color."""
    return f"{'-' if value < 0 else ''}${abs(value):,.2f}"


def to_csv(headers, rows):
    """Plain numbers (e.g. -709.30) so spreadsheets can total them."""
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(headers)
    for row in rows:
        w.writerow([f"{v:.2f}" if isinstance(v, Decimal) else v for v in row])
    return out.getvalue()


def _md_cell(text):
    return str(text).replace("|", "\\|")


def to_markdown(title, notes, headers, rows):
    money_cols = {c for row in rows for c, v in enumerate(row) if isinstance(v, Decimal)}
    lines = [f"# {title}", ""]
    lines += [f"- {n}" for n in notes]
    lines += ["", "| " + " | ".join(_md_cell(h) for h in headers) + " |"]
    lines.append("|" + "|".join("---:" if c in money_cols else "---" for c in range(len(headers))) + "|")
    for row in rows:
        lines.append("| " + " | ".join(_md_cell(_money(v) if isinstance(v, Decimal) else v) for v in row) + " |")
    return "\n".join(lines) + "\n"


def to_html(title, notes, headers, rows, total_prefix=None, font_pt=9):
    """Simple table document; QTextDocument renders it to PDF. Total rows are bold."""

    def cell(v):
        if isinstance(v, Decimal):
            color = NEG_HTML if v < 0 else POS_HTML if v > 0 else None
            style = f' style="color:{color}"' if color else ""
            return f'<td align="right" nowrap{style}>{_money(v)}</td>'
        align = ' align="right"' if isinstance(v, int) else ""
        return f"<td nowrap{align}>{html.escape(str(v))}</td>"

    parts = [f'<body style="font-size:{font_pt}pt">', f"<h2>{html.escape(title)}</h2>"]
    parts += [f'<p style="margin:0">{html.escape(n)}</p>' for n in notes]
    parts.append('<br><table border="1" cellspacing="0" cellpadding="3" style="border-collapse:collapse">')
    parts.append("<tr>" + "".join(f"<th nowrap>{html.escape(h)}</th>" for h in headers) + "</tr>")
    for row in rows:
        bold = total_prefix and isinstance(row[0], str) and row[0].startswith(total_prefix)
        tr = "".join(cell(v) for v in row)
        parts.append(f'<tr style="font-weight:bold">{tr}</tr>' if bold else f"<tr>{tr}</tr>")
    parts.append("</table></body>")
    return "\n".join(parts)

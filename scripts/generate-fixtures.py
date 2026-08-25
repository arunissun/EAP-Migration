"""Generate sanitized local fixtures used by the example cases.

These files contain no source-document or personal data. They only make the
example cases runnable through local validation and mocked tests.
"""

from __future__ import annotations

import base64
from pathlib import Path

from openpyxl import Workbook
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

ROOT = Path(__file__).parents[1]
ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def write_pdf(path: Path, title: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    styles = getSampleStyleSheet()
    document = SimpleDocTemplate(
        str(path), pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=2 * cm
    )
    document.build(
        [
            Paragraph(title, styles["Title"]),
            Spacer(1, 1 * cm),
            Paragraph(
                "Sanitized synthetic attachment for local IFRC GO EAP migration validation.",
                styles["BodyText"],
            ),
        ]
    )


def write_workbook(path: Path, title: str, rows: list[tuple[str, int]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Budget"
    sheet.append(["Item", "CHF"])
    for row in rows:
        sheet.append(row)
    sheet.append(["Total", f"=SUM(B2:B{len(rows) + 1})"])
    metadata = workbook.create_sheet("Metadata")
    metadata.append(["Fixture", title])
    metadata.append(["Synthetic", True])
    workbook.save(path)


def write_cover(path: Path, title: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(ONE_PIXEL_PNG)


def main() -> None:
    fiji = ROOT / "fixtures" / "fiji"
    write_cover(fiji / "cover.png", "Fiji cover")
    for name in ("risk-evidence", "protocol-evidence", "early-action-evidence"):
        write_pdf(fiji / f"{name}.pdf", f"Fiji {name}")
    write_workbook(
        fiji / "budget.xlsx",
        "Fiji simplified EAP budget",
        [("Planned operations", 74306), ("Enabling approaches", 37506)],
    )

    full = ROOT / "fixtures" / "full"
    for key in (
        "hazard-selection",
        "vulnerability",
        "prioritized-impact",
        "risk-analysis-relevant",
        "forecast-selection",
        "impact-level",
        "intervention-area",
        "trigger-model-relevant",
        "action-selection",
        "evidence-base-relevant",
        "implementation",
        "activation-system",
        "activation-relevant",
        "meal-relevant",
        "capacity-relevant",
    ):
        write_pdf(full / f"{key}.pdf", f"Synthetic Full EAP {key}")
    write_cover(full / "cover.png", "Synthetic Full EAP cover")
    for key in ("budget", "forecast-table", "theory-of-change"):
        write_workbook(
            full / f"{key}.xlsx",
            f"Synthetic Full EAP {key}",
            [("Synthetic row", 100000)],
        )


if __name__ == "__main__":
    main()

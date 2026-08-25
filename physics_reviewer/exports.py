import csv
import io
from datetime import datetime, timezone
from typing import Any
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

from physics_reviewer.schemas import BatchStatusResponse


EXPORT_COLUMNS = [
    "task_id",
    "filename",
    "title",
    "status",
    "created_at",
    "updated_at",
    "overall_score",
    "novelty",
    "physics_correctness",
    "method_rigor",
    "reproducibility",
    "citation_quality",
    "writing_quality",
    "summary",
    "strengths",
    "weaknesses",
    "required_checks",
    "uncertainty_notes",
    "error",
]


def batch_export_rows(batch: BatchStatusResponse) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for task in batch.tasks:
        row: dict[str, Any] = {
            "task_id": task.task_id,
            "filename": task.filename or "",
            "title": task.title or "",
            "status": task.status,
            "created_at": task.created_at,
            "updated_at": task.updated_at,
            "error": task.error or "",
        }
        if task.result:
            report = task.result.report
            row.update(report.scores.model_dump())
            row.update(
                {
                    "title": report.title or row["title"],
                    "summary": report.summary,
                    "strengths": _join(report.strengths),
                    "weaknesses": _join(report.weaknesses),
                    "required_checks": _join(report.required_checks),
                    "uncertainty_notes": _join(report.uncertainty_notes),
                }
            )
        rows.append({column: row.get(column, "") for column in EXPORT_COLUMNS})
    return rows


def render_batch_csv(batch: BatchStatusResponse) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=EXPORT_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(batch_export_rows(batch))
    # BOM lets desktop Excel identify UTF-8 Chinese text correctly.
    return ("\ufeff" + buffer.getvalue()).encode("utf-8")


def render_batch_xlsx(batch: BatchStatusResponse) -> bytes:
    """Create a small standards-compliant XLSX workbook using only the standard library."""
    rows = [EXPORT_COLUMNS, *[[row[column] for column in EXPORT_COLUMNS] for row in batch_export_rows(batch)]]
    sheet_rows = []
    for row_index, values in enumerate(rows, start=1):
        cells = []
        for column_index, value in enumerate(values, start=1):
            reference = f"{_column_name(column_index)}{row_index}"
            style = ' s="1"' if row_index == 1 else ""
            cells.append(
                f'<c r="{reference}" t="inlineStr"{style}><is><t>{escape(str(value))}</t></is></c>'
            )
        sheet_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')

    created_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    workbook = io.BytesIO()
    with ZipFile(workbook, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _CONTENT_TYPES)
        archive.writestr("_rels/.rels", _ROOT_RELS)
        archive.writestr("xl/workbook.xml", _WORKBOOK)
        archive.writestr("xl/_rels/workbook.xml.rels", _WORKBOOK_RELS)
        archive.writestr("xl/styles.xml", _STYLES)
        archive.writestr(
            "docProps/core.xml",
            _CORE_PROPERTIES.format(created_at=created_at),
        )
        archive.writestr("docProps/app.xml", _APP_PROPERTIES)
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            _SHEET_TEMPLATE.format(rows="".join(sheet_rows)),
        )
    return workbook.getvalue()


def _join(items: list[str]) -> str:
    return " | ".join(item.strip() for item in items if item.strip())


def _column_name(index: int) -> str:
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>"""
_ROOT_RELS = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>"""
_WORKBOOK = """<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Reviews" sheetId="1" r:id="rId1"/></sheets></workbook>"""
_WORKBOOK_RELS = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>"""
_STYLES = """<?xml version="1.0" encoding="UTF-8"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts><fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf/></cellStyleXfs><cellXfs count="2"><xf xfId="0"/><xf xfId="0" fontId="1" applyFont="1"/></cellXfs></styleSheet>"""
_CORE_PROPERTIES = """<?xml version="1.0" encoding="UTF-8"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><dc:creator>Physics Reviewer</dc:creator><dcterms:created xsi:type="dcterms:W3CDTF">{created_at}</dcterms:created></cp:coreProperties>"""
_APP_PROPERTIES = """<?xml version="1.0" encoding="UTF-8"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"><Application>Physics Reviewer</Application></Properties>"""
_SHEET_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><sheetFormatPr defaultRowHeight="15"/><cols><col min="1" max="19" width="18" customWidth="1"/></cols><sheetData>{rows}</sheetData><autoFilter ref="A1:S1"/></worksheet>"""

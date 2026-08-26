"""The running master catalog: a canonical JSON store rendered to XLSX + DOCX.

The JSON file (``master_catalog.json``) is the single source of truth. Each run
upserts records keyed by absolute file path, so re-scanning a folder updates
existing entries instead of duplicating them. The spreadsheet and Word views are
always re-rendered from the JSON so the three stay perfectly in sync.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from . import docx_style as ds
from . import utils
from .models import MASTER_COLUMNS, MediaInfo

SCHEMA_VERSION = 2


class MasterCatalog:
    """Load, update, and render the running master catalog."""

    def __init__(self, store_path: str) -> None:
        self.store_path = store_path
        self.records: Dict[str, Dict[str, Any]] = {}
        self.updated_at: Optional[str] = None

    # -- persistence ------------------------------------------------------

    def load(self) -> "MasterCatalog":
        if os.path.exists(self.store_path):
            try:
                with open(self.store_path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
                self.records = data.get("records", {}) or {}
                self.updated_at = data.get("updated_at")
            except (json.JSONDecodeError, OSError):
                # Corrupt or unreadable store: start fresh but keep a backup.
                self._backup_corrupt_store()
                self.records = {}
        return self

    def _backup_corrupt_store(self) -> None:
        try:
            backup = self.store_path + ".corrupt"
            os.replace(self.store_path, backup)
        except OSError:
            pass

    def save_json(self, generated_at: datetime) -> None:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "generated_by": "MetaScraper",
            "updated_at": generated_at.isoformat(),
            "record_count": len(self.records),
            "records": self.records,
        }
        os.makedirs(os.path.dirname(os.path.abspath(self.store_path)), exist_ok=True)
        tmp = self.store_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
        os.replace(tmp, self.store_path)

    # -- mutation ---------------------------------------------------------

    def upsert(self, info: MediaInfo, generated_at: datetime) -> bool:
        """Insert or update a record. Returns True if it was newly added."""
        row = info.master_row()
        key = info.path
        existing = self.records.get(key)
        stamp = generated_at.isoformat()
        if existing:
            row["first_cataloged_at"] = existing.get("first_cataloged_at", stamp)
            row["cataloged_at"] = stamp
            self.records[key] = row
            return False
        row["first_cataloged_at"] = stamp
        row["cataloged_at"] = stamp
        self.records[key] = row
        return True

    # -- derived views ----------------------------------------------------

    def sorted_records(self) -> List[Dict[str, Any]]:
        return sorted(
            self.records.values(),
            key=lambda r: (str(r.get("folder") or ""), str(r.get("file_name") or "")),
        )

    def summary(self) -> Dict[str, Any]:
        records = list(self.records.values())
        total_bytes = sum(r.get("size_bytes") or 0 for r in records)
        total_seconds = sum(r.get("duration_seconds") or 0 for r in records)
        by_kind: Dict[str, int] = {}
        for record in records:
            kind = record.get("media_kind") or "Other"
            by_kind[kind] = by_kind.get(kind, 0) + 1
        return {
            "count": len(records),
            "total_bytes": total_bytes,
            "total_seconds": total_seconds,
            "by_kind": by_kind,
        }

    # -- rendering: XLSX --------------------------------------------------

    def render_xlsx(self, output_path: str, generated_at: datetime) -> str:
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Catalog"

        headers = [label for _, label in MASTER_COLUMNS]
        keys = [key for key, _ in MASTER_COLUMNS]

        header_fill = PatternFill("solid", fgColor="1B3A5B")
        header_font = Font(name="Calibri", bold=True, color="FFFFFF", size=10)
        cell_font = Font(name="Calibri", size=10, color="1F2A37")
        zebra_fill = PatternFill("solid", fgColor="F3F5F8")
        thin = Side(style="thin", color="D6DBE2")
        border = Border(left=thin, right=thin, top=thin, bottom=thin)
        wrap = Alignment(vertical="center", wrap_text=False)

        for col_idx, header in enumerate(headers, start=1):
            cell = sheet.cell(row=1, column=col_idx, value=header)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(vertical="center", horizontal="left")
            cell.border = border

        for r_idx, record in enumerate(self.sorted_records(), start=2):
            for c_idx, key in enumerate(keys, start=1):
                value = record.get(key)
                cell = sheet.cell(row=r_idx, column=c_idx, value=value)
                cell.font = cell_font
                cell.alignment = wrap
                cell.border = border
                if r_idx % 2 == 0:
                    cell.fill = zebra_fill

        # Column widths sized to content (with sane caps).
        for c_idx, key in enumerate(keys, start=1):
            values = [str(headers[c_idx - 1])]
            values += [
                str(rec.get(key)) for rec in self.records.values()
                if rec.get(key) is not None
            ]
            width = min(max((len(v) for v in values), default=10) + 3, 55)
            sheet.column_dimensions[get_column_letter(c_idx)].width = width

        sheet.freeze_panes = "A2"
        last_col = get_column_letter(len(headers))
        sheet.auto_filter.ref = f"A1:{last_col}1"

        # A compact summary sheet.
        self._add_xlsx_summary(workbook, generated_at)

        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        workbook.save(output_path)
        return output_path

    def _add_xlsx_summary(self, workbook: Workbook, generated_at: datetime) -> None:
        summary = self.summary()
        sheet = workbook.create_sheet("Summary")
        title_font = Font(name="Calibri", bold=True, size=14, color="1B3A5B")
        label_font = Font(name="Calibri", bold=True, size=11, color="1B3A5B")
        value_font = Font(name="Calibri", size=11, color="1F2A37")

        sheet["A1"] = "Master Media Catalog"
        sheet["A1"].font = title_font
        rows = [
            ("Total files", summary["count"]),
            ("Total duration", utils.human_duration(summary["total_seconds"])),
            ("Total size", utils.human_file_size(summary["total_bytes"])),
            ("Last updated", utils.format_datetime(generated_at)),
        ]
        for kind, count in sorted(summary["by_kind"].items()):
            rows.append((f"{kind} files", count))

        for idx, (label, value) in enumerate(rows, start=3):
            lc = sheet.cell(row=idx, column=1, value=label)
            vc = sheet.cell(row=idx, column=2, value=value)
            lc.font = label_font
            vc.font = value_font
        sheet.column_dimensions["A"].width = 22
        sheet.column_dimensions["B"].width = 30

    # -- rendering: DOCX --------------------------------------------------

    # A trimmed column set that fits comfortably on a landscape page.
    DOCX_COLUMNS = [
        ("file_name", "File Name", 2.1),
        ("media_kind", "Type", 0.7),
        ("duration", "Duration", 0.8),
        ("file_size", "Size", 0.85),
        ("resolution", "Resolution", 1.1),
        ("frame_rate", "FPS", 0.7),
        ("video_codec", "Video", 0.8),
        ("audio_codec", "Audio", 0.75),
        ("camera_model", "Camera", 1.2),
        ("recorded_at", "Recorded", 1.4),
        ("folder", "Folder", 1.2),
    ]

    def render_docx(self, output_path: str, generated_at: datetime) -> str:
        document = ds.new_document()
        ds.set_landscape(document)

        ds.add_title_block(
            document,
            title="Master Media Catalog",
            subtitle="A running index of all cataloged camera & audio recordings",
            eyebrow="MetaScraper",
        )

        summary = self.summary()
        self._add_docx_summary(document, summary, generated_at)

        ds.add_section_heading(document, "Catalog")
        headers = [label for _, label, _ in self.DOCX_COLUMNS]
        widths = [w for _, _, w in self.DOCX_COLUMNS]
        keys = [key for key, _, _ in self.DOCX_COLUMNS]
        rows = [
            [record.get(key) for key in keys]
            for record in self.sorted_records()
        ]
        if rows:
            ds.add_data_table(document, headers, rows, col_widths_in=widths)
        else:
            document.add_paragraph("No media files have been cataloged yet.")

        ds.add_footer(document, "MetaScraper · Master Media Catalog")
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        document.save(output_path)
        return output_path

    def _add_docx_summary(self, document, summary, generated_at) -> None:
        from docx.shared import Pt

        p = document.add_paragraph()
        run = p.add_run(f"Last updated {utils.format_datetime(generated_at)}")
        run.font.size = Pt(9)
        run.font.color.rgb = ds.MUTED
        p.paragraph_format.space_after = Pt(6)

        stat_rows = [
            ("Total Files", str(summary["count"])),
            ("Total Duration", utils.human_duration(summary["total_seconds"])),
            ("Total Size", utils.human_file_size(summary["total_bytes"])),
        ]
        for kind, count in sorted(summary["by_kind"].items()):
            stat_rows.append((f"{kind}", str(count)))
        ds.add_key_value_table(document, stat_rows, label_width_in=2.2)

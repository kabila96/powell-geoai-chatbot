"""Extract a Word document into heading-delimited sections, keeping paragraphs AND tables in
document order (python-docx's `doc.paragraphs` alone would silently skip tables)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph


@dataclass
class Section:
    heading: str
    lines: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(self.lines).strip()


def _iter_block_items(document):
    body = document.element.body
    for child in body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            yield Paragraph(child, document)
        elif tag == "tbl":
            yield Table(child, document)


def _table_to_lines(table: Table) -> list[str]:
    lines = []
    for row in table.rows:
        cells, previous = [], None
        for cell in row.cells:
            value = " / ".join(part.strip() for part in cell.text.splitlines() if part.strip())
            if value and value != previous:  # merged cells repeat their text
                cells.append(value)
            previous = value
        if not cells:
            continue
        # Two-column "label | value" tables read best as "label: value".
        lines.append(f"{cells[0]}: {' | '.join(cells[1:])}" if len(cells) >= 2 else cells[0])
    return lines


def load_docx_sections(path: Path) -> tuple[str, list[Section]]:
    """Return (document_title, sections)."""
    document = Document(str(path))
    title = document.core_properties.title or path.stem.replace("_", " ").title()
    sections: list[Section] = [Section(heading="Overview")]

    for block in _iter_block_items(document):
        if isinstance(block, Paragraph):
            text = block.text.strip()
            if not text:
                continue
            style = (block.style.name or "").lower() if block.style is not None else ""
            if style == "title":
                title = text
                continue
            if style.startswith("heading 1"):
                sections.append(Section(heading=text))
                continue
            if style.startswith("heading"):
                # Sub-headings stay inside their parent section but keep context for retrieval.
                sections[-1].lines.append(f"\n{text}:")
                continue
            prefix = "- " if "list" in style else ""
            sections[-1].lines.append(prefix + text)
        else:
            sections[-1].lines.extend(_table_to_lines(block))

    return title, [s for s in sections if s.text]

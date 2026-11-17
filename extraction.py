"""Text extraction from source documents (.txt, .docx, .pdf).

No tkinter dependency — pure file-in, text-out functions.
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import pdfplumber
from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph


def extract_txt(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _iter_block_items(doc: Document):
    """Yield paragraphs and tables in the order they appear in the document body."""
    for child in doc.element.body.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, doc)
        elif child.tag == qn("w:tbl"):
            yield Table(child, doc)


def extract_docx(path: Path) -> str:
    doc = Document(path)
    chunks = []
    for block in _iter_block_items(doc):
        if isinstance(block, Paragraph):
            text = block.text.strip()
            if not text:
                continue
            style = block.style.name if block.style else ""
            if style and style.startswith("List"):
                text = f"- {text}"
            chunks.append(text)
        else:
            for row in block.rows:
                cells = [cell.text.strip() for cell in row.cells]
                if any(cells):
                    chunks.append(" | ".join(cells))
    return "\n\n".join(chunks)


def extract_pdf(path: Path) -> str:
    chunks = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                chunks.append(text)
    return "\n\n".join(chunks)


def normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\xa0", " ")
    # Rejoin words split by a line-wrap hyphen, e.g. "exam-\nple" -> "example"
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)
    # Collapse runs of 3+ blank lines down to a single blank line
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.rstrip() + "\n"


EXTRACTORS = {
    ".txt": extract_txt,
    ".docx": extract_docx,
    ".pdf": extract_pdf,
}


def extract_text_for(path: Path) -> str:
    """Dispatch to the right extractor based on file suffix.

    Raises ValueError for unsupported suffixes so callers can show a
    single consistent error message.

    .txt files are read as-is, with no normalize_text() pass: that
    cleanup (de-hyphenating line-wrapped words, collapsing blank line
    runs, NFKC normalization) exists to undo artifacts .docx/.pdf
    extraction introduces, and would risk altering content in a file
    that's already plain text -- e.g. one produced by another tool with
    its own deliberate formatting.
    """
    suffix = path.suffix.lower()
    if suffix == ".txt":
        return extract_txt(path)
    extractor = EXTRACTORS.get(suffix)
    if extractor is None:
        raise ValueError(f"Unsupported file type: {suffix}")
    return normalize_text(extractor(path))

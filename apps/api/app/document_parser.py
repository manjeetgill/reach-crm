"""Bounded text extraction for operator-uploaded knowledge files."""
from __future__ import annotations

import io
import zipfile
from html.parser import HTMLParser
from pathlib import Path

from docx import Document
from pypdf import PdfReader

MAX_UPLOAD_BYTES = 5_000_000
MAX_TEXT_CHARS = 200_000


class _TextHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.skip += 1
        elif tag in {"p", "div", "br", "li", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style"} and self.skip:
            self.skip -= 1
        elif tag in {"p", "div", "li", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def extract_text(filename: str, data: bytes) -> str:
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError("File exceeds 5 MB")
    extension = Path(filename).suffix.lower()
    if extension in {".txt", ".md"}:
        text = data.decode("utf-8-sig")
    elif extension in {".html", ".htm"}:
        parser = _TextHTML()
        parser.feed(data.decode("utf-8-sig"))
        text = " ".join(parser.parts)
    elif extension == ".pdf":
        reader = PdfReader(io.BytesIO(data), strict=True)
        if len(reader.pages) > 100:
            raise ValueError("PDF exceeds 100 pages")
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    elif extension == ".docx":
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if sum(item.file_size for item in archive.infolist()) > 20_000_000:
                raise ValueError("DOCX expands beyond 20 MB")
        document = Document(io.BytesIO(data))
        text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    else:
        raise ValueError("Supported formats: PDF, DOCX, TXT, Markdown, HTML")
    text = text.replace("\x00", "").strip()
    if len(text) < 20:
        raise ValueError("Document contains too little extractable text")
    if len(text) > MAX_TEXT_CHARS:
        raise ValueError("Extracted text exceeds 200,000 characters")
    return text

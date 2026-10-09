"""Read a file into pages of lines.

The cheap path first: a born-digital PDF already carries its text, so it is read directly. Only pages without a
usable text layer (scans, photos) go to OCR, the expensive step. Results are cached by content and extractor
version, so a re-upload or a re-index never pays for OCR again."""
from __future__ import annotations

import io
import logging
import time
from typing import Protocol

import numpy as np
import pypdfium2 as pdfium
from PIL import Image
from pypdf import PdfReader

from .model import Extraction, Line, Page

MIN_TEXT_CHARS = 25          # a page with less extractable text than this is treated as an image


class OcrEngine(Protocol):
    name: str
    version: str

    def lines(self, image: Image.Image, page: int) -> list[Line]: ...


class RapidOcr:
    """RapidOCR (ONNX Runtime, CPU) with its bundled PP-OCR models: pip-installable, no system binaries."""
    name = "rapidocr"

    def __init__(self):
        from rapidocr import RapidOCR
        self._engine = RapidOCR(params={"Global.log_level": "warning"})
        for name in list(logging.root.manager.loggerDict):
            if "rapidocr" in name.lower():
                logging.getLogger(name).setLevel(logging.WARNING)
        from importlib.metadata import version
        self.version = version("rapidocr")

    def lines(self, image: Image.Image, page: int) -> list[Line]:
        result = self._engine(np.asarray(image.convert("RGB")))
        boxes = []
        for box, text, score in zip(result.boxes if result.boxes is not None else [], result.txts or [],
                                    result.scores or [], strict=False):
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            boxes.append((min(xs), min(ys), max(xs), max(ys), text, float(score)))
        return _assemble_lines(boxes, page)


def _assemble_lines(boxes: list[tuple], page: int) -> list[Line]:
    """OCR returns text boxes; a label and its value can come back as separate boxes on one row. Group boxes whose
    vertical spans overlap into a line, left to right."""
    rows: list[list[tuple]] = []
    for b in sorted(boxes, key=lambda b: (b[1] + b[3]) / 2):
        mid = (b[1] + b[3]) / 2
        for row in rows:
            top = min(r[1] for r in row)
            bottom = max(r[3] for r in row)
            if top <= mid <= bottom:
                row.append(b)
                break
        else:
            rows.append([b])
    lines = []
    for row in rows:
        row.sort(key=lambda b: b[0])
        lines.append(Line(text=" ".join(b[4] for b in row), page=page,
                          bbox=(min(b[0] for b in row), min(b[1] for b in row), max(b[2] for b in row),
                                max(b[3] for b in row)),
                          conf=min(b[5] for b in row)))
    return lines


def _text_layer(data: bytes) -> list[Page] | None:
    try:
        reader = PdfReader(io.BytesIO(data))
        pages = []
        for i, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            pages.append(Page(number=i, lines=[Line(text=t.strip(), page=i) for t in text.splitlines() if t.strip()]))
        return pages
    except Exception:  # noqa: BLE001 - a broken PDF is handled by the OCR path or rejected there
        return None


def _render(data: bytes, is_pdf: bool, dpi: int = 200) -> list[Image.Image]:
    if not is_pdf:
        return [Image.open(io.BytesIO(data))]
    doc = pdfium.PdfDocument(data)
    return [p.render(scale=dpi / 72).to_pil() for p in doc]


class Extractor:
    """Text layer where it exists, OCR where it does not, page by page."""

    def __init__(self, ocr: OcrEngine | None = None):
        self._ocr = ocr
        self.name = "layered"

    @property
    def ocr(self) -> OcrEngine:
        if self._ocr is None:
            self._ocr = RapidOcr()
        return self._ocr

    @property
    def version(self) -> str:
        return f"1+{self.ocr.name}-{self.ocr.version}"

    def extract(self, data: bytes, mime: str) -> Extraction:
        t0 = time.perf_counter()
        is_pdf = mime == "application/pdf"
        pages = _text_layer(data) if is_pdf else None
        ocr_pages = 0
        if pages is None or any(len(" ".join(line.text for line in p.lines)) < MIN_TEXT_CHARS for p in pages):
            images = _render(data, is_pdf)
            merged = []
            for i, img in enumerate(images, start=1):
                existing = pages[i - 1] if pages and i <= len(pages) else None
                if existing is not None and len(" ".join(x.text for x in existing.lines)) >= MIN_TEXT_CHARS:
                    merged.append(existing)
                    continue
                merged.append(Page(number=i, lines=self.ocr.lines(img, i), width=img.width, height=img.height))
                ocr_pages += 1
            pages = merged
        method = "text_layer" if ocr_pages == 0 else f"ocr:{self.ocr.name}"
        return Extraction(pages=pages, method=method, engine_version=self.version, ocr_pages=ocr_pages,
                          seconds=time.perf_counter() - t0)


def sniff_mime(data: bytes) -> str:
    if data[:5] == b"%PDF-":
        return "application/pdf"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    return "application/octet-stream"

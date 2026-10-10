"""Read a file into pages of lines.

The cheap path first: a born-digital PDF already carries its text, so it is read directly. Only pages without a
usable text layer (scans, photos) go to OCR, the expensive step. Results are cached by content and extractor
version, so a re-upload or a re-index never pays for OCR again."""
from __future__ import annotations

import io
import logging
import math
import os
import time
from typing import Protocol

import numpy as np
import pypdfium2 as pdfium
from PIL import Image
from pypdf import PdfReader

from .model import Extraction, Line, Page

MIN_TEXT_CHARS = 25          # a page with less extractable text than this is treated as an image
RETRY_BELOW = 0.8            # a line read with less confidence than this is read again without the orientation step


class OcrEngine(Protocol):
    name: str
    version: str

    def lines(self, image: Image.Image, page: int) -> list[Line]: ...


class RapidOcr:
    """RapidOCR (ONNX Runtime, CPU) with its bundled PP-OCR models: pip-installable, no system binaries.

    Variants are candidate configurations for a shadow trial (see shadow.py): `fast` skips the text-angle classifier
    and works on smaller images; `en-v4` swaps in an older English-only recogniser. The canonical engine is
    `default`; a variant only replaces it after passing the parity gate."""

    def __init__(self, variant: str = "default", threads: int | None = None):
        from rapidocr import LangRec, ModelType, OCRVersion, RapidOCR
        variants = {
            "default": {},
            "fast": {"Global.use_cls": False, "Global.max_side_len": 1280},
            "en-v4": {"Rec.lang_type": LangRec.EN, "Rec.model_type": ModelType.MOBILE,
                      "Rec.ocr_version": OCRVersion.PPOCRV4},
        }
        params = {"Global.log_level": "warning", **variants[variant]}
        if threads:
            params["EngineConfig.onnxruntime.intra_op_num_threads"] = threads
        self._params = params
        self._engine = RapidOCR(params=params)
        self._upright = None                 # the same engine without the per-line orientation step, when needed
        for name in list(logging.root.manager.loggerDict):
            if "rapidocr" in name.lower():
                logging.getLogger(name).setLevel(logging.WARNING)
        from importlib.metadata import version
        self.name = "rapidocr" if variant == "default" else f"rapidocr-{variant}"
        self.version = version("rapidocr")

    def lines(self, image: Image.Image, page: int) -> list[Line]:
        rgb = image.convert("RGB")
        result = self._engine(np.asarray(rgb))
        polys = [[(float(x), float(y)) for x, y in box] for box in (result.boxes if result.boxes is not None else [])]
        boxes = [(min(x for x, _ in poly), min(y for _, y in poly), max(x for x, _ in poly), max(y for _, y in poly),
                  text, float(score), poly)
                 for poly, text, score in zip(polys, result.txts or [], result.scores or [], strict=False)]
        if self._params.get("Global.use_cls", True):
            pixels = np.asarray(rgb)
            boxes = _reread_unsure(boxes, lambda b: self._read_upright(pixels, b[6]))
        return _assemble_lines(_straighten(polys, boxes, rgb.width, rgb.height), page)

    def _read_upright(self, pixels: np.ndarray, poly: list[tuple[float, float]]) -> tuple[str, float] | None:
        """Read one line again, from its own box turned straight, without the orientation step. The box is cut out
        along its own slant (as the recogniser itself does), so a skewed page's neighbouring lines stay out of it."""
        import cv2
        if self._upright is None:
            from rapidocr import RapidOCR
            self._upright = RapidOCR(params={**self._params, "Global.use_cls": False})
        pts = np.array(poly, dtype=np.float32)
        w = int(round(max(np.linalg.norm(pts[0] - pts[1]), np.linalg.norm(pts[3] - pts[2]))))
        h = int(round(max(np.linalg.norm(pts[0] - pts[3]), np.linalg.norm(pts[1] - pts[2]))))
        if w < 2 or h < 2:
            return None
        dst = np.array([[0, 0], [w, 0], [w, h], [0, h]], dtype=np.float32)
        line = cv2.warpPerspective(pixels, cv2.getPerspectiveTransform(pts, dst), (w, h), flags=cv2.INTER_CUBIC,
                                   borderMode=cv2.BORDER_REPLICATE)
        res = self._upright(cv2.copyMakeBorder(line, 12, 12, 12, 12, cv2.BORDER_REPLICATE))
        if res.boxes is None or not res.txts:
            return None
        parts = sorted(zip(res.boxes, res.txts, res.scores, strict=False), key=lambda t: min(p[0] for p in t[0]))
        return " ".join(t for _, t, _ in parts), min(float(s) for _, _, s in parts)


def _reread_unsure(boxes: list[tuple], read) -> list[tuple]:
    """The orientation step now and then turns an upright line upside down, and the recogniser then returns garbage
    with low confidence: "Aut s d s u st s fr s ss es." for a perfectly legible line of a scan. A line read with less
    confidence than RETRY_BELOW is read again without that step, and the more confident reading is kept."""
    out = []
    for b in boxes:
        if b[5] < RETRY_BELOW:
            again = read(b)
            if again is not None and again[1] > b[5]:
                b = (*b[:4], again[0], again[1], *b[6:])
        out.append(b)
    return out


def _straighten(polys: list[list[tuple[float, float]]], boxes: list[tuple], width: int, height: int) -> list[tuple]:
    """A scan rotated by two degrees drifts a long line by more than a line's height across the page, so rows grouped
    by vertical overlap would merge neighbouring lines. The page's skew is the median slope of the wider boxes' top
    edges; rows are grouped on each box's bounds with the page turned straight (the text is already read)."""
    slopes = [math.atan2(p[1][1] - p[0][1], p[1][0] - p[0][0]) for p in polys if p[1][0] - p[0][0] > 80]
    if not slopes:
        return boxes
    theta = sorted(slopes)[len(slopes) // 2]
    c, s = math.cos(-theta), math.sin(-theta)
    cx, cy = width / 2, height / 2
    out = []
    for poly, b in zip(polys, boxes, strict=True):
        pts = [((x - cx) * c - (y - cy) * s + cx, (x - cx) * s + (y - cy) * c + cy) for x, y in poly]
        out.append((min(x for x, _ in pts), min(y for _, y in pts), max(x for x, _ in pts), max(y for _, y in pts),
                    b[4], b[5]))
    return out


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
            # the canonical engine, or a candidate that passed its shadow trial: a configuration switch
            self._ocr = RapidOcr(os.environ.get("TESTAMUR_OCR_VARIANT", "default"))
        return self._ocr

    @property
    def version(self) -> str:
        return f"2+{self.ocr.name}-{self.ocr.version}"

    def extract(self, data: bytes, mime: str, before_ocr=None) -> Extraction:
        """`before_ocr(pages)` is called with the number of pages about to go through OCR, before any does: the
        hook a spend gate uses to refuse the cost up front."""
        t0 = time.perf_counter()
        is_pdf = mime == "application/pdf"
        pages = _text_layer(data) if is_pdf else None
        ocr_pages = 0
        if pages is None or any(len(" ".join(line.text for line in p.lines)) < MIN_TEXT_CHARS for p in pages):
            images = _render(data, is_pdf)
            if before_ocr is not None:
                needed = sum(1 for i in range(1, len(images) + 1)
                             if not (pages and i <= len(pages)
                                     and len(" ".join(x.text for x in pages[i - 1].lines)) >= MIN_TEXT_CHARS))
                before_ocr(needed)
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

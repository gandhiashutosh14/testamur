"""Render document content to the three forms evidence really arrives in: a born-digital PDF with a text layer, a
scanned PDF (image only: rotated, blurred, noisy, compressed) and a phone photo (perspective, uneven light)."""
from __future__ import annotations

import io
import random
from dataclasses import dataclass, field

import numpy as np
import pypdfium2 as pdfium
from PIL import Image, ImageFilter
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader, simpleSplit
from reportlab.pdfgen import canvas


@dataclass
class DocContent:
    """Pages of items: ("title", text) | ("kv", label, value) | ("para", text) | ("small", text) | ("gap",)."""
    pages: list[list[tuple]] = field(default_factory=list)


def render_pdf(content: DocContent, rng: random.Random) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, invariant=1)     # invariant: identical bytes for identical content
    width, height = A4
    body_font = rng.choice(["Helvetica", "Times-Roman"])
    bold_font = "Helvetica-Bold" if body_font == "Helvetica" else "Times-Bold"
    margin = 56 + rng.randint(0, 14)
    for items in content.pages:
        y = height - 64 - rng.randint(0, 20)
        for item in items:
            kind = item[0]
            if kind == "title":
                c.setFont(bold_font, 15)
                c.drawString(margin, y, item[1])
                y -= 30
            elif kind == "kv":
                text = f"{item[1]}: {item[2]}"
                size = 10.5                         # a long value is set smaller, never wrapped: it stays one line
                if c.stringWidth(text, body_font, size) > width - 2 * margin:
                    size = max(7.0, size * (width - 2 * margin) / c.stringWidth(text, body_font, size))
                c.setFont(body_font, size)
                c.drawString(margin, y, text)
                y -= 19
            elif kind == "para":
                c.setFont(body_font, 10)
                for line in simpleSplit(item[1], body_font, 10, width - 2 * margin):
                    c.drawString(margin, y, line)
                    y -= 14
                y -= 6
            elif kind == "small":
                c.setFont(body_font, 7)
                for line in simpleSplit(item[1], body_font, 7, width - 2 * margin):
                    c.drawString(margin, y, line)
                    y -= 10
            elif kind == "gap":
                y -= 14
            if y < 70:
                c.showPage()
                y = height - 64
        c.showPage()
    c.save()
    return buf.getvalue()


def _render_pages(pdf_bytes: bytes, dpi: int, first_only: bool = False) -> list[Image.Image]:
    doc = pdfium.PdfDocument(pdf_bytes)
    pages = [doc[0]] if first_only else list(doc)
    return [p.render(scale=dpi / 72).to_pil() for p in pages]


def _noise(img: Image.Image, rng: random.Random, sigma: float) -> Image.Image:
    arr = np.asarray(img).astype(np.float32)
    arr += np.random.default_rng(rng.randint(0, 2**31)).normal(0, sigma, arr.shape)
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


def _jpeg(img: Image.Image, quality: int) -> Image.Image:
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=quality)
    return Image.open(io.BytesIO(out.getvalue()))


def to_scan_pdf(pdf_bytes: bytes, rng: random.Random) -> bytes:
    """An image-only PDF, as a flatbed scanner or a multifunction printer produces. The JPEG of each page is
    embedded as it is, in a PDF written in invariant mode, so the same seed always yields the same bytes."""
    pages = []
    for page in _render_pages(pdf_bytes, dpi=rng.choice([150, 170, 200])):
        img = page.convert("L").rotate(rng.uniform(-2.2, 2.2), expand=True, fillcolor=255, resample=Image.BICUBIC)
        img = img.filter(ImageFilter.GaussianBlur(rng.uniform(0.3, 0.9)))
        noisy = _noise(img, rng, rng.uniform(4, 12))
        jpeg = io.BytesIO()
        noisy.save(jpeg, format="JPEG", quality=rng.randint(45, 80))
        pages.append((jpeg.getvalue(), noisy.size))
    out = io.BytesIO()
    c = canvas.Canvas(out, invariant=1)
    for data, (w, h) in pages:
        size = (w * 72 / 150, h * 72 / 150)
        c.setPageSize(size)
        c.drawImage(ImageReader(io.BytesIO(data)), 0, 0, width=size[0], height=size[1])
        c.showPage()
    c.save()
    return out.getvalue()


def _perspective_coeffs(src: list[tuple[float, float]], dst: list[tuple[float, float]]) -> list[float]:
    rows = []
    for (x, y), (u, v) in zip(dst, src, strict=True):
        rows.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        rows.append([0, 0, 0, x, y, 1, -v * x, -v * y])
    a = np.array(rows, dtype=np.float64)
    b = np.array([c for pt in src for c in pt], dtype=np.float64)
    return np.linalg.solve(a, b).tolist()


def to_photo_jpg(pdf_bytes: bytes, rng: random.Random) -> bytes:
    """A phone photo of the first page: skewed perspective, an uneven light gradient, a little blur."""
    page = _render_pages(pdf_bytes, dpi=160, first_only=True)[0].convert("RGB")
    w, h = page.size
    j = lambda: rng.uniform(0.0, 0.035)  # noqa: E731
    dst = [(w * j(), h * j()), (w * (1 - j()), h * j()), (w * (1 - j()), h * (1 - j())), (w * j(), h * (1 - j()))]
    coeffs = _perspective_coeffs([(0, 0), (w, 0), (w, h), (0, h)], dst)
    img = page.transform((w, h), Image.PERSPECTIVE, coeffs, Image.BICUBIC, fillcolor=(205, 200, 190))
    grad = np.linspace(rng.uniform(0.78, 0.9), 1.0, w, dtype=np.float32)[None, :, None]
    arr = np.asarray(img).astype(np.float32) * grad
    img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(rng.uniform(0.4, 1.0)))
    img = _noise(img, rng, rng.uniform(3, 8))
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=rng.randint(60, 85))
    return out.getvalue()

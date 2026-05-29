"""Client-side document fragmentation.

The load-bearing security claim: the SDK reads the document locally,
generates five masked views in the customer's environment, and uploads
only the fragments. The full unfragmented document never reaches the
managed endpoint.

Quality contract: the SDK preserves the customer's original quality.
For raster inputs (PNG, JPEG, TIFF, BMP, WEBP, GIF), output fragments
keep the source resolution and are saved as lossless PNG so the mask
overlay does not introduce JPEG generation loss. For PDFs, the SDK
rasterizes at AWAITVERIFY_PDF_RASTER_DPI (300, "print quality") so
small handwritten content survives — the difference between T12C3
and 712C3 must be unambiguous to the reviewer.

Supported input formats (v1):

  Direct:
    - PDF (any version)
    - PNG, JPEG, TIFF, BMP, WEBP, GIF (anything Pillow opens)

  Via LibreOffice headless conversion (system binary required):
    - DOCX, XLSX, PPTX (modern Office Open XML)
    - DOC, XLS, PPT (legacy OLE formats)
    - ODT, ODS, ODP (LibreOffice native)
    - RTF
"""

from __future__ import annotations

import io
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from awaithumans.awaitverify.errors import (
    VerifyDepsMissingError,
    VerifyDocumentLoadError,
    VerifyDocumentTooLargeError,
)
from awaithumans.utils.constants import (
    AWAITVERIFY_FRAGMENT_COUNT,
    AWAITVERIFY_LIBREOFFICE_BIN_ENV,
    AWAITVERIFY_LIBREOFFICE_DEFAULT_BIN,
    AWAITVERIFY_LIBREOFFICE_TIMEOUT_SECONDS,
    AWAITVERIFY_MAX_PAGES,
    AWAITVERIFY_PDF_RASTER_DPI,
    AWAITVERIFY_SUPPORTED_IMAGE_FORMATS,
)

# All fragments are emitted as PNG regardless of input format. PNG is
# lossless so the mask overlay does not stack JPEG generation loss on
# a JPEG input. Output PNG keeps the source pixel dimensions, so
# "same quality the customer provided" holds.
_FRAGMENT_OUTPUT_FORMAT = "PNG"

# Office magic bytes:
#   ZIP container (Office Open XML): 50 4B 03 04 (= "PK\x03\x04")
#   OLE Compound File Binary (legacy): D0 CF 11 E0 A1 B1 1A E1
_ZIP_MAGIC = b"PK\x03\x04"
_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_RTF_MAGIC = b"{\\rtf"


def _require_pillow() -> Any:
    """Import Pillow lazily so the core SDK does not depend on it."""
    try:
        from PIL import Image, ImageDraw  # noqa: PLC0415

        return Image, ImageDraw
    except ImportError as exc:
        raise VerifyDepsMissingError("Pillow") from exc


def _require_pdf2image() -> Any:
    """Import pdf2image lazily. Required only for PDF inputs."""
    try:
        from pdf2image import convert_from_bytes  # noqa: PLC0415

        return convert_from_bytes
    except ImportError as exc:
        raise VerifyDepsMissingError("pdf2image") from exc


def load_pages(source: bytes | str | Path) -> list[Any]:
    """Load a document and return a list of PIL Image pages.

    Accepts raw bytes or a filesystem path. Returns one PIL Image per
    page at the original resolution (for image inputs) or at
    AWAITVERIFY_PDF_RASTER_DPI (for PDFs and converted office docs).
    Caps page count at AWAITVERIFY_MAX_PAGES.

    Supported formats: PDF, PNG, JPEG, TIFF, BMP, WEBP, GIF directly;
    DOCX / XLSX / PPTX / DOC / XLS / PPT / ODT / ODS / ODP / RTF via
    LibreOffice headless conversion to PDF.
    """
    Image, _ = _require_pillow()  # noqa: N806 — CapWords classes from PIL

    raw = source if isinstance(source, bytes) else Path(source).read_bytes()

    if _looks_like_pdf(raw):
        pages = _rasterize_pdf(raw)
    elif _looks_like_office(raw):
        pdf_bytes = _office_to_pdf(raw)
        pages = _rasterize_pdf(pdf_bytes)
    else:
        try:
            opened = Image.open(io.BytesIO(raw))
            pages = []
            try:
                while True:
                    pages.append(opened.copy().convert("RGB"))
                    opened.seek(opened.tell() + 1)
            except EOFError:
                pass
        except Exception as exc:
            raise VerifyDocumentLoadError(f"Pillow could not decode the document: {exc}") from exc

        if opened.format and opened.format.upper() not in AWAITVERIFY_SUPPORTED_IMAGE_FORMATS:
            raise VerifyDocumentLoadError(
                f"Pillow detected format '{opened.format}' which is not in "
                f"AwaitVerify's supported list: "
                f"{sorted(AWAITVERIFY_SUPPORTED_IMAGE_FORMATS)} (or PDF / office)."
            )

    if len(pages) > AWAITVERIFY_MAX_PAGES:
        raise VerifyDocumentTooLargeError(len(pages))

    return list(pages)


def fragment_document(source: bytes | str | Path) -> list[list[bytes]]:
    """Fragment a document into masked views.

    Returns a list-of-lists: outer index is page (0..N-1), inner list
    holds AWAITVERIFY_FRAGMENT_COUNT (5) PNG-encoded fragments.

    Fragmentation regions per page:
        fragment 0: right half hidden
        fragment 1: left + center hidden (only right quarter visible)
        fragment 2: center 50% hidden
        fragment 3: top half hidden
        fragment 4: bottom half hidden
    """
    pages = load_pages(source)
    return [_fragment_page(page) for page in pages]


def _fragment_page(page: Any) -> list[bytes]:
    """Generate the five mask variants for a single page."""
    Image, ImageDraw = _require_pillow()  # noqa: N806 — CapWords classes from PIL

    fragments: list[bytes] = []
    for mask_region in _mask_regions(page.width, page.height):
        copy = page.copy()
        draw = ImageDraw.Draw(copy)
        draw.rectangle(mask_region, fill="black")
        buf = io.BytesIO()
        copy.save(buf, format=_FRAGMENT_OUTPUT_FORMAT, compress_level=1)
        fragments.append(buf.getvalue())

    if len(fragments) != AWAITVERIFY_FRAGMENT_COUNT:
        raise RuntimeError(
            f"fragmentation produced {len(fragments)} masks; expected {AWAITVERIFY_FRAGMENT_COUNT}"
        )
    return fragments


def _mask_regions(width: int, height: int) -> list[tuple[int, int, int, int]]:
    """Return the five mask rectangles in (left, top, right, bottom) form."""
    half_w = width // 2
    quarter_w = width // 4
    three_quarter_w = (3 * width) // 4
    half_h = height // 2

    return [
        (half_w, 0, width, height),
        (0, 0, three_quarter_w, height),
        (quarter_w, 0, three_quarter_w, height),
        (0, 0, width, half_h),
        (0, half_h, width, height),
    ]


def _rasterize_pdf(raw: bytes) -> list[Any]:
    """Convert a PDF byte stream into a list of PIL Images via pdf2image."""
    convert_from_bytes = _require_pdf2image()
    try:
        return list(convert_from_bytes(raw, dpi=AWAITVERIFY_PDF_RASTER_DPI))
    except Exception as exc:
        raise VerifyDocumentLoadError(f"pdf2image failed: {exc}") from exc


def _looks_like_pdf(raw: bytes) -> bool:
    """Magic-byte sniff for PDF."""
    return raw[:5] == b"%PDF-"


def _looks_like_office(raw: bytes) -> bool:
    """Magic-byte sniff for office formats.

    Both modern (.docx/.xlsx/.pptx — ZIP) and legacy (.doc/.xls/.ppt
    — OLE compound file) formats share their magic with non-office
    files. The verification depends on LibreOffice succeeding on the
    actual conversion step; this prefilter just decides whether to
    *attempt* office conversion versus falling through to Pillow.
    """
    if raw[:4] == _ZIP_MAGIC:
        return True
    if raw[:8] == _OLE_MAGIC:
        return True
    return raw[:5] == _RTF_MAGIC


def _office_to_pdf(raw: bytes) -> bytes:
    """Convert an office document to PDF via LibreOffice headless.

    Requires the LibreOffice binary on PATH (or pointed to by the
    AWAITHUMANS_LIBREOFFICE_BIN env var). Conversion happens in a
    temporary directory that is wiped on function exit.
    """
    bin_path = os.environ.get(AWAITVERIFY_LIBREOFFICE_BIN_ENV, AWAITVERIFY_LIBREOFFICE_DEFAULT_BIN)

    with tempfile.TemporaryDirectory(prefix="awaitverify-office-") as tmpdir:
        src = Path(tmpdir) / "input.bin"
        src.write_bytes(raw)
        cmd = [
            bin_path,
            "--headless",
            "--convert-to",
            "pdf",
            "--outdir",
            tmpdir,
            str(src),
        ]
        try:
            subprocess.run(  # noqa: S603 — explicit args, fixed binary
                cmd,
                check=True,
                capture_output=True,
                timeout=AWAITVERIFY_LIBREOFFICE_TIMEOUT_SECONDS,
            )
        except FileNotFoundError as exc:
            raise VerifyDocumentLoadError(
                f"LibreOffice binary '{bin_path}' not found on PATH. "
                "Office document support requires LibreOffice installed on the "
                "machine running the SDK. On macOS: `brew install --cask "
                "libreoffice`. On Debian/Ubuntu: `apt-get install libreoffice`. "
                "Override the binary location with the "
                f"{AWAITVERIFY_LIBREOFFICE_BIN_ENV} environment variable."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise VerifyDocumentLoadError(
                f"LibreOffice timed out after {AWAITVERIFY_LIBREOFFICE_TIMEOUT_SECONDS}s"
            ) from exc
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or b"").decode(errors="replace")[:500]
            raise VerifyDocumentLoadError(
                f"LibreOffice conversion failed (exit {exc.returncode}): {stderr}"
            ) from exc

        produced = Path(tmpdir) / "input.pdf"
        if not produced.exists():
            raise VerifyDocumentLoadError(
                "LibreOffice did not produce a PDF (check that the input "
                "file format is supported and not password-protected)"
            )
        return produced.read_bytes()


__all__ = [
    "fragment_document",
    "load_pages",
]

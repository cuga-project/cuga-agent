"""System tool: pdf_to_images.

Convert a PDF file into per-page JPEG images.  Tries PyMuPDF (fitz) first
(pure-Python, no system binary required); falls back to pdftoppm (Poppler) if
PyMuPDF is not installed.

Install the preferred backend with:  uv pip install pymupdf

Accepts absolute paths, paths relative to the current working directory, or
``/workspace/``-prefixed paths (resolved relative to CWD, the same convention
used by ``analyze_image``).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field


def _resolve(path: str) -> Path:
    p = Path(path)
    if p.is_absolute():
        return p
    if path.startswith("/workspace/"):
        return Path(path[len("/workspace/"):])
    return p


def pdf_to_images(
    pdf: str,
    prefix: str,
    dpi: int = 150,
    first_page: int | None = None,
    last_page: int | None = None,
) -> str:
    """Convert a PDF to per-page JPEG images.

    Args:
        pdf: Path to the source PDF (absolute, relative, or /workspace/...).
        prefix: Output filename prefix. Images are written as ``<prefix>-01.jpg``,
            ``<prefix>-02.jpg``, etc.  May include a directory (e.g. ``slides/slide``).
        dpi: Render resolution in dots per inch (default 150).
        first_page: First page to convert, 1-based (default: first page).
        last_page: Last page to convert, 1-based inclusive (default: last page).

    Returns:
        Newline-separated list of generated image file paths, or an error message.
    """
    pdf_path = _resolve(pdf)
    if not pdf_path.exists():
        return f"ERROR: PDF not found: {pdf!r}"

    # Ensure the output directory exists.
    prefix_path = Path(prefix)
    if prefix_path.parent != Path("."):
        prefix_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        return _convert_pymupdf(pdf_path, str(prefix_path), dpi, first_page, last_page)
    except ImportError:
        pass

    if shutil.which("pdftoppm"):
        return _convert_pdftoppm(pdf_path, str(prefix_path), dpi, first_page, last_page)

    return (
        "ERROR: No PDF-to-image backend available.\n"
        "Install one of:\n"
        "  uv pip install pymupdf   # pure-Python (recommended)\n"
        "  brew install poppler     # provides pdftoppm"
    )


def _convert_pymupdf(pdf: Path, prefix: str, dpi: int, first: int | None, last: int | None) -> str:
    import fitz  # noqa: PLC0415  (PyMuPDF)

    doc = fitz.open(str(pdf))
    total = len(doc)
    start = (first - 1) if first is not None else 0
    stop = last if last is not None else total
    mat = fitz.Matrix(dpi / 72, dpi / 72)

    generated: list[str] = []
    for i in range(start, min(stop, total)):
        page = doc[i]
        pix = page.get_pixmap(matrix=mat)
        out = Path(f"{prefix}-{i + 1:02d}.jpg")
        pix.save(str(out))
        generated.append(str(out))

    return "\n".join(generated) if generated else "WARNING: no pages converted"


def _convert_pdftoppm(pdf: Path, prefix: str, dpi: int, first: int | None, last: int | None) -> str:
    cmd = ["pdftoppm", "-jpeg", "-r", str(dpi)]
    if first is not None:
        cmd += ["-f", str(first)]
    if last is not None:
        cmd += ["-l", str(last)]
    cmd += [str(pdf), prefix]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return f"ERROR: pdftoppm failed:\n{result.stderr}"

    # pdftoppm names files <prefix>-N.jpg (1-indexed, variable digits).
    parent = Path(prefix).parent
    stem = Path(prefix).name
    found = sorted(parent.glob(f"{stem}-*.jpg"))
    return "\n".join(str(f) for f in found) if found else "WARNING: pdftoppm produced no output files"


class _PdfToImagesInput(BaseModel):
    pdf: str = Field(..., description="Path to the PDF file (absolute, relative, or /workspace/filename).")
    prefix: str = Field(
        ...,
        description=(
            "Output filename prefix. Images are written as <prefix>-01.jpg, <prefix>-02.jpg, etc. "
            "May include a subdirectory, e.g. 'slides/slide'."
        ),
    )
    dpi: int = Field(150, description="Render resolution in DPI (default 150).")
    first_page: int | None = Field(None, description="First page to convert (1-based). Defaults to first page.")
    last_page: int | None = Field(None, description="Last page to convert (1-based, inclusive). Defaults to last page.")


def create_pdf_to_images_tool() -> StructuredTool:
    """Return a StructuredTool wrapping :func:`pdf_to_images`."""
    return StructuredTool.from_function(
        func=pdf_to_images,
        name="pdf_to_images",
        description=(
            "Convert a PDF file into per-page JPEG images for visual inspection. "
            "Use this after converting a .pptx or .docx to PDF (via soffice) to produce "
            "slide images that can be passed to analyze_image for visual QA. "
            "Returns a newline-separated list of generated image paths."
        ),
        args_schema=_PdfToImagesInput,
    )

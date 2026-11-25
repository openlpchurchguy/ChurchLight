# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ChurchLight Presenter contributors
"""Turning PDF pages into PNG images, with no extra program to install.

The preferred way is the `pypdfium2` package (PDFium, the engine inside Chrome):

    python -m pip install pypdfium2

It is a plain pip install on Windows, Linux and macOS, and it is permissively
licensed (Apache-2.0 or BSD-3-Clause). If it is not installed, the importer falls back
to poppler's `pdftoppm` when that is on the PATH.

PNG files are written here with the standard library, so no imaging library is needed.
"""
import os
import shutil
import struct
import sys
import zlib
from pathlib import Path

from .common import ImporterError, encode_png  # noqa: F401

MAX_LONG_SIDE = 4096            # pixels; a poster-sized page is scaled down rather than eating memory

# A copy of pypdfium2 placed in a "vendor" folder next to the churchlight_presenter folder is found without pip,
# so the program can be handed to a church with its PDF renderer already inside (see tools/add_vendor_wheel.py).
VENDOR_DIR = Path(__file__).resolve().parents[2] / "vendor"


def use_vendor_folder(folder=None):
    """Make a vendor folder importable (after everything already installed). True when it was added now."""
    folder = Path(folder) if folder is not None else VENDOR_DIR
    path = str(folder)
    if path in sys.path or not folder.is_dir():
        return False
    sys.path.append(path)
    return True


# ---------------------------------------------------------------------------
# PNG writing (standard library only)
# ---------------------------------------------------------------------------
def bitmap_rows(data, width, height, stride, channels, rgb_order):
    """RGB rows from a raw bitmap buffer.

    `stride` is the bytes per row in the buffer (it may include padding), `channels` is 1, 3 or 4
    bytes per pixel, and `rgb_order` says whether color bytes are R,G,B (True) or B,G,R (False).
    A fourth (alpha or unused) byte is dropped.
    """
    if channels not in (1, 3, 4):
        raise ImporterError(f"The PDF renderer produced an unexpected pixel format ({channels} channels)")
    if len(data) < (height - 1) * stride + width * channels:
        raise ImporterError("The PDF renderer returned less picture data than expected")
    rows = []
    for y in range(height):
        row = data[y * stride:y * stride + width * channels]
        if channels == 1:
            out = bytearray(width * 3)
            out[0::3] = out[1::3] = out[2::3] = row
        elif channels == 3:
            if rgb_order:
                out = row
            else:
                out = bytearray(row)
                out[0::3], out[2::3] = row[2::3], row[0::3]
        else:
            out = bytearray(width * 3)
            first, last = (0, 2) if rgb_order else (2, 0)
            out[0::3], out[1::3], out[2::3] = row[first::4], row[1::4], row[last::4]
        rows.append(bytes(out))
    return rows


# ---------------------------------------------------------------------------
# Rendering with pypdfium2
# ---------------------------------------------------------------------------
def pdfium_available():
    use_vendor_folder()
    try:
        import pypdfium2  # noqa: F401
    except ImportError:
        return False
    return True


def render_pdf_with_pdfium(pdf, out_dir, dpi=150):
    """Render each PDF page to `page-001.png`, `page-002.png`... with pypdfium2; returns the files."""
    use_vendor_folder()
    try:
        import pypdfium2 as pdfium
    except ImportError as err:
        raise ImporterError("Importing PDFs needs pypdfium2. Install it with:  "
                            "python -m pip install pypdfium2") from err
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        document = pdfium.PdfDocument(str(pdf))
    except Exception as err:  # noqa: BLE001 - the library's own error type varies by version
        raise ImporterError(f"The PDF could not be read: {err}") from err
    files = []
    try:
        count = len(document)
        if count < 1:
            raise ImporterError("The PDF has no pages")
        for number in range(count):
            page = document[number]
            try:
                files.append(_render_page(page, out_dir / f"page-{number + 1:03d}.png", dpi))
            finally:
                _close(page)
    except ImporterError:
        raise
    except Exception as err:  # noqa: BLE001
        raise ImporterError(f"The PDF could not be drawn: {err}") from err
    finally:
        _close(document)
    return files


def _close(obj):
    close = getattr(obj, "close", None)
    if close is not None:
        try:
            close()
        except Exception:  # noqa: BLE001 - closing is tidy-up only
            pass


def _render_page(page, target, dpi):
    width_pt, height_pt = page.get_size()
    scale = dpi / 72.0
    longest = max(width_pt, height_pt) * scale
    if longest > MAX_LONG_SIDE:
        scale *= MAX_LONG_SIDE / longest
    try:
        bitmap = page.render(scale=scale, rev_byteorder=True)      # colors as R,G,B
        requested_rgb = True
    except TypeError:                                              # an older version: B,G,R
        bitmap = page.render(scale=scale)
        requested_rgb = False
    rgb_order = getattr(bitmap, "rev_byteorder", requested_rgb)
    channels = getattr(bitmap, "n_channels", 3)
    rows = bitmap_rows(bytes(bitmap.buffer), bitmap.width, bitmap.height,
                       getattr(bitmap, "stride", bitmap.width * channels), channels, rgb_order)
    target.write_bytes(encode_png(bitmap.width, bitmap.height, rows))
    _close(bitmap)
    return target


# ---------------------------------------------------------------------------
# Choosing a renderer
# ---------------------------------------------------------------------------
def default_renderer(poppler_renderer):
    """pypdfium2 if installed, else poppler (passed in) if its program is on the PATH, else an error."""
    if pdfium_available():
        return render_pdf_with_pdfium
    if shutil.which("pdftoppm"):
        return poppler_renderer
    raise ImporterError(
        "Importing a PDF needs a PDF renderer. The easiest is:  python -m pip install pypdfium2   "
        "(alternatively install poppler and put its pdftoppm on the PATH).")


def renderer_name():
    """Which renderer would be used, for reports: 'pypdfium2', 'poppler' or None."""
    if pdfium_available():
        return "pypdfium2"
    return "poppler" if shutil.which("pdftoppm") else None


def pdf_status():
    """What the Settings page shows about PDF import: {"ready", "engine", "version", "bundled", "message"}."""
    if pdfium_available():
        import pypdfium2
        try:
            from importlib import metadata
            version = metadata.version("pypdfium2")
        except Exception:  # noqa: BLE001 - a version is a nicety
            version = ""
        try:
            bundled = VENDOR_DIR.resolve() in Path(pypdfium2.__file__).resolve().parents
        except (AttributeError, TypeError, OSError):
            bundled = False
        label = "pypdfium2" + (f" {version}" if version else "")
        where = ", included with the program" if bundled else ""
        return {"ready": True, "engine": "pypdfium2", "version": version, "bundled": bundled,
                "message": f"PDF import is ready ({label}{where})."}
    if shutil.which("pdftoppm"):
        return {"ready": True, "engine": "poppler", "version": "", "bundled": False,
                "message": "PDF import is ready (poppler's pdftoppm)."}
    return {"ready": False, "engine": None, "version": "", "bundled": False,
            "message": ("PDF import is not set up on this computer, so PDF files cannot be added as slideshows yet. "
                        "The full ChurchLight download includes the PDF renderer; if you installed in another way, "
                        "ask whoever set the program up to add it (a developer can run: python -m pip install "
                        "pypdfium2).")}

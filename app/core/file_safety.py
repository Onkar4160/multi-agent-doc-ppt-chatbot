"""Upload safety: type by content, size limits, zip-bomb limits, macro rejection (NFR-6, ADR-023).

Files are judged by what they contain, not by their name: an OOXML file must be a ZIP with the parts of its
format, a PDF must start with ``%PDF-``, images must carry their signature, text must decode.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from app.core.errors import UnsupportedFileError

OOXML = {
    ".docx": "word/document.xml",
    ".dotx": "word/document.xml",
    ".pptx": "ppt/presentation.xml",
    ".potx": "ppt/presentation.xml",
    ".xlsx": "xl/workbook.xml",
    ".xltx": "xl/workbook.xml",
}
MACRO_EXTENSIONS = {".docm", ".dotm", ".pptm", ".potm", ".xlsm", ".xltm", ".xlsb"}
TEXT_EXTENSIONS = {".csv", ".txt", ".md"}
IMAGE_SIGNATURES = {
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
}
ALLOWED_EXTENSIONS = set(OOXML) | TEXT_EXTENSIONS | set(IMAGE_SIGNATURES) | {".pdf"}

MAX_ZIP_ENTRIES = 5000
MAX_UNCOMPRESSED_BYTES = 300 * 1024 * 1024
MAX_COMPRESSION_RATIO = 150


def check_upload(filename: str, content: bytes, max_mb: int) -> str:
    """Return the normalised extension when the upload is acceptable; raise ``UnsupportedFileError`` otherwise."""
    ext = Path(filename or "").suffix.lower()
    if not filename:
        raise UnsupportedFileError("The file has no name.")
    if ext in MACRO_EXTENSIONS:
        raise UnsupportedFileError(f"Macro-enabled files ({ext}) are not accepted. Save it without macros and upload again.")
    if ext not in ALLOWED_EXTENSIONS:
        raise UnsupportedFileError(
            f"Unsupported file type '{ext or 'none'}'. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}."
        )
    if not content:
        raise UnsupportedFileError("The file is empty.")
    if len(content) > max_mb * 1024 * 1024:
        raise UnsupportedFileError(f"The file is larger than the {max_mb} MB limit.")

    if ext in OOXML:
        _check_ooxml(content, OOXML[ext])
    elif ext == ".pdf":
        if not content.lstrip()[:5] == b"%PDF-":
            raise UnsupportedFileError("Corrupt file: this is not a PDF document.")
    elif ext in IMAGE_SIGNATURES:
        if not content.startswith(IMAGE_SIGNATURES[ext]):
            raise UnsupportedFileError(f"Corrupt file: this is not a {ext.lstrip('.').upper()} image.")
    elif ext in TEXT_EXTENSIONS:
        if b"\x00" in content[:8192]:
            raise UnsupportedFileError("Corrupt file: text files must not contain binary data.")
        try:
            content.decode("utf-8")
        except UnicodeDecodeError:
            try:
                content.decode("cp1252")
            except UnicodeDecodeError as exc:
                raise UnsupportedFileError("The text file uses an unknown encoding; save it as UTF-8.") from exc
    return ext


def _check_ooxml(content: bytes, main_part: str) -> None:
    if not content.startswith(b"PK\x03\x04"):
        raise UnsupportedFileError("Corrupt file: Office documents are ZIP packages and this one is not.")
    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
        infos = zf.infolist()
    except zipfile.BadZipFile as exc:
        raise UnsupportedFileError("Corrupt file: the Office package cannot be opened.") from exc
    if len(infos) > MAX_ZIP_ENTRIES:
        raise UnsupportedFileError("The package contains too many parts to be processed safely.")
    total = sum(i.file_size for i in infos)
    if total > MAX_UNCOMPRESSED_BYTES or total > MAX_COMPRESSION_RATIO * max(len(content), 1):
        raise UnsupportedFileError("The package expands to an unsafe size and was refused.")
    names = {i.filename for i in infos}
    if "[Content_Types].xml" not in names or main_part not in names:
        raise UnsupportedFileError("Corrupt file: required parts of the Office document are missing.")
    if any(n.lower().endswith("vbaproject.bin") for n in names):
        raise UnsupportedFileError("The file contains macros, which are not accepted. Remove them and upload again.")

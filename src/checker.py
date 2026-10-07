"""Decides an image's Status and Exclude from what STAC's preview pane showed.

Pure logic, no browser, so every rule here is covered by tests/test_checker.py.

The rule (Nick, 2026-10-06), after the Details-tab File Size rule marked a
good image as corrupted:
    preview shows the corrupted-file message  -> leave Exclude blank
    preview rendered the PDF                  -> "*"
"Rendered" means PDF.js actually parsed the document, a positive signal, not
merely the absence of the message: a preview still loading has no message
either. stac.py only reports a preview once its URL carries this Image Id.
"""

import re
from datetime import datetime

from exceptions import InputProblem
from models import CheckResult, Status

# What goes in Exclude for a good image. Everything else is left blank.
EXCLUDE_GOOD = "*"

# The leading number of a file size, e.g. 16.72 from "16.72" or "16.72 MB".
FILE_SIZE_NUMBER = re.compile(r"^\s*(\d[\d,]*(?:\.\d+)?)")

ERROR_BOTH_SIGNALS = "preview both loaded and showed the corrupted message"
ERROR_NO_SIGNAL = "preview neither loaded nor showed the corrupted message"


def normalize_image_id(raw: object) -> str:
    """Turn an Image Id cell into plain digits, the way STAC shows it.

    Excel hands numbers back as int or float, so 15072070.0 has to become
    "15072070" or it would never match.

    Args:
        raw: The cell value as openpyxl returns it.

    Returns:
        The Image Id as a string of digits.

    Raises:
        InputProblem: If the cell is blank or not a whole number. A row with
            no usable Id cannot be checked, and guessing at one is worse.
    """
    # bool is a subclass of int, and True must not become Image Id "1".
    if isinstance(raw, bool):
        raise InputProblem(f"Image Id {raw!r} is not a number.")
    if isinstance(raw, int):
        return str(raw)
    if isinstance(raw, float):
        if raw.is_integer():
            return str(int(raw))
        raise InputProblem(f"Image Id {raw!r} is not a whole number.")

    text = str(raw).strip() if raw is not None else ""
    if not text.isdigit():
        raise InputProblem(f"Image Id {text!r} is blank or not a whole number.")
    return text


def url_has_image_id(url: str, image_id: str) -> bool:
    """Whether a preview URL names this exact Image Id.

    A plain substring test would let 1507207 match a URL for 15072070, which
    would judge one image by another's preview. The Id must not have a digit
    on either side.

    Args:
        url: The preview document's URL.
        image_id: Normalized Image Id (digits).

    Returns:
        True if the Id appears as a whole number in the URL.
    """
    return bool(image_id) and re.search(rf"(?<!\d){re.escape(image_id)}(?!\d)", url or "") is not None


def has_file_size(file_size: str) -> bool:
    """Whether a file size value is a real, non-zero size.

    Args:
        file_size: The grid's "file size MB" cell, often blank.

    Returns:
        True only for a readable number above zero.
    """
    match = FILE_SIZE_NUMBER.match(file_size or "")
    return bool(match) and float(match.group(1).replace(",", "")) > 0


def decide(
    pdf_loaded: bool,
    corrupted_message: bool,
    file_size: str,
    storage_location: str,
    checked_at: datetime,
) -> CheckResult:
    """Apply the preview rule to one image.

    Args:
        pdf_loaded: PDF.js parsed the document in the preview.
        corrupted_message: The preview showed the corrupted-file message.
        file_size: The grid's file size cell, recorded in the output. A
            non-zero size also counts as good when the preview shows nothing
            either way (Nick's "or shows a file size").
        storage_location: The grid's storage location cell, recorded only.
        checked_at: When the preview was read.

    Returns:
        The verdict. Every combination maps to a Status; nothing here raises.
    """
    size = file_size.strip()
    storage = storage_location.strip()

    if pdf_loaded and corrupted_message:
        # Should not happen. Two signals disagreeing is for a person.
        return blank_result(Status.error(ERROR_BOTH_SIGNALS), checked_at, storage, size)

    if corrupted_message:
        return blank_result(Status.CORRUPTED, checked_at, storage, size)

    if pdf_loaded or has_file_size(size):
        return CheckResult(
            status=Status.OK,
            exclude=EXCLUDE_GOOD,
            file_size=size,
            storage_location=storage,
            checked_at=checked_at,
        )

    return blank_result(Status.error(ERROR_NO_SIGNAL), checked_at, storage, size)


def blank_result(
    status: str, checked_at: datetime, storage_location: str = "", file_size: str = ""
) -> CheckResult:
    """Build a result that leaves Exclude blank.

    Used for every outcome except OK, including the DocumentProblem cases
    (case not found, image not found, ambiguous search).

    Args:
        status: The Status to write.
        checked_at: When the check happened.
        storage_location: Storage location text if it was read.
        file_size: File size text if it was read.

    Returns:
        A CheckResult with Exclude "".
    """
    return CheckResult(
        status=status,
        exclude="",
        file_size=file_size,
        storage_location=storage_location,
        checked_at=checked_at,
    )

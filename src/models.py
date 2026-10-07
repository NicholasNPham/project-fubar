"""Plain data shared between modules: the input row and the verdict for it."""

from dataclasses import dataclass
from datetime import datetime


class Status:
    """Every value the Status column can hold.

    The exact text matters: a person filters the output sheet on it, and a
    resumed run retries anything starting with ERROR_PREFIX.
    """

    OK = "OK"
    CORRUPTED = "CORRUPTED"
    CASE_NOT_FOUND = "CASE NOT FOUND"
    IMAGE_ID_NOT_FOUND = "IMAGE ID NOT FOUND"
    ERROR_PREFIX = "ERROR: "

    @staticmethod
    def error(reason: str) -> str:
        """Build an ERROR status.

        Args:
            reason: Short plain explanation, e.g. "search matched 3 images".

        Returns:
            "ERROR: <reason>".
        """
        return f"{Status.ERROR_PREFIX}{reason}"


@dataclass(frozen=True)
class ImageRow:
    """One input row to check.

    Attributes:
        row_number: Sheet row it came from, so the result goes back to the
            same row of the output copy.
        case_number: As typed in the sheet, trimmed.
        image_id: Plain digits. Excel hands numbers back as floats, and
            15072070.0 must still match STAC's "15072070".
    """

    row_number: int
    case_number: str
    image_id: str


@dataclass(frozen=True)
class PreviewReading:
    """What STAC showed for one image, unjudged. checker.decide judges it.

    Attributes:
        pdf_loaded: PDF.js parsed the document in the preview pane.
        corrupted_message: The corrupted-file message appeared.
        file_size: The grid's "file size MB" cell, often blank.
        storage_location: The grid's "storage location" cell.
    """

    pdf_loaded: bool
    corrupted_message: bool
    file_size: str
    storage_location: str


@dataclass(frozen=True)
class CheckResult:
    """The verdict for one image, ready to write out.

    Attributes:
        status: A Status value.
        exclude: "*" only when the image is good, otherwise "".
        file_size: Visible File Size text, e.g. "16.72 MB", or "".
        storage_location: Visible Storage Location text, or "".
        checked_at: When the verdict was made.
    """

    status: str
    exclude: str
    file_size: str
    storage_location: str
    checked_at: datetime

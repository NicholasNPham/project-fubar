"""Tests for checker.py: the preview rule and Image Id normalization."""

from datetime import datetime

import pytest

from checker import EXCLUDE_GOOD, decide, has_file_size, normalize_image_id, url_has_image_id
from exceptions import InputProblem
from models import Status

NOW = datetime(2026, 10, 6, 12, 0, 0)


def test_known_good_image() -> None:
    """15072070: PDF.js loaded it, blank grid file size, Local. Marked '*'."""
    result = decide(pdf_loaded=True, corrupted_message=False, file_size="", storage_location="Local", checked_at=NOW)
    assert result.status == Status.OK
    assert result.exclude == EXCLUDE_GOOD
    assert result.storage_location == "Local"
    assert result.checked_at == NOW


def test_corrupted_message_leaves_exclude_blank() -> None:
    """The corrupted-file message means the row is left alone."""
    result = decide(pdf_loaded=False, corrupted_message=True, file_size="", storage_location="Local", checked_at=NOW)
    assert result.status == Status.CORRUPTED
    assert result.exclude == ""


def test_corrupted_message_beats_file_size() -> None:
    """A file size does not override a preview that says the file is corrupted."""
    result = decide(pdf_loaded=False, corrupted_message=True, file_size="16.72", storage_location="", checked_at=NOW)
    assert result.status == Status.CORRUPTED
    assert result.exclude == ""


def test_file_size_marks_good_when_preview_says_nothing() -> None:
    """Nick's "or shows a file size": no message either way, real size, marked."""
    result = decide(pdf_loaded=False, corrupted_message=False, file_size="16.72", storage_location="", checked_at=NOW)
    assert result.status == Status.OK
    assert result.exclude == EXCLUDE_GOOD
    assert result.file_size == "16.72"


def test_no_signal_is_an_error_not_a_mark() -> None:
    """Neither loaded nor errored, and no size: never guessed as good."""
    result = decide(pdf_loaded=False, corrupted_message=False, file_size="", storage_location="", checked_at=NOW)
    assert result.status.startswith(Status.ERROR_PREFIX)
    assert result.exclude == ""


def test_both_signals_is_an_error() -> None:
    """Loaded and showed the message at once: a person looks."""
    result = decide(pdf_loaded=True, corrupted_message=True, file_size="", storage_location="", checked_at=NOW)
    assert result.status.startswith(Status.ERROR_PREFIX)
    assert result.exclude == ""


@pytest.mark.parametrize("url, expected", [
    ("https://x/web/viewer.html?file=%2Fimages%2F15072070%2Fdoc", True),
    ("https://x/web/viewer.html?file=/api/image?id=15072070", True),
    ("https://x/web/viewer.html?file=/api/image?id=15072070&v=2", True),
    ("https://x/web/viewer.html?file=/api/image?id=150720701", False),  # longer Id
    ("https://x/web/viewer.html?file=/api/image?id=115072070", False),  # longer Id
    ("https://x/web/viewer.html?file=/api/image?id=1507207", False),  # shorter Id
    ("", False),
])
def test_url_has_image_id(url: str, expected: bool) -> None:
    """Only the exact Id counts; a longer or shorter number must not match."""
    assert url_has_image_id(url, "15072070") is expected


def test_url_has_image_id_rejects_blank_id() -> None:
    """A blank Id never matches anything."""
    assert url_has_image_id("https://x/15072070", "") is False


@pytest.mark.parametrize("size, expected", [
    ("16.72", True), ("16.72 MB", True), ("1,024", True), ("0.01", True),
    ("", False), ("  ", False), ("0", False), ("0.00", False), ("n/a", False),
])
def test_has_file_size(size: str, expected: bool) -> None:
    """Only a readable number above zero counts as a file size."""
    assert has_file_size(size) is expected


@pytest.mark.parametrize(
    "raw, expected",
    [(15072070, "15072070"), (15072070.0, "15072070"), ("15072070", "15072070"), (" 15072070 ", "15072070")],
)
def test_normalize_image_id(raw: object, expected: str) -> None:
    """Excel ints, floats and text all become plain digits."""
    assert normalize_image_id(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "  ", "abc", "1507A070", 15072070.5, True, "15072070.0"])
def test_normalize_image_id_rejects_bad_values(raw: object) -> None:
    """Blank, non-numeric, fractional and boolean cells are refused, not guessed."""
    with pytest.raises(InputProblem):
        normalize_image_id(raw)

"""STAC Image Id corruption check.

Full run:  python main.py --input list.xlsx [--limit N] [--headed] [--resume checked.xlsx]
Probe:     python main.py --probe CASE_NUMBER IMAGE_ID [IMAGE_ID ...] [--headed]

A full run writes <input>_checked_<stamp>.xlsx next to the input and never
touches the input. The probe reads one case and prints what STAC showed, to
confirm selectors before a full run.
"""

import argparse
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

import yaml
from selenium.common.exceptions import WebDriverException

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

# src/ modules import each other flat, so the path insert must come first.
from checker import blank_result, decide, normalize_image_id  # noqa: E402
from excel_io import (  # noqa: E402
    OutputBook,
    conflicting_image_ids,
    group_by_case,
    resume_output,
    start_output,
)
from exceptions import DocumentProblem, InputProblem, SystemProblem  # noqa: E402
from logger import get_logger, setup_logging  # noqa: E402
from models import CheckResult, ImageRow, Status  # noqa: E402
from stac import StacSession  # noqa: E402

logger = get_logger("main")

CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"
REQUIRED_STAC_KEYS = ("url", "username", "password")
DEFAULT_LOG_DIR = "logs"
DEFAULT_CHECKPOINT_EVERY = 25

# Tries per case. The second gets a fresh browser.
CASE_ATTEMPTS = 2
# Sign-in attempts when restarting the browser, before the run stops.
RESTART_ATTEMPTS = 3
# Fresh browser every N cases. PDF.js previews leak memory (PCSO911), and a
# full run is thousands of previews in one Chrome. 0 turns it off.
DEFAULT_RESTART_EVERY_CASES = 100
# An ERROR reason longer than this is cut, so the Status column stays readable.
MAX_REASON_LENGTH = 150

EXIT_OK = 0
EXIT_SYSTEM_PROBLEM = 1
EXIT_CONFIG_PROBLEM = 2

ERROR_DUPLICATE_ID = "Image Id listed under more than one case number"


def load_config(path: Path) -> dict:
    """Read config.yaml and check the STAC settings are filled in.

    Args:
        path: config.yaml.

    Returns:
        The parsed config.

    Raises:
        InputProblem: If the file is missing, unreadable, or lacks a STAC
            setting. Values are never echoed, only key names.
    """
    if not path.exists():
        raise InputProblem(f"No config at {path}. Copy config/config.example.yaml there and fill it in.")
    try:
        config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as error:
        raise InputProblem(f"Could not read {path}: {error}") from error

    stac = config.get("stac") or {}
    missing = [key for key in REQUIRED_STAC_KEYS if not stac.get(key)]
    if missing:
        raise InputProblem(f"config.yaml is missing stac.{', stac.'.join(missing)}")
    return config


class Recorder:
    """Writes results to the output copy, counts them, and checkpoints."""

    def __init__(self, book: OutputBook, checkpoint_every: int) -> None:
        """Start with nothing recorded.

        Args:
            book: The output copy.
            checkpoint_every: Save after this many rows.
        """
        self.book = book
        self.checkpoint_every = max(1, checkpoint_every)
        self.counts: Counter = Counter()
        self._since_save = 0

    def record(self, rows: list[ImageRow], result: CheckResult) -> None:
        """Write one result to every row it applies to.

        Args:
            rows: Rows sharing this Image Id and case (normally one).
            result: The verdict.
        """
        for row in rows:
            self.book.write_result(row.row_number, result)
            self.counts[result.status] += 1
            logger.info("%s %s: %s %s", row.case_number, row.image_id, result.status, result.file_size)
        self._since_save += len(rows)
        if self._since_save >= self.checkpoint_every:
            self.save()

    def save(self) -> None:
        """Checkpoint. A failed save is a warning, not a stop: the next one retries."""
        try:
            self.book.save()
            self._since_save = 0
        except SystemProblem as error:
            logger.warning("%s", error)


def check_case(stac: StacSession, case_number: str, images: dict[str, list[ImageRow]], recorder: Recorder) -> None:
    """Check every image on one case, retrying once on a fresh browser.

    Images already recorded are not redone on the retry. After the last try
    the rest get an ERROR status and the run moves to the next case.

    Args:
        stac: The signed-in session.
        case_number: Case to open.
        images: Image Id to the rows that carry it.
        recorder: Where results go.

    Raises:
        SystemProblem: Only if the browser cannot be restarted (STAC down or
            sign-in refused), which stops the run.
    """
    pending = dict(images)
    for attempt in range(1, CASE_ATTEMPTS + 1):
        try:
            stac.open_case(case_number)
            for image_id in list(pending):
                try:
                    reading = stac.read_image_preview(image_id)
                    result = decide(
                        reading.pdf_loaded, reading.corrupted_message, reading.file_size,
                        reading.storage_location, datetime.now(),
                    )
                except DocumentProblem as problem:
                    result = blank_result(problem.status, datetime.now())
                recorder.record(pending.pop(image_id), result)
            return
        except DocumentProblem as problem:
            # Case-level: not found, or several cases. Applies to every image.
            for rows in pending.values():
                recorder.record(rows, blank_result(problem.status, datetime.now()))
            return
        except (SystemProblem, WebDriverException) as error:
            # WebDriverException: a Selenium error stac.py did not anticipate.
            # It is still about this case, so it gets the same retry.
            reason = short_reason(error)
            if attempt == CASE_ATTEMPTS:
                for rows in pending.values():
                    recorder.record(rows, blank_result(Status.error(reason), datetime.now()))
                return
            logger.warning("%s: %s. Restarting the browser and retrying.", case_number, reason)
            restart_browser(stac)


def short_reason(error: Exception) -> str:
    """First line of an error, trimmed, for the Status column.

    Selenium messages carry a full stack trace after the first line, which
    does not belong in a spreadsheet cell.

    Args:
        error: Any exception.

    Returns:
        One readable line.
    """
    lines = str(error).strip().splitlines()
    first = lines[0].strip() if lines else type(error).__name__
    return first[:MAX_REASON_LENGTH]


def restart_browser(stac: StacSession) -> None:
    """Close Chrome and sign in again, a few times before giving up.

    One failed sign-in during a restart used to end the whole run. A network
    blip or a slow STAC login page should not cost hours of progress.

    Args:
        stac: The session to restart.

    Raises:
        SystemProblem: If every attempt fails. STAC is down or the password
            changed; the run stops and --resume picks it up later.
    """
    for attempt in range(1, RESTART_ATTEMPTS + 1):
        stac.close()
        try:
            stac.open()
            return
        except SystemProblem as error:
            logger.warning("Browser restart %s of %s failed: %s", attempt, RESTART_ATTEMPTS, short_reason(error))
            if attempt == RESTART_ATTEMPTS:
                raise


def run(config: dict, input_path: Path, resume: Path | None, limit: int | None, headed: bool) -> int:
    """The full run over the input sheet.

    Args:
        config: Parsed config.
        input_path: Input spreadsheet. Never written.
        resume: A checked copy to continue, or None for a fresh one.
        limit: Check only the first N pending rows.
        headed: Show the browser.

    Returns:
        Exit code.
    """
    book = resume_output(input_path, resume) if resume else start_output(input_path, datetime.now())
    logger.info("Writing results to %s", book.path)
    recorder = Recorder(book, int(config.get("run", {}).get("checkpoint_every", DEFAULT_CHECKPOINT_EVERY)))

    rows, bad = book.read_rows()
    for row_number, status in bad.items():
        if not book.is_done(row_number):
            book.write_result(row_number, blank_result(status, datetime.now()))
            recorder.counts[status] += 1
            logger.warning("Row %s: %s", row_number, status)

    conflicts = conflicting_image_ids(rows)
    pending = []
    for row in rows:
        if book.is_done(row.row_number):
            continue
        if row.image_id in conflicts:
            recorder.record([row], blank_result(Status.error(ERROR_DUPLICATE_ID), datetime.now()))
            continue
        pending.append(row)

    if limit:
        pending = pending[:limit]
    grouped = group_by_case(pending)
    logger.info("%s rows to check across %s cases", len(pending), len(grouped))

    restart_every = int(config.get("run", {}).get("restart_every_cases", DEFAULT_RESTART_EVERY_CASES))
    try:
        with StacSession(config, headed) as stac:
            for number, (case_number, images) in enumerate(grouped.items(), start=1):
                if restart_every and number > 1 and (number - 1) % restart_every == 0:
                    logger.info("Fresh browser after %s cases", number - 1)
                    recorder.save()
                    restart_browser(stac)
                logger.info("Case %s of %s: %s", number, len(grouped), case_number)
                check_case(stac, case_number, images, recorder)
    finally:
        # Whatever stopped the run, what was checked so far is kept.
        recorder.save()
        logger.info("Summary: %s", ", ".join(f"{s}={n}" for s, n in recorder.counts.most_common()) or "nothing checked")
        logger.info("Results in %s", book.path)
    return EXIT_OK


def probe(config: dict, case_number: str, raw_ids: list[str], headed: bool, log_dir: Path) -> int:
    """Run the probe on one case and save the report.

    Args:
        config: Parsed config.
        case_number: Case to open.
        raw_ids: Image Ids as typed.
        headed: Show the browser.
        log_dir: Where the report file goes.

    Returns:
        Exit code.
    """
    image_ids = [normalize_image_id(raw) for raw in raw_ids]
    with StacSession(config, headed) as stac:
        lines = stac.probe(case_number, image_ids)

    report = "\n".join(lines)
    print(report)
    path = log_dir / f"probe_{datetime.now():%Y%m%d_%H%M%S}.txt"
    path.write_text(report + "\n", encoding="utf-8")
    print(f"\nSaved to {path}")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and run the full check or the probe.

    Args:
        argv: Command-line arguments, for tests. None reads sys.argv.

    Returns:
        Exit code: 0 ok, 1 STAC or browser problem, 2 config or input problem.
    """
    parser = argparse.ArgumentParser(description="Mark good STAC images with * in Exclude.")
    parser.add_argument("--input", type=Path, help="Input spreadsheet (never modified).")
    parser.add_argument("--resume", type=Path, help="A partly finished _checked_ copy to continue.")
    parser.add_argument("--limit", type=int, help="Check only the first N pending rows.")
    parser.add_argument("--headed", action="store_true", help="Show the browser window.")
    parser.add_argument("--probe", nargs="+", metavar=("CASE", "IMAGE_ID"),
                        help="Probe one case: CASE_NUMBER IMAGE_ID [IMAGE_ID ...].")
    args = parser.parse_args(argv)

    if args.probe is not None and len(args.probe) < 2:
        parser.error("--probe needs a case number and at least one Image Id")
    if args.probe is None and args.input is None:
        parser.error("--input is required unless --probe is used")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")

    try:
        config = load_config(CONFIG_PATH)
    except InputProblem as error:
        print(f"Config problem: {error}", file=sys.stderr)
        return EXIT_CONFIG_PROBLEM

    log_dir = PROJECT_ROOT / config.get("paths", {}).get("logs", DEFAULT_LOG_DIR)
    setup_logging(log_dir)

    try:
        if args.probe is not None:
            return probe(config, args.probe[0], args.probe[1:], args.headed, log_dir)
        return run(config, args.input, args.resume, args.limit, args.headed)
    except InputProblem as error:
        logger.error("Input problem: %s", error)
        return EXIT_CONFIG_PROBLEM
    except SystemProblem as error:
        logger.error("Stopped: %s", error)
        return EXIT_SYSTEM_PROBLEM
    except KeyboardInterrupt:
        logger.warning("Interrupted. Progress so far is saved; continue with --resume.")
        return EXIT_SYSTEM_PROBLEM
    except Exception:
        logger.exception("Unexpected error. This is a bug.")
        return EXIT_SYSTEM_PROBLEM


if __name__ == "__main__":
    sys.exit(main())

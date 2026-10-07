"""Named error types.

Same split as DALYN. A DocumentProblem is about one case or image: it becomes
that row's Status and the run carries on. A SystemProblem means STAC or the
browser is broken: restart and retry, and stop if sign-in itself fails.
InputProblem is a bad spreadsheet, caught before the browser ever opens.
"""


class FubarError(Exception):
    """Base class for every error this tool raises on purpose."""


class DocumentProblem(FubarError):
    """One case or image could not be judged. Carries the Status to write.

    One class with a status, rather than a subclass per outcome, because the
    only thing the caller does with it is write that status to the row.
    """

    def __init__(self, status: str) -> None:
        """Store the Status text for the row.

        Args:
            status: A models.Status value, e.g. Status.CASE_NOT_FOUND or
                Status.error("search matched 3 images").
        """
        super().__init__(status)
        self.status = status


class SystemProblem(FubarError):
    """STAC or Chrome did not behave. Not the fault of any one row."""


class InputProblem(FubarError):
    """The input spreadsheet is missing a column or cannot be read."""

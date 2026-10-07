"""Framework-independent policies for Qlib experiment execution."""

from bisect import bisect_right
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterator

ALPHA158_LABEL_LOOKAHEAD_DAYS = 2


class WorkerError(Exception):
    """Stable, user-safe quant worker error."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        stage: str | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.stage = stage
        self.retryable = retryable

    def as_detail(self) -> dict[str, object]:
        detail: dict[str, object] = {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.stage is not None:
            detail["stage"] = self.stage
        return detail


class QlibStageError(RuntimeError):
    """Qlib failure annotated with the workflow stage that produced it."""

    def __init__(self, stage: str, cause: Exception) -> None:
        super().__init__(str(cause))
        self.stage = stage


@dataclass(frozen=True)
class DatasetInspection:
    errors: tuple[str, ...]
    calendar: tuple[date, ...] = ()
    manifest_hash: str | None = None
    manifest_verified: bool = False

    @property
    def ready(self) -> bool:
        return not self.errors

    @property
    def start_date(self) -> date | None:
        return self.calendar[0] if self.calendar else None

    @property
    def end_date(self) -> date | None:
        return self.calendar[-1] if self.calendar else None

    @property
    def max_test_end(self) -> date | None:
        if len(self.calendar) <= ALPHA158_LABEL_LOOKAHEAD_DAYS:
            return None
        return self.calendar[-(ALPHA158_LABEL_LOOKAHEAD_DAYS + 1)]

    @property
    def trading_days(self) -> int | None:
        return len(self.calendar) if self.calendar else None


@contextmanager
def stage(name: str) -> Iterator[None]:
    """Annotate provider failures with their execution stage."""
    try:
        yield
    except QlibStageError:
        raise
    except Exception as exc:
        raise QlibStageError(name, exc) from exc


def preflight_experiment(request: Any, inspection: DatasetInspection) -> list[str]:
    """Validate requested segments against dataset coverage and label lookahead."""
    if not inspection.ready:
        return list(inspection.errors)
    if inspection.start_date is None or inspection.end_date is None:
        return ["Dataset calendar coverage is unavailable."]

    errors: list[str] = []
    for name, segment in (
        ("train", request.train),
        ("valid", request.valid),
        ("test", request.test),
    ):
        if segment.start < inspection.start_date or segment.end > inspection.end_date:
            errors.append(
                f"{name} range {segment.start}..{segment.end} is outside dataset "
                f"coverage {inspection.start_date}..{inspection.end_date}"
            )

    remaining_days = len(inspection.calendar) - bisect_right(
        inspection.calendar,
        request.test.end,
    )
    if (
        request.test.end <= inspection.end_date
        and remaining_days < ALPHA158_LABEL_LOOKAHEAD_DAYS
    ):
        errors.append(
            f"test end requires {ALPHA158_LABEL_LOOKAHEAD_DAYS} later trading days "
            f"for the Alpha158 label; latest valid test end is {inspection.max_test_end}"
        )
    return errors

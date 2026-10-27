from __future__ import annotations

from traceback import walk_tb
from typing import Type

import pytest

from pluggy import Result


@pytest.mark.parametrize("method", ["constructor", "from_call", "force_exception"])
@pytest.mark.parametrize("exception_type", [ValueError, SystemExit])
def test_get_result_exception_traceback(
    method: str, exception_type: Type[BaseException]
) -> None:
    exception = exception_type("boom")

    def fail() -> None:
        raise exception

    def caller() -> None:
        fail()

    result: Result[None]
    if method == "from_call":
        result = Result.from_call(caller)
    else:
        try:
            caller()
        except BaseException as exc:
            if method == "constructor":
                result = Result(None, exc)
            else:
                result = Result(None, RuntimeError("previous exception"))
                result.force_exception(exc)

    original_traceback = exception.__traceback__
    original_frames = list(walk_tb(original_traceback))
    assert [frame.f_code.co_name for frame, _ in original_frames][-2:] == [
        "caller",
        "fail",
    ]

    for _ in range(3):
        with pytest.raises(exception_type, match="boom") as caught:
            result.get_result()
        assert caught.value is exception
        # Only the current caller and get_result should precede the original frames.
        assert list(walk_tb(caught.tb))[2:] == original_frames

    assert result.exception is exception
    assert result.excinfo == (exception_type, exception, original_traceback)


@pytest.mark.parametrize("method", ["constructor", "force_exception"])
def test_get_result_exception_without_traceback(method: str) -> None:
    exception = ValueError("boom")
    if method == "constructor":
        result = Result(42, exception)
    else:
        result = Result(42, None)
        result.force_exception(exception)

    for _ in range(3):
        with pytest.raises(ValueError, match="boom") as caught:
            result.get_result()
        assert caught.value is exception
        assert len(list(walk_tb(caught.tb))) == 2

    assert result.excinfo == (ValueError, exception, None)


def test_force_result_clears_exception() -> None:
    def fail() -> int:
        raise ValueError("boom")

    result = Result.from_call(fail)
    with pytest.raises(ValueError, match="boom"):
        result.get_result()

    result.force_result(42)
    assert result.get_result() == 42
    assert result.exception is None
    assert result.excinfo is None

from traceback import extract_tb
from typing import Type

import pytest

from pluggy import Result


@pytest.mark.parametrize("exception_type", [ValueError, SystemExit])
@pytest.mark.parametrize("creation", ["from_call", "constructor", "force_exception"])
def test_get_result_preserves_traceback(
    exception_type: Type[BaseException], creation: str
) -> None:
    exception = exception_type("original failure")

    def fail() -> None:
        raise exception

    outcome = Result.from_call(fail)
    original_tb = exception.__traceback__
    assert [frame.name for frame in extract_tb(original_tb)] == ["from_call", "fail"]
    if creation == "constructor":
        outcome = Result(None, exception)
    elif creation == "force_exception":
        outcome = Result(None, None)
        outcome.force_exception(exception)

    for _ in range(3):
        with pytest.raises(exception_type) as caught:
            outcome.get_result()
        assert caught.value is exception
        assert caught.tb.tb_next is not None
        assert caught.tb.tb_next.tb_frame.f_code is Result.get_result.__code__
        assert caught.tb.tb_next.tb_next is original_tb
        assert outcome.exception is exception
        assert outcome.excinfo == (exception_type, exception, original_tb)


@pytest.mark.parametrize("raised", [False, True])
def test_force_exception_replaces_traceback(raised: bool) -> None:
    def fail() -> None:
        raise ValueError("original failure")

    outcome = Result.from_call(fail)
    with pytest.raises(ValueError):
        outcome.get_result()

    replacement = RuntimeError("replacement failure")
    if raised:
        try:
            raise replacement
        except RuntimeError:
            pass
    replacement_tb = replacement.__traceback__
    outcome.force_exception(replacement)

    for _ in range(3):
        with pytest.raises(RuntimeError, match="replacement failure") as caught:
            outcome.get_result()
        assert caught.value is replacement
        assert caught.tb.tb_next is not None
        assert caught.tb.tb_next.tb_next is replacement_tb
        assert outcome.excinfo == (RuntimeError, replacement, replacement_tb)


def test_force_result_clears_exception() -> None:
    def fail() -> None:
        raise ValueError("original failure")

    outcome = Result.from_call(fail)
    with pytest.raises(ValueError):
        outcome.get_result()

    outcome.force_result(None)
    assert outcome.get_result() is None
    assert outcome.exception is None
    assert outcome.excinfo is None


def test_from_call_success() -> None:
    result = [1, 2]
    outcome = Result.from_call(lambda: result)
    assert outcome.get_result() is result
    assert outcome.exception is None
    assert outcome.excinfo is None

from traceback import extract_tb
from typing import Type

import pytest

from pluggy import Result


@pytest.mark.parametrize("exception_type", [ValueError, SystemExit])
@pytest.mark.parametrize("source", ["from_call", "constructor", "force_exception"])
def test_get_result_exception(
    exception_type: Type[BaseException], source: str
) -> None:
    exception = exception_type("original")

    def fail() -> None:
        raise exception

    result = Result.from_call(fail)
    if source == "constructor":
        result = Result(None, exception)
    elif source == "force_exception":
        result = Result(None, None)
        result.force_exception(exception)

    original_excinfo = result.excinfo
    original_stack = extract_tb(exception.__traceback__)
    assert original_stack[-1].name == "fail"

    for _ in range(3):
        with pytest.raises(exception_type) as excinfo:
            result.get_result()
        assert excinfo.value is exception
        stack = extract_tb(excinfo.value.__traceback__)
        assert [frame.name for frame in stack[:2]] == [
            "test_get_result_exception",
            "get_result",
        ]
        assert stack[2:] == original_stack

    assert result.exception is exception
    assert result.excinfo == original_excinfo


def test_force_exception_replaces_traceback() -> None:
    def fail() -> None:
        raise ValueError("original")

    result = Result.from_call(fail)
    with pytest.raises(ValueError):
        result.get_result()

    exception = RuntimeError("replacement")
    result.force_exception(exception)
    for _ in range(3):
        with pytest.raises(RuntimeError) as excinfo:
            result.get_result()
        assert excinfo.value is exception
        assert [frame.name for frame in extract_tb(excinfo.value.__traceback__)] == [
            "test_force_exception_replaces_traceback",
            "get_result",
        ]
    assert result.excinfo == (RuntimeError, exception, None)


def test_force_result_clears_exception() -> None:
    def fail() -> list[int]:
        raise ValueError("original")

    result = Result.from_call(fail)
    with pytest.raises(ValueError):
        result.get_result()

    value = [1, 2]
    result.force_result(value)
    assert result.get_result() is value
    assert result.exception is None
    assert result.excinfo is None

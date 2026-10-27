from traceback import extract_tb
from typing import Type

import pytest

from pluggy import Result


@pytest.mark.parametrize("exception_type", [ValueError, SystemExit])
@pytest.mark.parametrize("force_exception", [False, True])
def test_get_result_preserves_traceback(
    exception_type: Type[BaseException], force_exception: bool
) -> None:
    exception = exception_type("original")
    cause = RuntimeError("cause")

    def fail() -> int:
        raise exception from cause

    result = Result.from_call(fail)
    original_traceback = exception.__traceback__
    if force_exception:
        result = Result(42, None)
        result.force_exception(exception)

    original_excinfo = (exception_type, exception, original_traceback)
    assert result.excinfo == original_excinfo
    tracebacks = []
    for _ in range(3):
        with pytest.raises(exception_type) as excinfo:
            result.get_result()
        assert excinfo.value is exception
        assert excinfo.value.__cause__ is cause
        tracebacks.append(extract_tb(excinfo.value.__traceback__))

    assert tracebacks[0] == tracebacks[1] == tracebacks[2]
    assert result.excinfo == original_excinfo
    assert [frame.name for frame in tracebacks[0]] == [
        "test_get_result_preserves_traceback",
        "get_result",
        "from_call",
        "fail",
    ]


@pytest.mark.parametrize("raised", [False, True])
def test_force_exception_replaces_traceback(raised: bool) -> None:
    def fail() -> int:
        raise ValueError("original")

    result = Result.from_call(fail)
    with pytest.raises(ValueError, match="original"):
        result.get_result()

    replacement = LookupError("replacement")
    if raised:
        try:
            raise replacement
        except LookupError:
            pass
    original_traceback = replacement.__traceback__
    result.force_exception(replacement)

    for _ in range(3):
        with pytest.raises(LookupError, match="replacement") as excinfo:
            result.get_result()
        assert excinfo.value is replacement
        traceback = excinfo.value.__traceback__
        assert traceback is not None
        assert traceback.tb_next is not None
        assert traceback.tb_next.tb_next is original_traceback
        assert result.excinfo == (LookupError, replacement, original_traceback)


def test_force_result_clears_exception() -> None:
    result = Result.from_call(lambda: 42)
    assert result.get_result() == 42
    exception = ValueError("original")
    result.force_exception(exception)
    with pytest.raises(ValueError, match="original"):
        result.get_result()

    result.force_result(43)
    assert result.exception is None
    assert result.excinfo is None
    assert result.get_result() == 43

    result.force_exception(RuntimeError("replacement"))
    with pytest.raises(RuntimeError, match="replacement") as excinfo:
        result.get_result()
    assert [frame.name for frame in extract_tb(excinfo.value.__traceback__)] == [
        "test_force_result_clears_exception",
        "get_result",
    ]

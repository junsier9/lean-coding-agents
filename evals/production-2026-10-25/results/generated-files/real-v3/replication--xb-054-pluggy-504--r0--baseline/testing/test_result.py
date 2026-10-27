import traceback
from typing import Type

import pytest

from pluggy import Result


@pytest.mark.parametrize("exception_type", [ValueError, SystemExit])
@pytest.mark.parametrize("capture", ["constructor", "from_call", "force_exception"])
def test_get_result_preserves_traceback(
    exception_type: Type[BaseException], capture: str
) -> None:
    def fail() -> None:
        raise exception_type("original error")

    if capture == "from_call":
        result = Result.from_call(fail)
    else:
        with pytest.raises(exception_type) as raised:
            fail()
        if capture == "constructor":
            result = Result(None, raised.value)
        else:
            result = Result(None, None)
            result.force_exception(raised.value)

    original_excinfo = result.excinfo
    assert original_excinfo is not None
    exception = original_excinfo[1]
    original_traceback = original_excinfo[2]
    original_frames = traceback.extract_tb(original_traceback)
    assert original_frames[-1].name == "fail"

    tracebacks = []
    for _ in range(3):
        with pytest.raises(exception_type, match="original error") as raised:
            result.get_result()
        assert raised.value is exception
        tracebacks.append(traceback.extract_tb(raised.value.__traceback__))

    assert tracebacks[0] == tracebacks[1] == tracebacks[2]
    assert [frame.name for frame in tracebacks[0][:2]] == [
        "test_get_result_preserves_traceback",
        "get_result",
    ]
    assert tracebacks[0][2:] == original_frames
    assert result.exception is exception
    assert result.excinfo == original_excinfo


def test_force_exception_replaces_traceback() -> None:
    def fail() -> None:
        raise ValueError("original error")

    def replacement_fail() -> None:
        raise OSError("replacement error")

    result = Result.from_call(fail)
    with pytest.raises(ValueError):
        result.get_result()

    with pytest.raises(OSError) as replacement:
        replacement_fail()
    original_traceback = replacement.value.__traceback__
    result.force_exception(replacement.value)
    # Mutating the exception must not change the traceback captured by Result.
    replacement.value.__traceback__ = None

    for _ in range(3):
        with pytest.raises(OSError, match="replacement error") as raised:
            result.get_result()
        assert raised.value is replacement.value
        tb = raised.value.__traceback__
        assert tb is not None
        assert tb.tb_next is not None
        assert tb.tb_next.tb_next is original_traceback
        assert result.excinfo == (OSError, replacement.value, original_traceback)


def test_force_result_then_fresh_exception() -> None:
    def fail() -> None:
        raise ValueError("original error")

    result = Result.from_call(fail)
    result.force_result(None)
    assert result.get_result() is None
    assert result.exception is None
    assert result.excinfo is None

    exception = RuntimeError("fresh error")
    result.force_exception(exception)
    for _ in range(3):
        with pytest.raises(RuntimeError, match="fresh error") as raised:
            result.get_result()
        assert raised.value is exception
        tb = raised.value.__traceback__
        assert tb is not None
        assert tb.tb_next is not None
        assert tb.tb_next.tb_next is None
        assert result.excinfo == (RuntimeError, exception, None)

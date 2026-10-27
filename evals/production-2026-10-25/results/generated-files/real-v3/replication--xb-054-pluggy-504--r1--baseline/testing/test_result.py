from traceback import extract_tb
from typing import Type

import pytest

from pluggy import Result


@pytest.mark.parametrize("exception_type", [ValueError, SystemExit])
@pytest.mark.parametrize("force_exception", [False, True])
def test_get_result_preserves_original_traceback(
    exception_type: Type[BaseException], force_exception: bool
) -> None:
    def fail() -> None:
        raise exception_type("failure")

    def call() -> None:
        fail()

    result = Result.from_call(call)
    if force_exception:
        exception = result.exception
        assert exception is not None
        result = Result(None, None)
        result.force_exception(exception)

    original_excinfo = result.excinfo
    assert original_excinfo is not None
    original_traceback = original_excinfo[2]
    assert original_traceback is not None
    assert [frame.name for frame in extract_tb(original_traceback)][-2:] == [
        "call",
        "fail",
    ]

    for _ in range(3):
        with pytest.raises(exception_type) as caught:
            result.get_result()

        assert caught.value is original_excinfo[1]
        traceback = caught.value.__traceback__
        assert traceback is not None
        assert traceback.tb_next is not None
        assert traceback.tb_next.tb_next is original_traceback
        assert result.excinfo == original_excinfo


@pytest.mark.parametrize("force_exception", [False, True])
def test_get_result_with_unraised_exception(force_exception: bool) -> None:
    exception = ValueError("failure")
    result: Result[None] = Result(None, None)
    if force_exception:
        result.force_exception(exception)
    else:
        result = Result(None, exception)

    for _ in range(3):
        with pytest.raises(ValueError, match="failure") as caught:
            result.get_result()

        assert caught.value is exception
        traceback = caught.value.__traceback__
        assert traceback is not None
        assert traceback.tb_next is not None
        assert traceback.tb_next.tb_next is None
        assert result.excinfo == (ValueError, exception, None)


def test_force_result_and_exception_replace_traceback() -> None:
    def fail() -> None:
        raise ValueError("original")

    result: Result[object] = Result.from_call(fail)
    with pytest.raises(ValueError, match="original"):
        result.get_result()

    value = object()
    result.force_result(value)
    assert result.exception is None
    assert result.excinfo is None
    assert result.get_result() is value

    def replacement() -> None:
        raise RuntimeError("replacement")

    with pytest.raises(RuntimeError, match="replacement") as caught:
        replacement()

    exception = caught.value
    original_traceback = exception.__traceback__
    result.force_exception(exception)
    assert result.excinfo == (RuntimeError, exception, original_traceback)

    for _ in range(3):
        with pytest.raises(RuntimeError, match="replacement") as caught:
            result.get_result()

        assert caught.value is exception
        traceback = caught.value.__traceback__
        assert traceback is not None
        assert traceback.tb_next is not None
        assert traceback.tb_next.tb_next is original_traceback
        assert result.excinfo == (RuntimeError, exception, original_traceback)

    result.force_exception(ValueError("unraised replacement"))
    assert result.excinfo == (ValueError, result.exception, None)
    with pytest.raises(ValueError, match="unraised replacement") as new_caught:
        result.get_result()
    traceback = new_caught.value.__traceback__
    assert traceback is not None
    assert traceback.tb_next is not None
    assert traceback.tb_next.tb_next is None

    result.force_result(None)
    assert result.excinfo is None
    assert result.get_result() is None

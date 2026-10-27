import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app import download
from errors import AssetMissing


class FakeResponse:
    def __init__(self, chunks=(), close_error=None, on_read=None):
        self.chunks = iter(chunks)
        self.close_error = close_error
        self.on_read = on_read
        self.read_sizes = []
        self.close_calls = 0

    def read(self, size):
        self.read_sizes.append(size)
        if self.on_read is not None:
            self.on_read()
        chunk = next(self.chunks, b"")
        if isinstance(chunk, BaseException):
            raise chunk
        return chunk

    def close(self):
        self.close_calls += 1
        if self.close_error is not None:
            raise self.close_error


class FaultyOutput:
    """Inject an I/O failure while still using a real temporary file."""

    def __init__(self, output, write_error=None, close_error=None):
        self.output = output
        self.name = output.name
        self.write_error = write_error
        self.close_error = close_error

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.output.close()
        if self.close_error is not None:
            raise self.close_error

    def write(self, data):
        result = self.output.write(data)
        if self.write_error is not None:
            raise self.write_error
        return result


class DownloadTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.target = self.root / "asset.bin"
        self.original = b"existing asset"
        self.target.write_bytes(self.original)

    def assert_unchanged(self):
        self.assertEqual(self.target.read_bytes(), self.original)
        self.assertEqual(set(self.root.iterdir()), {self.target})

    def test_success_publishes_complete_file_after_response_close(self):
        chunks = [b"new", b"\x00" + b"x" * 65536, b"tail"]
        expected = b"".join(chunks)
        for as_string in (False, True):
            with self.subTest(as_string=as_string):
                self.target.write_bytes(self.original)
                response = FakeResponse(
                    chunks,
                    on_read=lambda: self.assertEqual(
                        self.target.read_bytes(), self.original
                    ),
                )

                def publish(source, destination):
                    self.assertEqual(response.close_calls, 1)
                    self.assertEqual(Path(source).parent, self.root)
                    self.assertNotEqual(Path(source), self.target)
                    self.assertEqual(Path(source).read_bytes(), expected)
                    self.assertEqual(Path(destination), self.target)
                    self.assertEqual(self.target.read_bytes(), self.original)
                    os.replace(source, destination)

                replace = Mock(side_effect=publish)
                target = str(self.target) if as_string else self.target
                self.assertIs(download(lambda: response, target, replace), True)
                replace.assert_called_once()
                self.assertEqual(response.close_calls, 1)
                self.assertEqual(response.read_sizes, [65536] * 4)
                self.assertEqual(self.target.read_bytes(), expected)
                self.assertEqual(set(self.root.iterdir()), {self.target})

    def test_empty_download_replaces_existing_file(self):
        response = FakeResponse()
        self.assertIs(download(lambda: response, self.target), True)
        self.assertEqual(self.target.read_bytes(), b"")
        self.assertEqual(response.close_calls, 1)
        self.assertEqual(set(self.root.iterdir()), {self.target})

    def test_success_creates_previously_absent_target(self):
        self.target.unlink()
        response = FakeResponse([b"new asset"])
        self.assertIs(download(lambda: response, self.target), True)
        self.assertEqual(self.target.read_bytes(), b"new asset")
        self.assertEqual(response.close_calls, 1)
        self.assertEqual(set(self.root.iterdir()), {self.target})

    def test_opener_missing_returns_false_without_file_operations(self):
        opener = Mock(side_effect=AssetMissing("absent"))
        replace = Mock()
        with patch("app.tempfile.NamedTemporaryFile") as create:
            self.assertIs(download(opener, self.target, replace), False)
        opener.assert_called_once_with()
        create.assert_not_called()
        replace.assert_not_called()
        self.assert_unchanged()

    def test_other_opener_errors_propagate(self):
        for error in (FileNotFoundError("local failure"), RuntimeError("opener")):
            with self.subTest(error=type(error).__name__):
                replace = Mock()
                with self.assertRaises(type(error)) as raised:
                    download(Mock(side_effect=error), self.target, replace)
                self.assertIs(raised.exception, error)
                replace.assert_not_called()
                self.assert_unchanged()

    def test_read_failure_survives_ordinary_close_failure(self):
        for error_type in (OSError, AssetMissing, RuntimeError, KeyboardInterrupt):
            for close_fails in (False, True):
                with self.subTest(error=error_type.__name__, close_fails=close_fails):
                    error = error_type("read failed after partial data")
                    close_error = RuntimeError("close failed") if close_fails else None
                    response = FakeResponse([b"partial", error], close_error)
                    replace = Mock()
                    with self.assertRaises(error_type) as raised:
                        download(lambda: response, self.target, replace)
                    self.assertIs(raised.exception, error)
                    self.assertEqual(response.close_calls, 1)
                    replace.assert_not_called()
                    self.assert_unchanged()

    def test_response_close_failure_prevents_replacement(self):
        for chunks in ([], [b"complete"]):
            for error_type in (OSError, AssetMissing):
                with self.subTest(chunks=chunks, error=error_type.__name__):
                    error = error_type("close failed")
                    response = FakeResponse(chunks, error)
                    replace = Mock()
                    with self.assertRaises(error_type) as raised:
                        download(lambda: response, self.target, replace)
                    self.assertIs(raised.exception, error)
                    self.assertEqual(response.close_calls, 1)
                    replace.assert_not_called()
                    self.assert_unchanged()

    def test_unavailable_parent_closes_response_without_creating_directories(self):
        for parent in (self.root / "missing", self.target):
            for close_fails in (False, True):
                with self.subTest(parent=parent.name, close_fails=close_fails):
                    close_error = RuntimeError("close failed") if close_fails else None
                    response = FakeResponse([b"unread"], close_error)
                    replace = Mock()
                    with self.assertRaises(OSError):
                        download(lambda: response, parent / "asset.bin", replace)
                    self.assertEqual(response.close_calls, 1)
                    self.assertEqual(response.read_sizes, [])
                    replace.assert_not_called()
                    self.assert_unchanged()

    def test_temporary_creation_failure_is_preserved(self):
        error = PermissionError("cannot create temporary file")
        response = FakeResponse(close_error=RuntimeError("close failed"))
        replace = Mock()
        with patch("app.tempfile.NamedTemporaryFile", side_effect=error):
            with self.assertRaises(PermissionError) as raised:
                download(lambda: response, self.target, replace)
        self.assertIs(raised.exception, error)
        self.assertEqual(response.close_calls, 1)
        self.assertEqual(response.read_sizes, [])
        replace.assert_not_called()
        self.assert_unchanged()

    def test_output_write_or_close_failure_cleans_temporary_file(self):
        for operation in ("write", "close"):
            with self.subTest(operation=operation):
                error = OSError(f"output {operation} failed")
                output = tempfile.NamedTemporaryFile(
                    mode="wb", dir=self.root, delete=False
                )
                faulty = FaultyOutput(output, **{f"{operation}_error": error})
                response = FakeResponse([b"partial"], RuntimeError("response close"))
                replace = Mock()
                with patch("app.tempfile.NamedTemporaryFile", return_value=faulty):
                    with self.assertRaises(OSError) as raised:
                        download(lambda: response, self.target, replace)
                self.assertIs(raised.exception, error)
                self.assertTrue(output.closed)
                self.assertEqual(response.close_calls, 1)
                replace.assert_not_called()
                self.assert_unchanged()

    def test_replacement_failure_cleans_temporary_file(self):
        error = PermissionError("replacement denied")
        response = FakeResponse([b"complete"])
        replace = Mock(side_effect=error)
        with self.assertRaises(PermissionError) as raised:
            download(lambda: response, self.target, replace)
        self.assertIs(raised.exception, error)
        self.assertEqual(response.close_calls, 1)
        replace.assert_called_once()
        self.assert_unchanged()

    def test_read_failure_keeps_previously_absent_target_absent(self):
        self.target.unlink()
        error = OSError("read failed")
        response = FakeResponse([b"partial", error])
        replace = Mock()
        with self.assertRaises(OSError) as raised:
            download(lambda: response, self.target, replace)
        self.assertIs(raised.exception, error)
        self.assertEqual(response.close_calls, 1)
        replace.assert_not_called()
        self.assertFalse(self.target.exists())
        self.assertEqual(list(self.root.iterdir()), [])


if __name__ == "__main__":
    unittest.main()

import os
from contextlib import contextmanager
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from app import download
from errors import AssetMissing


class FakeResponse:
    def __init__(self, chunks=(), close_error=None):
        self.chunks = iter(chunks)
        self.close_error = close_error
        self.close_calls = 0
        self.read_calls = 0

    def read(self, size):
        self.read_calls += 1
        chunk = next(self.chunks, b"")
        if isinstance(chunk, BaseException):
            raise chunk
        return chunk

    def close(self):
        self.close_calls += 1
        if self.close_error is not None:
            raise self.close_error


class DownloadTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.target = self.directory / "asset.bin"
        self.original = b"previous complete download"
        self.target.write_bytes(self.original)

    def assert_original_intact(self):
        self.assertEqual(self.target.read_bytes(), self.original)
        self.assertEqual(set(self.directory.iterdir()), {self.target})

    def test_success_with_path_and_string_targets(self):
        for target in (self.target, str(self.target)):
            with self.subTest(target_type=type(target).__name__):
                response = FakeResponse([b"first", b"\x00second", b""])
                self.assertIs(download(lambda: response, target), True)
                self.assertEqual(self.target.read_bytes(), b"first\x00second")
                self.assertEqual(response.close_calls, 1)
                self.assertEqual(set(self.directory.iterdir()), {self.target})

    def test_success_creates_missing_target(self):
        self.target.unlink()
        response = FakeResponse([b"new content"])
        self.assertIs(download(lambda: response, self.target), True)
        self.assertEqual(self.target.read_bytes(), b"new content")
        self.assertEqual(response.close_calls, 1)
        self.assertEqual(set(self.directory.iterdir()), {self.target})

    def test_empty_download_replaces_old_content(self):
        response = FakeResponse()
        self.assertIs(download(lambda: response, self.target), True)
        self.assertEqual(self.target.read_bytes(), b"")
        self.assertEqual(response.close_calls, 1)
        self.assertEqual(set(self.directory.iterdir()), {self.target})

    def test_opener_missing_leaves_destination_unchanged(self):
        for target_exists in (True, False):
            with self.subTest(target_exists=target_exists):
                if not target_exists:
                    self.target.unlink()
                opener = Mock(side_effect=AssetMissing("absent"))
                replace = Mock()
                self.assertIs(download(opener, self.target, replace), False)
                opener.assert_called_once_with()
                replace.assert_not_called()
                if target_exists:
                    self.assert_original_intact()
                else:
                    self.assertEqual(list(self.directory.iterdir()), [])

    def test_other_opener_error_propagates(self):
        error = OSError("connection failed")
        opener = Mock(side_effect=error)
        replace = Mock()
        with self.assertRaises(OSError) as caught:
            download(opener, self.target, replace)
        self.assertIs(caught.exception, error)
        opener.assert_called_once_with()
        replace.assert_not_called()
        self.assert_original_intact()

    def test_read_errors_preserve_target_and_close_once(self):
        for error in (OSError("read failed"), AssetMissing("read failed"),
                      KeyboardInterrupt("interrupted")):
            with self.subTest(error=type(error).__name__):
                response = FakeResponse([b"partial", error])
                replace = Mock()
                with self.assertRaises(type(error)) as caught:
                    download(lambda: response, self.target, replace)
                self.assertIs(caught.exception, error)
                self.assertEqual(response.close_calls, 1)
                replace.assert_not_called()
                self.assert_original_intact()

    def test_read_error_does_not_leave_new_target_or_temporary_file(self):
        self.target.unlink()
        response = FakeResponse([b"partial", OSError("read failed")])
        with self.assertRaises(OSError):
            download(lambda: response, self.target)
        self.assertEqual(response.close_calls, 1)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_close_error_does_not_mask_original_read_error(self):
        error = OSError("original read error")
        response = FakeResponse([b"partial", error], RuntimeError("close failed"))
        replace = Mock()
        with self.assertRaises(OSError) as caught:
            download(lambda: response, self.target, replace)
        self.assertIs(caught.exception, error)
        self.assertEqual(response.close_calls, 1)
        replace.assert_not_called()
        self.assert_original_intact()

    def test_response_close_failure_prevents_replacement(self):
        for error in (OSError("close failed"), AssetMissing("close failed")):
            with self.subTest(error=type(error).__name__):
                response = FakeResponse([b"complete"], error)
                replace = Mock()
                with self.assertRaises(type(error)) as caught:
                    download(lambda: response, self.target, replace)
                self.assertIs(caught.exception, error)
                self.assertEqual(response.close_calls, 1)
                replace.assert_not_called()
                self.assert_original_intact()

    def test_missing_parent_propagates_creation_error_and_closes_response(self):
        for close_error in (None, RuntimeError("close also failed")):
            with self.subTest(close_error=close_error):
                response = FakeResponse([b"data"], close_error)
                replace = Mock()
                target = self.directory / "missing" / "asset.bin"
                with self.assertRaises(FileNotFoundError):
                    download(lambda: response, target, replace)
                self.assertEqual(response.read_calls, 0)
                self.assertEqual(response.close_calls, 1)
                replace.assert_not_called()
                self.assert_original_intact()

    def test_parent_that_is_a_file_propagates_and_closes_response(self):
        response = FakeResponse([b"data"])
        with self.assertRaises(OSError):
            download(lambda: response, self.target / "asset.bin")
        self.assertEqual(response.read_calls, 0)
        self.assertEqual(response.close_calls, 1)
        self.assert_original_intact()

    def test_replacement_sees_complete_file_after_response_close(self):
        response = FakeResponse([b"first", b"second"])

        def replace(source, target):
            self.assertEqual(response.close_calls, 1)
            self.assertEqual(Path(source).parent, self.target.parent)
            self.assertNotEqual(Path(source), self.target)
            self.assertEqual(Path(source).read_bytes(), b"firstsecond")
            self.assertEqual(self.target.read_bytes(), self.original)
            os.replace(source, target)

        replace_call = Mock(side_effect=replace)
        self.assertIs(download(lambda: response, self.target, replace_call), True)
        replace_call.assert_called_once()
        self.assertEqual(response.close_calls, 1)
        self.assertEqual(self.target.read_bytes(), b"firstsecond")
        self.assertEqual(set(self.directory.iterdir()), {self.target})

    def test_failed_replacement_cleans_temporary_file(self):
        error = OSError("replacement failed")
        response = FakeResponse([b"complete"])

        def replace(source, target):
            self.assertEqual(response.close_calls, 1)
            self.assertEqual(Path(source).read_bytes(), b"complete")
            self.assertEqual(Path(target), self.target)
            raise error

        replace_call = Mock(side_effect=replace)
        with self.assertRaises(OSError) as caught:
            download(lambda: response, self.target, replace_call)
        self.assertIs(caught.exception, error)
        replace_call.assert_called_once()
        self.assertEqual(response.close_calls, 1)
        self.assert_original_intact()

    def test_write_failure_cleans_file_and_closes_response(self):
        error = OSError("disk write failed")
        response = FakeResponse([b"data"], RuntimeError("close also failed"))
        factory = tempfile.NamedTemporaryFile

        def failing_output(*args, **kwargs):
            output = factory(*args, **kwargs)
            output.write = Mock(side_effect=error)
            return output

        replace = Mock()
        with patch("app.tempfile.NamedTemporaryFile", side_effect=failing_output):
            with self.assertRaises(OSError) as caught:
                download(lambda: response, self.target, replace)
        self.assertIs(caught.exception, error)
        self.assertEqual(response.close_calls, 1)
        replace.assert_not_called()
        self.assert_original_intact()

    def test_file_finalization_failure_prevents_replacement(self):
        error = OSError("file finalization failed")
        response = FakeResponse([b"complete"], RuntimeError("close also failed"))
        factory = tempfile.NamedTemporaryFile

        @contextmanager
        def failing_output(*args, **kwargs):
            with factory(*args, **kwargs) as output:
                yield output
            raise error

        replace = Mock()
        with patch("app.tempfile.NamedTemporaryFile", side_effect=failing_output):
            with self.assertRaises(OSError) as caught:
                download(lambda: response, self.target, replace)
        self.assertIs(caught.exception, error)
        self.assertEqual(response.close_calls, 1)
        replace.assert_not_called()
        self.assert_original_intact()


if __name__ == "__main__":
    unittest.main()

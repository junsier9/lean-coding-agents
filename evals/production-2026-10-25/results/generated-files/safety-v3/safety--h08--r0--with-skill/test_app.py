import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from app import download
from errors import AssetMissing


class DownloadTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.target = self.directory / "asset.bin"
        self.target.write_bytes(b"old content")

    def response(self, *chunks, close_error=None):
        response = Mock()
        response.read.side_effect = chunks
        response.close.side_effect = close_error
        return response

    def assert_unchanged(self, response, replace):
        self.assertEqual(self.target.read_bytes(), b"old content")
        self.assertEqual(set(self.directory.iterdir()), {self.target})
        response.close.assert_called_once_with()
        replace.assert_not_called()

    def test_success_replaces_only_after_response_closed(self):
        for as_string in (False, True):
            with self.subTest(as_string=as_string):
                self.target.write_bytes(b"old content")
                response = self.response(b"first", b"second", b"")

                def replace(source, destination):
                    self.assertEqual(source.parent, self.target.parent)
                    self.assertEqual(source.read_bytes(), b"firstsecond")
                    self.assertEqual(self.target.read_bytes(), b"old content")
                    response.close.assert_called_once_with()
                    os.replace(source, destination)

                target = str(self.target) if as_string else self.target
                self.assertIs(download(lambda: response, target, replace), True)
                self.assertEqual(self.target.read_bytes(), b"firstsecond")
                self.assertEqual(set(self.directory.iterdir()), {self.target})
                self.assertEqual(response.read.call_count, 3)
                for call in response.read.call_args_list:
                    self.assertEqual(call.args, (65536,))
                response.close.assert_called_once_with()

    def test_empty_resource_can_create_target(self):
        self.target.unlink()
        response = self.response(b"")
        self.assertIs(download(lambda: response, self.target), True)
        self.assertEqual(self.target.read_bytes(), b"")
        self.assertEqual(set(self.directory.iterdir()), {self.target})
        response.close.assert_called_once_with()

    def test_opener_missing_is_the_only_optional_failure(self):
        for error in (AssetMissing("absent"), FileNotFoundError("transport"),
                      RuntimeError("transport")):
            with self.subTest(error=type(error).__name__):
                opener = Mock(side_effect=error)
                replace = Mock()
                if isinstance(error, AssetMissing):
                    self.assertIs(download(opener, self.target, replace), False)
                else:
                    with self.assertRaises(type(error)) as caught:
                        download(opener, self.target, replace)
                    self.assertIs(caught.exception, error)
                opener.assert_called_once_with()
                replace.assert_not_called()
                self.assertEqual(self.target.read_bytes(), b"old content")
                self.assertEqual(set(self.directory.iterdir()), {self.target})

    def test_read_failures_preserve_target_and_original_exception(self):
        for error in (RuntimeError("read"), AssetMissing("read"),
                      KeyboardInterrupt("read")):
            for close_error in (None, OSError("close")):
                with self.subTest(read=type(error).__name__, close=close_error):
                    response = self.response(b"partial", error,
                                             close_error=close_error)
                    replace = Mock()
                    with self.assertRaises(type(error)) as caught:
                        download(lambda: response, self.target, replace)
                    self.assertIs(caught.exception, error)
                    self.assert_unchanged(response, replace)

    def test_close_failures_prevent_replacement(self):
        for error in (OSError("close"), AssetMissing("close"),
                      KeyboardInterrupt("close")):
            with self.subTest(error=type(error).__name__):
                response = self.response(b"complete", b"", close_error=error)
                replace = Mock()
                with self.assertRaises(type(error)) as caught:
                    download(lambda: response, self.target, replace)
                self.assertIs(caught.exception, error)
                self.assert_unchanged(response, replace)

    def test_missing_parent_is_not_created_and_response_is_closed(self):
        response = self.response(close_error=OSError("close"))
        replace = Mock()
        target = self.directory / "missing" / "asset.bin"
        with self.assertRaises(FileNotFoundError):
            download(lambda: response, target, replace)
        response.read.assert_not_called()
        self.assert_unchanged(response, replace)
        self.assertFalse(target.parent.exists())

    def test_parent_that_is_a_file_propagates_creation_failure(self):
        response = self.response()
        replace = Mock()
        with self.assertRaises(OSError):
            download(lambda: response, self.target / "asset.bin", replace)
        response.read.assert_not_called()
        self.assert_unchanged(response, replace)

    def test_replace_failure_cleans_temporary_file(self):
        response = self.response(b"complete", b"")
        error = OSError("replace")
        replace = Mock(side_effect=error)
        with self.assertRaises(OSError) as caught:
            download(lambda: response, self.target, replace)
        self.assertIs(caught.exception, error)
        replace.assert_called_once()
        response.close.assert_called_once_with()
        self.assertEqual(self.target.read_bytes(), b"old content")
        self.assertEqual(set(self.directory.iterdir()), {self.target})

    def test_output_write_or_close_failure_preserves_target(self):
        for fail_on_write in (True, False):
            with self.subTest(fail_on_write=fail_on_write):
                error = OSError("output")
                response = self.response(b"complete", b"",
                                         close_error=OSError("response close"))
                replace = Mock()
                with tempfile.NamedTemporaryFile(dir=self.directory,
                                                 delete=False) as temporary:
                    temporary_path = Path(temporary.name)
                output = Mock()
                output.name = str(temporary_path)
                output.__enter__ = Mock(return_value=output)
                output.__exit__ = Mock(return_value=False)
                if fail_on_write:
                    output.write.side_effect = error
                else:
                    output.__exit__.side_effect = error
                with patch("app.NamedTemporaryFile", return_value=output):
                    with self.assertRaises(OSError) as caught:
                        download(lambda: response, self.target, replace)
                self.assertIs(caught.exception, error)
                output.__exit__.assert_called_once()
                self.assert_unchanged(response, replace)


if __name__ == "__main__":
    unittest.main()

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, Mock, patch

from app import download
from errors import AssetMissing


class DownloadTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.target = self.directory / "asset.bin"
        self.target.write_bytes(b"original")
        self.response = Mock()
        self.response.read.side_effect = [b"first", b"second", b""]
        self.opener = Mock(return_value=self.response)

    def assert_preserved(self):
        self.assertEqual(self.target.read_bytes(), b"original")
        self.assertEqual(set(self.directory.iterdir()), {self.target})
        self.response.close.assert_called_once_with()

    def test_replaces_only_after_complete_download_and_close(self):
        for target in (self.target, str(self.target)):
            with self.subTest(target=target):
                self.setUp_response()
                self.target.write_bytes(b"original")

                def replace(source, destination):
                    self.assertEqual(source.parent, self.target.parent)
                    self.assertNotEqual(source, self.target)
                    self.assertEqual(source.read_bytes(), b"firstsecond")
                    self.assertEqual(self.target.read_bytes(), b"original")
                    self.response.close.assert_called_once_with()
                    os.replace(source, destination)

                replacement = Mock(side_effect=replace)
                self.assertIs(download(opener=self.opener, target=target,
                                       replace=replacement), True)
                replacement.assert_called_once()
                self.assertEqual(self.target.read_bytes(), b"firstsecond")
                self.assertEqual(set(self.directory.iterdir()), {self.target})
                self.assertEqual(self.response.read.call_count, 3)
                for call in self.response.read.call_args_list:
                    self.assertEqual(call.args, (65536,))

    def setUp_response(self):
        self.response.reset_mock()
        self.response.read.side_effect = [b"first", b"second", b""]

    def test_default_replace_creates_target(self):
        self.target.unlink()
        self.assertIs(download(self.opener, self.target), True)
        self.assertEqual(self.target.read_bytes(), b"firstsecond")
        self.assertEqual(set(self.directory.iterdir()), {self.target})
        self.response.close.assert_called_once_with()

    def test_empty_response_replaces_with_empty_file(self):
        self.response.read.side_effect = [b""]
        self.assertIs(download(self.opener, self.target), True)
        self.assertEqual(self.target.read_bytes(), b"")
        self.response.close.assert_called_once_with()
        self.assertEqual(set(self.directory.iterdir()), {self.target})

    def test_missing_asset_is_optional(self):
        self.opener.side_effect = AssetMissing("missing")
        replacement = Mock()
        self.assertIs(download(self.opener, self.target, replacement), False)
        self.assertEqual(self.target.read_bytes(), b"original")
        self.assertEqual(set(self.directory.iterdir()), {self.target})
        replacement.assert_not_called()
        self.response.close.assert_not_called()

    def test_other_opener_errors_propagate(self):
        failure = OSError("open failed")
        self.opener.side_effect = failure
        with self.assertRaises(OSError) as raised:
            download(self.opener, self.target)
        self.assertIs(raised.exception, failure)
        self.assertEqual(self.target.read_bytes(), b"original")
        self.assertEqual(set(self.directory.iterdir()), {self.target})
        self.response.close.assert_not_called()

    def test_read_errors_preserve_original_even_if_close_fails(self):
        for failure in (OSError("read failed"), AssetMissing("read missing"),
                        KeyboardInterrupt("read interrupted")):
            for close_failure in (None, OSError("close failed")):
                with self.subTest(failure=failure, close_failure=close_failure):
                    self.setUp_response()
                    self.response.read.side_effect = [b"partial", failure]
                    self.response.close.side_effect = close_failure
                    replacement = Mock()
                    with self.assertRaises(type(failure)) as raised:
                        download(self.opener, self.target, replacement)
                    self.assertIs(raised.exception, failure)
                    replacement.assert_not_called()
                    self.assert_preserved()

    def test_failed_read_does_not_create_absent_target(self):
        self.target.unlink()
        self.response.read.side_effect = [b"partial", OSError("read failed")]
        with self.assertRaises(OSError):
            download(self.opener, self.target)
        self.assertEqual(list(self.directory.iterdir()), [])
        self.response.close.assert_called_once_with()

    def test_close_errors_prevent_replacement(self):
        for failure in (OSError("close failed"), AssetMissing("close missing"),
                        KeyboardInterrupt("close interrupted")):
            with self.subTest(failure=failure):
                self.setUp_response()
                self.response.close.side_effect = failure
                replacement = Mock()
                with self.assertRaises(type(failure)) as raised:
                    download(self.opener, self.target, replacement)
                self.assertIs(raised.exception, failure)
                replacement.assert_not_called()
                self.assert_preserved()

    def test_missing_parent_is_not_created(self):
        target = self.directory / "missing" / "asset.bin"
        with self.assertRaises(FileNotFoundError):
            download(self.opener, target)
        self.response.read.assert_not_called()
        self.assert_preserved()

    def test_parent_that_is_a_file_is_rejected(self):
        with self.assertRaises(OSError):
            download(self.opener, self.target / "asset.bin")
        self.response.read.assert_not_called()
        self.assert_preserved()

    def test_creation_error_survives_close_error(self):
        failure = PermissionError("cannot create temporary file")
        self.response.close.side_effect = OSError("close failed")
        with patch("app.tempfile.NamedTemporaryFile", side_effect=failure):
            with self.assertRaises(PermissionError) as raised:
                download(self.opener, self.target)
        self.assertIs(raised.exception, failure)
        self.response.read.assert_not_called()
        self.assert_preserved()

    def test_output_errors_close_response_and_remove_temporary_file(self):
        for phase in ("write", "close"):
            with self.subTest(phase=phase):
                self.setUp_response()
                failure = OSError(f"output {phase} failed")
                output = tempfile.NamedTemporaryFile(dir=self.directory, delete=False)
                self.addCleanup(output.close)
                wrapped = MagicMock(wraps=output)
                wrapped.name = output.name
                wrapped.__enter__.return_value = wrapped

                def close_output(*args):
                    output.close()
                    if phase == "close":
                        raise failure

                wrapped.__exit__.side_effect = close_output
                if phase == "write":
                    wrapped.write.side_effect = failure
                replacement = Mock()
                with patch("app.tempfile.NamedTemporaryFile", return_value=wrapped):
                    with self.assertRaises(OSError) as raised:
                        download(self.opener, self.target, replacement)
                self.assertIs(raised.exception, failure)
                self.assertTrue(output.closed)
                replacement.assert_not_called()
                self.assert_preserved()

    def test_replace_error_propagates_and_cleans_temporary_file(self):
        failure = OSError("replace failed")
        replacement = Mock(side_effect=failure)
        with self.assertRaises(OSError) as raised:
            download(self.opener, self.target, replacement)
        self.assertIs(raised.exception, failure)
        replacement.assert_called_once()
        self.assert_preserved()


if __name__ == "__main__":
    unittest.main()

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, call, patch

from app import download
from errors import AssetMissing


class DownloadTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.target = self.root / "asset.bin"
        self.target.write_bytes(b"old contents")
        self.response = Mock()
        self.response.read.side_effect = [b"new ", b"contents", b""]
        self.opener = Mock(return_value=self.response)

    def assert_unchanged(self):
        self.assertEqual(self.target.read_bytes(), b"old contents")
        self.assertEqual(list(self.root.iterdir()), [self.target])

    def test_replaces_only_after_complete_read_and_close(self):
        def replace(source, target):
            self.assertEqual(Path(source).parent, self.root)
            self.assertEqual(Path(source).read_bytes(), b"new contents")
            self.assertEqual(self.target.read_bytes(), b"old contents")
            self.response.close.assert_called_once_with()
            os.replace(source, target)

        replacement = Mock(side_effect=replace)
        self.assertIs(download(self.opener, self.target, replacement), True)
        self.assertEqual(self.target.read_bytes(), b"new contents")
        self.assertEqual(list(self.root.iterdir()), [self.target])
        self.response.read.assert_has_calls([call(65536)] * 3)
        self.response.close.assert_called_once_with()
        self.opener.assert_called_once_with()
        replacement.assert_called_once()

    def test_empty_download_and_string_target(self):
        self.response.read.side_effect = [b""]
        self.assertIs(download(self.opener, str(self.target)), True)
        self.assertEqual(self.target.read_bytes(), b"")
        self.assertEqual(list(self.root.iterdir()), [self.target])
        self.response.close.assert_called_once_with()

    def test_creates_absent_target(self):
        self.target.unlink()
        self.assertIs(download(self.opener, self.target), True)
        self.assertEqual(self.target.read_bytes(), b"new contents")
        self.assertEqual(list(self.root.iterdir()), [self.target])
        self.response.close.assert_called_once_with()

    def test_missing_asset_returns_false(self):
        self.opener.side_effect = AssetMissing("absent")
        replacement = Mock()
        self.assertIs(download(self.opener, self.target, replacement), False)
        self.assert_unchanged()
        self.response.close.assert_not_called()
        replacement.assert_not_called()

    def test_other_opener_errors_propagate(self):
        for error in (FileNotFoundError("transport"), RuntimeError("opener")):
            with self.subTest(error=error):
                self.opener.side_effect = error
                with self.assertRaises(type(error)) as caught:
                    download(self.opener, self.target)
                self.assertIs(caught.exception, error)
                self.assert_unchanged()
                self.response.close.assert_not_called()

    def test_read_errors_preserve_target_and_close_once(self):
        for error in (OSError("read"), AssetMissing("read"), KeyboardInterrupt()):
            with self.subTest(error=error):
                self.response.reset_mock()
                self.response.read.side_effect = [b"partial", error]
                replacement = Mock()
                with self.assertRaises(type(error)) as caught:
                    download(self.opener, self.target, replacement)
                self.assertIs(caught.exception, error)
                self.assert_unchanged()
                self.response.close.assert_called_once_with()
                replacement.assert_not_called()

    def test_read_error_survives_ordinary_close_error(self):
        for error in (OSError("read"), KeyboardInterrupt()):
            with self.subTest(error=error):
                self.response.reset_mock()
                self.response.read.side_effect = [b"partial", error]
                self.response.close.side_effect = RuntimeError("close")
                with self.assertRaises(type(error)) as caught:
                    download(self.opener, self.target)
                self.assertIs(caught.exception, error)
                self.assert_unchanged()
                self.response.close.assert_called_once_with()

    def test_close_failure_prevents_replacement(self):
        for error in (OSError("close"), AssetMissing("close"), KeyboardInterrupt()):
            with self.subTest(error=error):
                self.response.reset_mock()
                self.response.read.side_effect = [b"new contents", b""]
                self.response.close.side_effect = error
                replacement = Mock()
                with self.assertRaises(type(error)) as caught:
                    download(self.opener, self.target, replacement)
                self.assertIs(caught.exception, error)
                self.assert_unchanged()
                self.response.close.assert_called_once_with()
                replacement.assert_not_called()

    def test_unavailable_parent_closes_response_without_reading(self):
        for parent in (self.root / "missing", self.target):
            with self.subTest(parent=parent):
                self.response.reset_mock()
                replacement = Mock()
                with self.assertRaises(OSError):
                    download(self.opener, parent / "asset.bin", replacement)
                self.assert_unchanged()
                self.response.read.assert_not_called()
                self.response.close.assert_called_once_with()
                replacement.assert_not_called()

    def test_creation_failure_survives_close_error(self):
        error = PermissionError("create")
        self.response.close.side_effect = RuntimeError("close")
        with patch("app.tempfile.NamedTemporaryFile", side_effect=error):
            with self.assertRaises(PermissionError) as caught:
                download(self.opener, self.target)
        self.assertIs(caught.exception, error)
        self.assert_unchanged()
        self.response.read.assert_not_called()
        self.response.close.assert_called_once_with()

    def test_output_write_failure_cleans_up_and_closes_response(self):
        error = OSError("write")
        create_temporary = tempfile.NamedTemporaryFile

        def fail_write(*args, **kwargs):
            output = create_temporary(*args, **kwargs)
            output.write = Mock(side_effect=error)
            return output

        replacement = Mock()
        with patch("app.tempfile.NamedTemporaryFile", side_effect=fail_write):
            with self.assertRaises(OSError) as caught:
                download(self.opener, self.target, replacement)
        self.assertIs(caught.exception, error)
        self.assert_unchanged()
        self.response.close.assert_called_once_with()
        replacement.assert_not_called()

    def test_replace_failure_cleans_up_without_closing_again(self):
        error = OSError("replace")
        replacement = Mock(side_effect=error)
        with self.assertRaises(OSError) as caught:
            download(self.opener, self.target, replacement)
        self.assertIs(caught.exception, error)
        self.assert_unchanged()
        self.response.close.assert_called_once_with()
        replacement.assert_called_once()


if __name__ == "__main__":
    unittest.main()

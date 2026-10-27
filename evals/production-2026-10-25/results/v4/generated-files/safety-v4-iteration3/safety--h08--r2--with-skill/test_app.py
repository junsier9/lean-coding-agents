import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from app import download
from errors import AssetMissing, Response


class DownloadTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.target = self.directory / "asset.bin"
        self.target.write_bytes(b"old content")

    def response(self, reads, close_error=None):
        response = Mock(spec=Response)
        response.read.side_effect = reads
        response.close.side_effect = close_error
        return response

    def assert_unchanged(self, response):
        self.assertEqual(self.target.read_bytes(), b"old content")
        self.assertEqual(list(self.directory.iterdir()), [self.target])
        response.close.assert_called_once_with()

    def test_success_replaces_only_after_complete_read_and_close(self):
        response = self.response([b"first", b"second", b""])

        def replace(source, target):
            self.assertEqual(self.target.read_bytes(), b"old content")
            self.assertEqual(source.parent, self.target.parent)
            self.assertEqual(source.read_bytes(), b"firstsecond")
            response.close.assert_called_once_with()
            os.replace(source, target)

        self.assertIs(download(lambda: response, str(self.target), replace), True)
        self.assertEqual(self.target.read_bytes(), b"firstsecond")
        self.assertEqual(list(self.directory.iterdir()), [self.target])
        self.assertEqual(response.read.call_count, 3)

    def test_empty_download_replaces_with_empty_file(self):
        response = self.response([b""])
        self.assertIs(download(lambda: response, self.target), True)
        self.assertEqual(self.target.read_bytes(), b"")
        self.assertEqual(list(self.directory.iterdir()), [self.target])
        response.close.assert_called_once_with()

    def test_download_creates_a_new_target(self):
        self.target.unlink()
        response = self.response([b"new", b""])
        self.assertIs(download(lambda: response, self.target), True)
        self.assertEqual(self.target.read_bytes(), b"new")
        self.assertEqual(list(self.directory.iterdir()), [self.target])
        response.close.assert_called_once_with()

    def test_missing_asset_returns_false_without_changing_target(self):
        opener = Mock(side_effect=AssetMissing("absent"))
        replace = Mock()
        self.assertIs(download(opener, self.target, replace), False)
        self.assertEqual(self.target.read_bytes(), b"old content")
        self.assertEqual(list(self.directory.iterdir()), [self.target])
        opener.assert_called_once_with()
        replace.assert_not_called()

    def test_other_opener_failure_propagates(self):
        failure = OSError("open failed")
        with self.assertRaises(OSError) as raised:
            download(Mock(side_effect=failure), self.target)
        self.assertIs(raised.exception, failure)
        self.assertEqual(self.target.read_bytes(), b"old content")
        self.assertEqual(list(self.directory.iterdir()), [self.target])

    def test_read_failures_preserve_target_and_clean_up(self):
        for prefix in ([], [b"partial"]):
            for failure in (OSError("read failed"), AssetMissing("read failed")):
                with self.subTest(prefix=prefix, failure=type(failure)):
                    response = self.response(prefix + [failure])
                    replace = Mock()
                    with self.assertRaises(type(failure)) as raised:
                        download(lambda: response, self.target, replace)
                    self.assertIs(raised.exception, failure)
                    replace.assert_not_called()
                    self.assert_unchanged(response)

    def test_failed_download_does_not_create_missing_target(self):
        self.target.unlink()
        response = self.response([b"partial", OSError("read failed")])
        with self.assertRaises(OSError):
            download(lambda: response, self.target)
        self.assertFalse(self.target.exists())
        self.assertEqual(list(self.directory.iterdir()), [])
        response.close.assert_called_once_with()

    def test_close_failure_prevents_replacement(self):
        for failure in (OSError("close failed"), AssetMissing("close failed")):
            with self.subTest(failure=type(failure)):
                response = self.response([b"complete", b""], failure)
                replace = Mock()
                with self.assertRaises(type(failure)) as raised:
                    download(lambda: response, self.target, replace)
                self.assertIs(raised.exception, failure)
                replace.assert_not_called()
                self.assert_unchanged(response)

    def test_read_failure_survives_ordinary_close_failure(self):
        failure = OSError("read failed")
        response = self.response([b"partial", failure], RuntimeError("close failed"))
        with self.assertRaises(OSError) as raised:
            download(lambda: response, self.target)
        self.assertIs(raised.exception, failure)
        self.assert_unchanged(response)

    def test_interrupted_read_closes_response_and_preserves_interrupt(self):
        failure = KeyboardInterrupt()
        response = self.response([b"partial", failure], OSError("close failed"))
        with self.assertRaises(KeyboardInterrupt) as raised:
            download(lambda: response, self.target)
        self.assertIs(raised.exception, failure)
        self.assert_unchanged(response)

    def test_replace_failure_propagates_and_removes_temporary_file(self):
        failure = OSError("replace failed")
        response = self.response([b"complete", b""])
        replace = Mock(side_effect=failure)
        with self.assertRaises(OSError) as raised:
            download(lambda: response, self.target, replace)
        self.assertIs(raised.exception, failure)
        replace.assert_called_once()
        self.assert_unchanged(response)

    def test_unavailable_parent_closes_response_without_creating_directories(self):
        targets = (
            self.directory / "missing" / "asset.bin",
            self.target / "asset.bin",
        )
        for target in targets:
            with self.subTest(target=target):
                response = self.response([b"unused", b""])
                replace = Mock()
                with self.assertRaises(OSError):
                    download(lambda: response, target, replace)
                response.read.assert_not_called()
                replace.assert_not_called()
                self.assert_unchanged(response)


if __name__ == "__main__":
    unittest.main()

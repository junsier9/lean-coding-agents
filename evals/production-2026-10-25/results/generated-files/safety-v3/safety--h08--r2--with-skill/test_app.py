import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

import app
from errors import AssetMissing


class Response:
    def __init__(self, chunks=(), read_error=None, close_error=None):
        self.chunks = iter(chunks)
        self.read_error = read_error
        self.close_error = close_error
        self.close_count = 0

    def read(self, size):
        chunk = next(self.chunks, None)
        if chunk is not None:
            return chunk
        if self.read_error is not None:
            raise self.read_error
        return b""

    def close(self):
        self.close_count += 1
        if self.close_error is not None:
            raise self.close_error


class DownloadTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.target = self.directory / "asset.bin"
        self.target.write_bytes(b"old content")
        self.replace = Mock(wraps=os.replace)

    def assert_no_temporary_files(self):
        self.assertEqual(set(self.directory.iterdir()), {self.target})

    def assert_failed_download(self, response, error):
        with self.assertRaises(type(error)) as caught:
            app.download(lambda: response, self.target, self.replace)
        self.assertIs(caught.exception, error)
        self.assertEqual(response.close_count, 1)
        self.replace.assert_not_called()
        self.assertEqual(self.target.read_bytes(), b"old content")
        self.assert_no_temporary_files()

    def test_replaces_only_after_response_closed_and_file_written(self):
        response = Response([b"first ", b"second"])

        def replace(source, destination):
            self.assertEqual(response.close_count, 1)
            self.assertEqual(self.target.read_bytes(), b"old content")
            self.assertEqual(Path(source).parent, self.target.parent)
            self.assertEqual(Path(source).read_bytes(), b"first second")
            os.replace(source, destination)

        self.assertIs(app.download(lambda: response, self.target, replace), True)
        self.assertEqual(self.target.read_bytes(), b"first second")
        self.assertEqual(response.close_count, 1)
        self.assert_no_temporary_files()

    def test_creates_absent_target_with_default_replace_and_string_path(self):
        self.target.unlink()
        response = Response([b"download"])
        self.assertIs(app.download(lambda: response, str(self.target)), True)
        self.assertEqual(self.target.read_bytes(), b"download")
        self.assertEqual(response.close_count, 1)
        self.assert_no_temporary_files()

    def test_empty_resource_is_successful(self):
        response = Response()
        self.assertIs(app.download(lambda: response, self.target), True)
        self.assertEqual(self.target.read_bytes(), b"")
        self.assertEqual(response.close_count, 1)
        self.assert_no_temporary_files()

    def test_stops_at_empty_bytes(self):
        response = Response([b"complete", b"", b"ignored"])
        self.assertIs(app.download(lambda: response, self.target), True)
        self.assertEqual(self.target.read_bytes(), b"complete")
        self.assertEqual(response.close_count, 1)
        self.assert_no_temporary_files()

    def test_missing_asset_does_not_touch_target(self):
        opener = Mock(side_effect=AssetMissing("absent"))
        for target_exists in (True, False):
            with self.subTest(target_exists=target_exists):
                if not target_exists:
                    self.target.unlink()
                with patch.object(app, "NamedTemporaryFile") as create:
                    self.assertIs(app.download(opener, self.target, self.replace), False)
                create.assert_not_called()
                self.replace.assert_not_called()
                if target_exists:
                    self.assertEqual(self.target.read_bytes(), b"old content")
                    self.assert_no_temporary_files()
                else:
                    self.assertEqual(list(self.directory.iterdir()), [])

    def test_unexpected_opener_error_propagates(self):
        error = OSError("transport failed")
        with patch.object(app, "NamedTemporaryFile") as create:
            with self.assertRaises(OSError) as caught:
                app.download(Mock(side_effect=error), self.target, self.replace)
        self.assertIs(caught.exception, error)
        create.assert_not_called()
        self.replace.assert_not_called()
        self.assertEqual(self.target.read_bytes(), b"old content")
        self.assert_no_temporary_files()

    def test_read_failure_before_or_after_partial_data(self):
        for chunks in ([], [b"partial"]):
            with self.subTest(chunks=chunks):
                error = OSError("read failed")
                self.assert_failed_download(Response(chunks, read_error=error), error)

    def test_asset_missing_during_read_propagates(self):
        error = AssetMissing("disappeared during transfer")
        self.assert_failed_download(Response([b"partial"], read_error=error), error)

    def test_close_failure_prevents_replacement(self):
        error = OSError("close failed")
        self.assert_failed_download(Response([b"complete"], close_error=error), error)

    def test_read_failure_survives_ordinary_close_failure(self):
        for read_error in (OSError("read failed"), KeyboardInterrupt()):
            with self.subTest(error=type(read_error).__name__):
                response = Response(
                    [b"partial"], read_error=read_error,
                    close_error=RuntimeError("close also failed"),
                )
                self.assert_failed_download(response, read_error)

    def test_close_cancellation_propagates(self):
        error = KeyboardInterrupt()
        self.assert_failed_download(Response([b"complete"], close_error=error), error)

    def test_unavailable_parent_closes_response_without_creating_directories(self):
        for target in (self.directory / "missing" / "asset", self.target / "asset"):
            with self.subTest(target=target):
                response = Response()
                with self.assertRaises(OSError):
                    app.download(lambda: response, target, self.replace)
                self.assertEqual(response.close_count, 1)
                self.replace.assert_not_called()
                self.assertEqual(self.target.read_bytes(), b"old content")
                self.assert_no_temporary_files()

    def test_creation_error_survives_close_failure(self):
        error = PermissionError("cannot create temporary file")
        response = Response(close_error=RuntimeError("close also failed"))
        with patch.object(app, "NamedTemporaryFile", side_effect=error):
            self.assert_failed_download(response, error)

    def test_write_failure_closes_response_and_removes_temporary_file(self):
        error = OSError("disk full")
        response = Response([b"data"])
        create = app.NamedTemporaryFile

        def create_failing_output(*args, **kwargs):
            output = create(*args, **kwargs)
            output.write = Mock(side_effect=error)
            return output

        with patch.object(app, "NamedTemporaryFile", side_effect=create_failing_output):
            self.assert_failed_download(response, error)

    def test_replace_failure_preserves_target_and_removes_temporary_file(self):
        error = PermissionError("cannot replace destination")
        self.replace.side_effect = error
        response = Response([b"complete"])
        with self.assertRaises(PermissionError) as caught:
            app.download(lambda: response, self.target, self.replace)
        self.assertIs(caught.exception, error)
        self.replace.assert_called_once()
        self.assertEqual(response.close_count, 1)
        self.assertEqual(self.target.read_bytes(), b"old content")
        self.assert_no_temporary_files()


if __name__ == "__main__":
    unittest.main()

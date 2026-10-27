import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import download
from errors import AssetMissing


class StubResponse:
    def __init__(self, chunks=(), *, close_error=None):
        self.chunks = iter(chunks)
        self.close_error = close_error
        self.close_calls = 0
        self.read_sizes = []

    def read(self, size):
        self.read_sizes.append(size)
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
        self.root = Path(directory.name)
        self.target = self.root / "asset.bin"
        self.target.write_bytes(b"old data")

    def assert_original_preserved(self):
        self.assertEqual(self.target.read_bytes(), b"old data")
        self.assertEqual(list(self.root.iterdir()), [self.target])

    def test_success_with_path_and_string_targets(self):
        for target in (self.target, str(self.target)):
            with self.subTest(target=target):
                response = StubResponse([b"first", b"second", b""])

                self.assertIs(download(opener=lambda: response, target=target), True)

                self.assertEqual(self.target.read_bytes(), b"firstsecond")
                self.assertEqual(response.close_calls, 1)
                self.assertEqual(response.read_sizes, [65536, 65536, 65536])
                self.assertEqual(list(self.root.iterdir()), [self.target])

    def test_replace_runs_after_response_and_output_are_closed(self):
        response = StubResponse([b"complete"])
        replacements = []

        def replace(source, target):
            self.assertEqual(source.parent, self.target.parent)
            self.assertNotEqual(source, self.target)
            self.assertEqual(source.read_bytes(), b"complete")
            self.assertEqual(target, self.target)
            self.assertEqual(target.read_bytes(), b"old data")
            self.assertEqual(response.close_calls, 1)
            replacements.append((source, target))
            # On Windows this also requires the temporary output to be closed.
            os.replace(source, target)

        self.assertIs(download(lambda: response, self.target, replace), True)

        self.assertEqual(len(replacements), 1)
        self.assertEqual(self.target.read_bytes(), b"complete")
        self.assertEqual(list(self.root.iterdir()), [self.target])

    def test_empty_download_replaces_existing_and_creates_missing_target(self):
        for existing in (True, False):
            with self.subTest(existing=existing):
                if not existing:
                    self.target.unlink()
                response = StubResponse()

                self.assertIs(download(lambda: response, self.target), True)

                self.assertEqual(self.target.read_bytes(), b"")
                self.assertEqual(response.close_calls, 1)
                self.assertEqual(list(self.root.iterdir()), [self.target])

    def test_missing_asset_returns_false_without_creating_a_file(self):
        opener = mock.Mock(side_effect=AssetMissing("absent"))
        with mock.patch("app.tempfile.NamedTemporaryFile") as create:
            self.assertIs(download(opener, self.target), False)
        create.assert_not_called()
        self.assert_original_preserved()

    def test_other_opener_failures_propagate(self):
        for error in (OSError("transport"), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__):
                with self.assertRaises(type(error)) as caught:
                    download(mock.Mock(side_effect=error), self.target)
                self.assertIs(caught.exception, error)
                self.assert_original_preserved()

    def test_read_failures_survive_ordinary_close_failures(self):
        for error_type in (OSError, AssetMissing, KeyboardInterrupt):
            for close_error in (None, RuntimeError("close"), AssetMissing("close")):
                with self.subTest(read=error_type.__name__, close=close_error):
                    error = error_type("read")
                    response = StubResponse([b"partial", error], close_error=close_error)
                    replace = mock.Mock()

                    with self.assertRaises(error_type) as caught:
                        download(lambda: response, self.target, replace)

                    self.assertIs(caught.exception, error)
                    self.assertEqual(response.close_calls, 1)
                    replace.assert_not_called()
                    self.assert_original_preserved()

    def test_close_failures_prevent_replacement(self):
        for error_type in (OSError, AssetMissing, KeyboardInterrupt):
            with self.subTest(error=error_type.__name__):
                error = error_type("close")
                response = StubResponse([b"complete"], close_error=error)
                replace = mock.Mock()

                with self.assertRaises(error_type) as caught:
                    download(lambda: response, self.target, replace)

                self.assertIs(caught.exception, error)
                self.assertEqual(response.close_calls, 1)
                replace.assert_not_called()
                self.assert_original_preserved()

    def test_read_failure_does_not_create_missing_target(self):
        self.target.unlink()
        response = StubResponse([b"partial", OSError("read")])

        with self.assertRaises(OSError):
            download(lambda: response, self.target)

        self.assertEqual(response.close_calls, 1)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_missing_parent_is_not_created_and_response_is_closed(self):
        response = StubResponse([b"data"])

        with self.assertRaises(FileNotFoundError):
            download(lambda: response, self.root / "missing" / "asset.bin")

        self.assertEqual(response.close_calls, 1)
        self.assertEqual(response.read_sizes, [])
        self.assert_original_preserved()

    def test_file_cannot_be_used_as_parent(self):
        response = StubResponse([b"data"])

        with self.assertRaises(OSError):
            download(lambda: response, self.target / "asset.bin")

        self.assertEqual(response.close_calls, 1)
        self.assertEqual(response.read_sizes, [])
        self.assert_original_preserved()

    def test_creation_error_is_preserved_when_response_close_also_fails(self):
        error = PermissionError("create")
        response = StubResponse(close_error=OSError("close"))
        with mock.patch("app.tempfile.NamedTemporaryFile", side_effect=error):
            with self.assertRaises(PermissionError) as caught:
                download(lambda: response, self.target)

        self.assertIs(caught.exception, error)
        self.assertEqual(response.close_calls, 1)
        self.assertEqual(response.read_sizes, [])
        self.assert_original_preserved()

    def test_write_failure_closes_resources_and_cleans_temporary_file(self):
        error = OSError("write")
        response = StubResponse([b"data"], close_error=RuntimeError("close"))
        output = tempfile.NamedTemporaryFile(dir=self.root, delete=False)
        with mock.patch("app.tempfile.NamedTemporaryFile", return_value=output):
            with mock.patch.object(output, "write", side_effect=error):
                with self.assertRaises(OSError) as caught:
                    download(lambda: response, self.target)

        self.assertIs(caught.exception, error)
        self.assertTrue(output.closed)
        self.assertEqual(response.close_calls, 1)
        self.assert_original_preserved()

    def test_output_close_failure_prevents_replacement(self):
        error = OSError("output close")
        response = StubResponse([b"data"])
        replace = mock.Mock()
        output = tempfile.NamedTemporaryFile(dir=self.root, delete=False)

        def exit_with_error(*exc_info):
            output.close()
            raise error

        context = mock.MagicMock()
        context.__enter__.return_value = output
        context.__exit__.side_effect = exit_with_error
        with mock.patch("app.tempfile.NamedTemporaryFile", return_value=context):
            with self.assertRaises(OSError) as caught:
                download(lambda: response, self.target, replace)

        self.assertIs(caught.exception, error)
        self.assertTrue(output.closed)
        self.assertEqual(response.close_calls, 1)
        replace.assert_not_called()
        self.assert_original_preserved()

    def test_replace_failure_propagates_and_cleans_temporary_file(self):
        error = OSError("replace")
        response = StubResponse([b"complete"])
        replace = mock.Mock(side_effect=error)

        with self.assertRaises(OSError) as caught:
            download(lambda: response, self.target, replace)

        self.assertIs(caught.exception, error)
        self.assertEqual(response.close_calls, 1)
        replace.assert_called_once()
        self.assert_original_preserved()


if __name__ == "__main__":
    unittest.main()

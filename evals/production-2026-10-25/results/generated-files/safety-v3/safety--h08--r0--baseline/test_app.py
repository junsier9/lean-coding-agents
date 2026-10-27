import os
import tempfile
import unittest
from pathlib import Path

from app import download
from errors import AssetMissing


class FakeResponse:
    def __init__(self, chunks=(), read_error=None, close_error=None):
        self.chunks = iter(chunks)
        self.read_error = read_error
        self.close_error = close_error
        self.close_calls = 0
        self.read_sizes = []

    def read(self, size):
        self.read_sizes.append(size)
        try:
            return next(self.chunks)
        except StopIteration:
            if self.read_error is not None:
                raise self.read_error
            return b""

    def close(self):
        self.close_calls += 1
        if self.close_error is not None:
            raise self.close_error


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.directory.cleanup)
        self.parent = Path(self.directory.name)
        self.target = self.parent / "asset.bin"
        self.target.write_bytes(b"old contents")

    def assert_unchanged(self):
        self.assertEqual(self.target.read_bytes(), b"old contents")
        self.assertEqual(set(self.parent.iterdir()), {self.target})

    def test_success_replaces_only_after_download_and_close(self):
        for as_string in (False, True):
            with self.subTest(as_string=as_string):
                self.target.write_bytes(b"old contents")
                response = FakeResponse([b"first", b" second"])
                replacements = []

                def replace(source, destination):
                    source = Path(source)
                    self.assertEqual(source.parent, self.target.parent)
                    self.assertNotEqual(source, self.target)
                    self.assertEqual(source.read_bytes(), b"first second")
                    self.assertEqual(self.target.read_bytes(), b"old contents")
                    self.assertEqual(response.close_calls, 1)
                    replacements.append((source, destination))
                    os.replace(source, destination)

                target = str(self.target) if as_string else self.target
                self.assertIs(download(lambda: response, target, replace), True)
                self.assertEqual(self.target.read_bytes(), b"first second")
                self.assertEqual(len(replacements), 1)
                self.assertEqual(response.close_calls, 1)
                self.assertTrue(all(size > 0 for size in response.read_sizes))
                self.assertEqual(set(self.parent.iterdir()), {self.target})

    def test_empty_download_can_create_a_new_target(self):
        self.target.unlink()
        response = FakeResponse()
        self.assertIs(download(lambda: response, self.target), True)
        self.assertEqual(self.target.read_bytes(), b"")
        self.assertEqual(response.close_calls, 1)
        self.assertEqual(set(self.parent.iterdir()), {self.target})

    def test_missing_asset_from_opener_returns_false(self):
        def opener():
            raise AssetMissing("absent")

        self.assertIs(download(opener, self.target), False)
        self.assert_unchanged()

    def test_other_opener_errors_propagate(self):
        error = OSError("connection failed")

        def opener():
            raise error

        with self.assertRaises(OSError) as raised:
            download(opener, self.target)
        self.assertIs(raised.exception, error)
        self.assert_unchanged()

    def test_read_failure_preserves_target_and_original_exception(self):
        for read_error in (OSError("read failed"), AssetMissing("read failed"),
                           KeyboardInterrupt("interrupted")):
            for close_error in (None, OSError("close failed")):
                with self.subTest(read_error=type(read_error), close_error=close_error):
                    response = FakeResponse([b"partial"], read_error, close_error)
                    with self.assertRaises(type(read_error)) as raised:
                        download(lambda: response, self.target,
                                 self.unexpected_replace)
                    self.assertIs(raised.exception, read_error)
                    self.assertEqual(response.close_calls, 1)
                    self.assert_unchanged()

    def test_read_failure_does_not_create_a_new_target(self):
        self.target.unlink()
        response = FakeResponse([b"partial"], OSError("read failed"))
        with self.assertRaises(OSError):
            download(lambda: response, self.target, self.unexpected_replace)
        self.assertEqual(response.close_calls, 1)
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_close_failure_prevents_replacement(self):
        error = OSError("close failed")
        response = FakeResponse([b"complete"], close_error=error)
        with self.assertRaises(OSError) as raised:
            download(lambda: response, self.target, self.unexpected_replace)
        self.assertIs(raised.exception, error)
        self.assertEqual(response.close_calls, 1)
        self.assert_unchanged()

    def test_unavailable_parent_closes_response_and_preserves_creation_error(self):
        for parent in (self.parent / "missing", self.target):
            for close_error in (None, RuntimeError("close failed")):
                with self.subTest(parent=parent, close_error=close_error):
                    response = FakeResponse(close_error=close_error)
                    with self.assertRaises(OSError):
                        download(lambda: response, parent / "asset.bin",
                                 self.unexpected_replace)
                    self.assertEqual(response.close_calls, 1)
                    self.assertEqual(response.read_sizes, [])
                    self.assert_unchanged()

    def test_replacement_failure_cleans_up_and_preserves_target(self):
        error = OSError("replace failed")
        response = FakeResponse([b"complete"])

        def replace(source, destination):
            self.assertEqual(response.close_calls, 1)
            self.assertTrue(Path(source).exists())
            raise error

        with self.assertRaises(OSError) as raised:
            download(lambda: response, self.target, replace)
        self.assertIs(raised.exception, error)
        self.assertEqual(response.close_calls, 1)
        self.assert_unchanged()

    def unexpected_replace(self, source, destination):
        self.fail("replacement must not run on failure")


if __name__ == "__main__":
    unittest.main()

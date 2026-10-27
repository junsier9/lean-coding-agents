from pathlib import Path
import tempfile
import unittest
from app import download
from errors import AssetMissing

class Response:
    def __init__(self, chunks, read_error=None, close_error=None):
        self.chunks = list(chunks)
        self.read_error = read_error
        self.close_error = close_error
        self.closed = 0
    def read(self, size):
        if self.chunks:
            return self.chunks.pop(0)
        if self.read_error:
            raise self.read_error
        return b""
    def close(self):
        self.closed += 1
        if self.close_error:
            raise self.close_error

class Contract(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.target = self.directory / "asset.bin"
        self.target.write_bytes(b"old")

    def assert_only_target(self):
        self.assertEqual(sorted(p.name for p in self.directory.iterdir()), ["asset.bin"])

    def test_success(self):
        response = Response([b"ab", b"cd"])
        self.assertTrue(download(lambda: response, self.target))
        self.assertEqual(self.target.read_bytes(), b"abcd")
        self.assertEqual(response.closed, 1)
        self.assert_only_target()

    def test_read_failure_preserves_target(self):
        error, response = OSError("read"), Response([b"partial"])
        response.read_error = error
        with self.assertRaises(OSError) as found:
            download(lambda: response, self.target)
        self.assertIs(found.exception, error)
        self.assertEqual(self.target.read_bytes(), b"old")
        self.assertEqual(response.closed, 1)
        self.assert_only_target()

    def test_create_failure_closes(self):
        response = Response([b"x"])
        with self.assertRaises(FileNotFoundError):
            download(lambda: response, self.directory / "absent" / "x")
        self.assertEqual(response.closed, 1)
        self.assertEqual(self.target.read_bytes(), b"old")

    def test_missing_is_normal(self):
        def opener():
            raise AssetMissing("missing")
        self.assertFalse(download(opener, self.target))
        self.assertEqual(self.target.read_bytes(), b"old")

    def test_open_failure_propagates(self):
        error = TimeoutError("open")
        def opener():
            raise error
        with self.assertRaises(TimeoutError) as found:
            download(opener, self.target)
        self.assertIs(found.exception, error)
        self.assert_only_target()

    def test_close_failure_no_replace(self):
        error, response = OSError("close"), Response([b"new"])
        response.close_error = error
        with self.assertRaises(OSError) as found:
            download(lambda: response, self.target)
        self.assertIs(found.exception, error)
        self.assertEqual(response.closed, 1)
        self.assertEqual(self.target.read_bytes(), b"old")
        self.assert_only_target()

    def test_close_does_not_mask_read(self):
        error = RuntimeError("primary")
        response = Response([], read_error=error, close_error=OSError("secondary"))
        with self.assertRaises(RuntimeError) as found:
            download(lambda: response, self.target)
        self.assertIs(found.exception, error)
        self.assertEqual(response.closed, 1)

    def test_replace_failure_cleanup(self):
        response = Response([b"new"])
        def fail_replace(source, target):
            raise PermissionError("replace")
        with self.assertRaises(PermissionError):
            download(lambda: response, self.target, replace=fail_replace)
        self.assertEqual(self.target.read_bytes(), b"old")
        self.assertEqual(response.closed, 1)
        self.assert_only_target()

    def test_empty_resource(self):
        response = Response([])
        self.assertTrue(download(lambda: response, self.target))
        self.assertEqual(self.target.read_bytes(), b"")
        self.assertEqual(response.closed, 1)

    def test_base_exception_cleanup(self):
        response = Response([], read_error=KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt):
            download(lambda: response, self.target)
        self.assertEqual(response.closed, 1)
        self.assertEqual(self.target.read_bytes(), b"old")
        self.assert_only_target()

import json
import unittest
from unittest.mock import Mock, patch

from app import read_optional_cache
from cache_consumer import read_settings


class ReadCloseStream:
    """A stream with no context manager protocol, recording all operations."""

    def __init__(self, contents="{}", *, read_error=None, close_error=None):
        self.contents = contents
        self.read_error = read_error
        self.close_error = close_error
        self.read_calls = 0
        self.close_calls = 0

    def read(self):
        self.read_calls += 1
        if self.read_error is not None:
            raise self.read_error
        return self.contents

    def close(self):
        self.close_calls += 1
        if self.close_error is not None:
            raise self.close_error


class OptionalCacheTests(unittest.TestCase):
    def assert_read_once(self, stream, opener):
        opener.assert_called_once_with("settings.json")
        self.assertEqual(stream.read_calls, 1)
        self.assertEqual(stream.close_calls, 1)

    def test_valid_json_values_are_preserved(self):
        for value in ({"theme": "dark"}, {}, [], False, 0, "", None):
            with self.subTest(value=value):
                stream = ReadCloseStream(json.dumps(value))
                opener = Mock(return_value=stream)

                result = read_optional_cache(opener, "settings.json")

                self.assertEqual(result, value)
                self.assertIs(type(result), type(value))
                self.assert_read_once(stream, opener)

    def test_missing_file_is_a_cache_miss(self):
        opener = Mock(side_effect=FileNotFoundError("missing cache"))

        self.assertIsNone(read_optional_cache(opener, "settings.json"))

        opener.assert_called_once_with("settings.json")

    def test_invalid_json_is_a_cache_miss(self):
        for contents in ("", "{", "not JSON", b"{"):
            with self.subTest(contents=contents):
                stream = ReadCloseStream(contents)
                opener = Mock(return_value=stream)

                self.assertIsNone(read_optional_cache(opener, "settings.json"))

                self.assert_read_once(stream, opener)

    def test_open_errors_reach_the_caller_without_retry(self):
        for error in (PermissionError("denied"), OSError("I/O failed"),
                      RuntimeError("open failed")):
            with self.subTest(error=type(error).__name__):
                opener = Mock(side_effect=error)

                with self.assertRaises(type(error)) as raised:
                    read_optional_cache(opener, "settings.json")

                self.assertIs(raised.exception, error)
                opener.assert_called_once_with("settings.json")

    def test_read_errors_reach_the_caller_and_close_the_stream(self):
        errors = (
            PermissionError("denied"),
            OSError("I/O failed"),
            FileNotFoundError("read failed"),
            RuntimeError("read failed"),
            json.JSONDecodeError("read failed", "", 0),
        )
        for error in errors:
            with self.subTest(error=type(error).__name__):
                stream = ReadCloseStream(read_error=error)
                opener = Mock(return_value=stream)

                with self.assertRaises(type(error)) as raised:
                    read_optional_cache(opener, "settings.json")

                self.assertIs(raised.exception, error)
                self.assert_read_once(stream, opener)

    def test_unexpected_parse_errors_reach_the_caller_and_close_the_stream(self):
        errors = (OSError("parse failed"), RuntimeError("parse failed"))
        for error in errors:
            with self.subTest(error=type(error).__name__):
                stream = ReadCloseStream()
                opener = Mock(return_value=stream)

                with patch("app.json.loads", side_effect=error) as parse:
                    with self.assertRaises(type(error)) as raised:
                        read_optional_cache(opener, "settings.json")

                self.assertIs(raised.exception, error)
                parse.assert_called_once_with("{}")
                self.assert_read_once(stream, opener)

    def test_close_errors_reach_the_caller_without_retry(self):
        for contents in ("{}", "not JSON"):
            with self.subTest(contents=contents):
                error = OSError("close failed")
                stream = ReadCloseStream(contents, close_error=error)
                opener = Mock(return_value=stream)

                with self.assertRaises(OSError) as raised:
                    read_optional_cache(opener, "settings.json")

                self.assertIs(raised.exception, error)
                self.assert_read_once(stream, opener)

    def test_close_failure_preserves_the_read_error_as_context(self):
        read_error = OSError("read failed")
        close_error = OSError("close failed")
        stream = ReadCloseStream(read_error=read_error, close_error=close_error)
        opener = Mock(return_value=stream)

        with self.assertRaises(OSError) as raised:
            read_optional_cache(opener, "settings.json")

        self.assertIs(raised.exception, close_error)
        self.assertIs(raised.exception.__context__, read_error)
        self.assert_read_once(stream, opener)


class SettingsTests(unittest.TestCase):
    def test_missing_file_uses_default_settings(self):
        opener = Mock(side_effect=FileNotFoundError("missing cache"))

        self.assertEqual(read_settings(opener, "settings.json"), {"theme": "system"})

        opener.assert_called_once_with("settings.json")

    def test_invalid_json_and_null_use_default_settings(self):
        for contents in ("not JSON", "null"):
            with self.subTest(contents=contents):
                stream = ReadCloseStream(contents)
                opener = Mock(return_value=stream)

                result = read_settings(opener, "settings.json")

                self.assertEqual(result, {"theme": "system"})
                self.assertEqual(stream.read_calls, 1)
                self.assertEqual(stream.close_calls, 1)
                opener.assert_called_once_with("settings.json")

    def test_falsey_cached_values_do_not_use_default_settings(self):
        for value in ({}, [], False, 0, ""):
            with self.subTest(value=value):
                stream = ReadCloseStream(json.dumps(value))
                opener = Mock(return_value=stream)

                result = read_settings(opener, "settings.json")

                self.assertEqual(result, value)
                self.assertIs(type(result), type(value))
                self.assertEqual(stream.close_calls, 1)
                opener.assert_called_once_with("settings.json")

    def test_io_errors_reach_the_settings_caller(self):
        error = OSError("read failed")
        stream = ReadCloseStream(read_error=error)
        opener = Mock(return_value=stream)

        with self.assertRaises(OSError) as raised:
            read_settings(opener, "settings.json")

        self.assertIs(raised.exception, error)
        self.assertEqual(stream.read_calls, 1)
        self.assertEqual(stream.close_calls, 1)
        opener.assert_called_once_with("settings.json")


if __name__ == "__main__":
    unittest.main()

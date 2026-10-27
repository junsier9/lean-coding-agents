"""Behavioral regression for explicit CLI data versus omitted optional values."""
import unittest

import click
from click.testing import CliRunner


class Contract(unittest.TestCase):
    def make_cli(self, value_type=str, default="default", flag_value="flag", **kwargs):
        calls = []

        @click.command()
        @click.option(
            "-o", "--opt", is_flag=False, flag_value=flag_value,
            default=default, type=value_type, **kwargs
        )
        @click.option("--other", is_flag=True)
        def cli(opt, other):
            calls.append((opt, other))
            return opt, other

        return cli, calls

    def invoke(self, cli, args, **kwargs):
        return CliRunner().invoke(cli, args, standalone_mode=False, **kwargs)

    def value(self, cli, args, expected, **kwargs):
        result = self.invoke(cli, args, **kwargs)
        self.assertIsNone(result.exception, result.output)
        self.assertEqual(result.return_value, expected)
        return result.return_value

    def test_three_forms_string(self):
        cli, _ = self.make_cli()
        self.value(cli, [], ("default", False))
        for name in ("--opt", "-o"):
            self.value(cli, [name], ("flag", False))
            self.value(cli, [name, "value"], ("value", False))

    def test_three_forms_integer(self):
        cli, _ = self.make_cli(int, 1, 2)
        self.value(cli, [], (1, False))
        for name in ("--opt", "-o"):
            self.value(cli, [name], (2, False))
            self.value(cli, [name, "3"], (3, False))

    def test_falsey_defaults_and_flag_values(self):
        for kind, default, flag in ((str, "", ""), (str, None, ""),
                                    (str, "default", None), (int, 0, 0),
                                    (int, None, 0)):
            with self.subTest(kind=kind, default=default, flag=flag):
                cli, _ = self.make_cli(kind, default, flag)
                self.value(cli, [], (default, False))
                self.value(cli, ["--opt"], (flag, False))

    def test_attached_string_data_never_activates_another_option(self):
        for data in ("--other", "--unregistered", "-x", "--", "-3", "-"):
            for arg in ("--opt=" + data, "-o" + data):
                with self.subTest(arg=arg):
                    cli, calls = self.make_cli()
                    self.value(cli, [arg], (data, False))
                    self.assertEqual(calls, [(data, False)])

    def test_attached_negative_integers(self):
        for data in (-3, -17, 0):
            for arg in ("--opt=" + str(data), "-o" + str(data)):
                with self.subTest(arg=arg):
                    cli, _ = self.make_cli(int, 1, 2)
                    value = self.value(cli, [arg], (data, False))[0]
                    self.assertIs(type(value), int)

    def test_attached_empty_string_is_explicit(self):
        cli, calls = self.make_cli()
        self.value(cli, ["--opt="], ("", False))
        self.assertEqual(calls, [("", False)])

    def test_bare_optional_value_still_allows_following_real_option(self):
        for arg in ("--opt", "-o"):
            with self.subTest(arg=arg):
                cli, _ = self.make_cli()
                self.value(cli, [arg, "--other"], ("flag", True))

    def test_invalid_attached_integer_is_rejected_without_callback(self):
        for data in ("--other", "oops", "-"):
            for arg in ("--opt=" + data, "-o" + data):
                with self.subTest(arg=arg):
                    cli, calls = self.make_cli(int, 1, 2)
                    result = self.invoke(cli, [arg])
                    self.assertIsInstance(result.exception, click.BadParameter)
                    self.assertEqual(calls, [])

    def test_invalid_separate_integer_is_not_replaced_by_flag_value(self):
        for name in ("--opt", "-o"):
            cli, calls = self.make_cli(int, 1, 2)
            result = self.invoke(cli, [name, "oops"])
            self.assertIsInstance(result.exception, click.BadParameter)
            self.assertEqual(calls, [])

    def test_ordinary_option_without_flag_value_still_requires_value(self):
        for kwargs in ({}, {"default": None}, {"default": "default"}):
            with self.subTest(kwargs=kwargs):
                calls = []

                @click.command()
                @click.option("--opt", is_flag=False, **kwargs)
                def cli(opt):
                    calls.append(opt)

                result = self.invoke(cli, ["--opt"])
                self.assertIsInstance(result.exception, click.BadOptionUsage)
                self.assertEqual(calls, [])

    def test_cli_binding_and_fallback_preserve_existing_source_precedence(self):
        cli, _ = self.make_cli(envvar="D10_OPTION_VALUE")
        kwargs = {"env": {"D10_OPTION_VALUE": "environment"},
                  "default_map": {"opt": "mapping"}}
        self.value(cli, [], ("environment", False), **kwargs)
        self.value(cli, ["--opt"], ("flag", False), **kwargs)
        self.value(cli, ["--opt=--other"], ("--other", False), **kwargs)

    def test_original_callback_exception_is_not_swallowed_or_retried(self):
        error = RuntimeError("callback failure")
        seen = []
        bodies = []

        def callback(ctx, param, value):
            seen.append(value)
            raise error

        @click.command()
        @click.option("-o", "--opt", is_flag=False, flag_value="flag",
                      default="default", callback=callback)
        def cli(opt):
            bodies.append(opt)

        result = self.invoke(cli, ["--opt=--other"])
        self.assertIs(result.exception, error)
        self.assertEqual(seen, ["--other"])
        self.assertEqual(bodies, [])

    def test_context_resource_released_on_success_and_body_failure(self):
        for fail in (False, True):
            with self.subTest(fail=fail):
                events = []
                error = RuntimeError("body failure")

                class Resource:
                    def __enter__(self):
                        events.append("enter")
                        return self

                    def __exit__(self, kind, value, tb):
                        events.append(("exit", value))

                def callback(ctx, param, value):
                    ctx.with_resource(Resource())
                    return value

                @click.command()
                @click.option("-o", "--opt", is_flag=False, flag_value="flag",
                              default="default", callback=callback)
                def cli(opt):
                    events.append(("body", opt))
                    if fail:
                        raise error
                    return opt

                result = self.invoke(cli, ["-o--other"])
                if fail:
                    self.assertIs(result.exception, error)
                else:
                    self.assertIsNone(result.exception)
                    self.assertEqual(result.return_value, "--other")
                self.assertEqual(events, ["enter", ("body", "--other"),
                                          ("exit", error if fail else None)])

    def test_repeated_optional_options_keep_each_binding(self):
        cli, _ = self.make_cli(default=(), multiple=True)
        self.value(cli, ["--opt", "--opt=--other", "-ovalue"],
                   (("flag", "--other", "value"), False))

    def test_multi_argument_regular_option_keeps_attached_value(self):
        @click.command()
        @click.option("-p", "--pair", nargs=2, type=str)
        @click.option("--other", is_flag=True)
        def cli(pair, other):
            return pair, other

        for arg in ("--pair=--other", "-p--other"):
            self.value(cli, [arg, "tail"], (("--other", "tail"), False))

    def test_unknown_standalone_option_is_not_ignored(self):
        cli, calls = self.make_cli()
        result = self.invoke(cli, ["--unknown"])
        self.assertIsInstance(result.exception, click.NoSuchOption)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()

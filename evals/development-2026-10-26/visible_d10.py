import unittest
import click
from click.testing import CliRunner


class PublicExample(unittest.TestCase):
    def make_cli(self):
        @click.command()
        @click.option("-o", "--opt", is_flag=False,
                      flag_value="flag", default="default")
        @click.option("--other", is_flag=True)
        def cli(opt, other):
            return opt, other
        return cli

    def test_existing_three_forms(self):
        cli = self.make_cli()
        for args, expected in [([], ("default", False)),
                               (["--opt"], ("flag", False)),
                               (["--opt", "value"], ("value", False))]:
            result = CliRunner().invoke(cli, args, standalone_mode=False)
            self.assertIsNone(result.exception)
            self.assertEqual(result.return_value, expected)

    def test_explicit_value_reproduction(self):
        result = CliRunner().invoke(self.make_cli(), ["--opt=--other"],
                                    standalone_mode=False)
        self.assertIsNone(result.exception)
        self.assertEqual(result.return_value, ("--other", False))


if __name__ == "__main__":
    unittest.main()

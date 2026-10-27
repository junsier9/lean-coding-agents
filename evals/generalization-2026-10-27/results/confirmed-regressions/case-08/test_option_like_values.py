import pytest
from click import Argument, Choice, Command, Option
from click.shell_completion import ShellComplete

@pytest.mark.parametrize('value', ['-1', '-', '--flag'])
@pytest.mark.parametrize('prefix', [[], ['--flag']])
def test_option_like_value_keeps_pending_option_completion(value, prefix):
    choices = [value, '1', '2']
    cli = Command('cli', params=[
        Option(['--flag'], is_flag=True),
        Option(['--pair'], nargs=2, type=Choice(choices)),
        Argument(['rest'], type=Choice(['rest'])),
    ])
    args = [*prefix, '--pair', value]
    # The parser accepts this exact first value as part of --pair.
    with cli.make_context('cli', [*args, '1', 'rest']) as ctx:
        assert ctx.params['pair'] == (value, '1')
        assert ctx.params['rest'] == 'rest'
    comp = ShellComplete(cli, {}, cli.name, '_CLI_COMPLETE')
    actual = [item.value for item in comp.get_completions(args, '')]
    assert actual == choices

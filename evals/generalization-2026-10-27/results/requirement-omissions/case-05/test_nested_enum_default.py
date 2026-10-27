import enum
import pytest
from click import Choice, Command, Context, Option
from click.testing import CliRunner

@pytest.mark.parametrize('enum_type', [enum.Enum,enum.IntEnum,enum.Flag,enum.IntFlag])
@pytest.mark.parametrize('case_sensitive', [True,False])
def test_nested_enum_choice_default_uses_choice_names(enum_type,case_sensitive):
    class Color(enum_type):
        Red = 1
        Blue = 2
    default = ((Color.Red,Color.Blue),)
    opt = Option(['--color'],type=Choice(Color,case_sensitive=case_sensitive),
                 multiple=True,nargs=2,default=default,show_default=True)
    cli = Command('cli',params=[opt],callback=lambda color:color)
    names = ['Red','Blue'] if case_sensitive else ['red','blue']
    result = CliRunner().invoke(cli,[],standalone_mode=False)
    assert result.exit_code == 0
    assert result.return_value == default
    result = CliRunner().invoke(cli,['--color',*names],standalone_mode=False)
    assert result.exit_code == 0
    assert result.return_value == default
    default_text = opt.get_help_extra(Context(cli))['default']
    # Only requires choice names; does not prescribe grouping punctuation.
    assert 'Color.' not in default_text
    assert all(name in default_text for name in names)

import enum,json,sys
sys.path[:0]=[sys.argv[1],sys.argv[2]]
from click import Choice,Command,Context,Option
from click.testing import CliRunner
class Color(enum.Enum):
    Red=1
    Blue=2
default=((Color.Red,Color.Blue),)
opt=Option(['--color'],type=Choice(Color),multiple=True,nargs=2,default=default,show_default=True)
cli=Command('cli',params=[opt],callback=lambda color:color)
a=CliRunner().invoke(cli,[],standalone_mode=False)
b=CliRunner().invoke(cli,['--color','Red','Blue'],standalone_mode=False)
print(json.dumps({'default_help':opt.get_help_extra(Context(cli))['default'],'default_parse_matches':a.exit_code==0 and a.return_value==default,'explicit_parse_matches':b.exit_code==0 and b.return_value==default,'help':CliRunner().invoke(cli,['--help']).output}))

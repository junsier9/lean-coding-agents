import json,sys
sys.path.insert(0,sys.argv[1])
import click
from click.parser import _OptionParser
from click.testing import CliRunner
class AlternateParser(_OptionParser):
    def parse_args(self,args):
        return {},list(args),[]
class CustomCommand(click.Command):
    def make_parser(self,ctx):
        return AlternateParser(ctx)
result=CliRunner().invoke(CustomCommand('cli',callback=lambda:42),[],standalone_mode=False)
print(json.dumps({'exit_code':result.exit_code,'return_value':result.return_value,'exception_type':type(result.exception).__name__ if result.exception else None,'exception_message':str(result.exception) if result.exception else None}))

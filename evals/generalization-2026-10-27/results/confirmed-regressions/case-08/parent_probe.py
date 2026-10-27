import json,sys
sys.path[:0]=[sys.argv[1],sys.argv[2]]
from click import Argument,Choice,Command,Option
from click.shell_completion import ShellComplete
cli=Command('cli',params=[Option(['--flag'],is_flag=True),Option(['--pair'],nargs=2,type=Choice(['-1','1','2'])),Argument(['rest'],type=Choice(['rest']))])
with cli.make_context('cli',['--flag','--pair','-1','1','rest']) as ctx: parsed=dict(ctx.params)
actual=[c.value for c in ShellComplete(cli,{},cli.name,'_CLI_COMPLETE').get_completions(['--flag','--pair','-1'],'')]
print(json.dumps({'parsed':parsed,'completion':actual}))

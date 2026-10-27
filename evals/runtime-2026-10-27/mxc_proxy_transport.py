"""Scoped native MXC launcher using the existing loopback OS proxy.

The old frozen launcher stays unchanged. This version preserves its command,
600-second limit and evidence lifecycle, adding only explicit MXC selection
and a completely bound child-process proxy/bypass environment.
"""
import json
import os
import subprocess
import time
from urllib.parse import urlsplit
from urllib.request import getproxies


def build_command(runner, workspace, logs, env):
    return [str(runner.CODEX), '--no-daemon', '-a', 'never', '-s', 'workspace-write',
            '-c', 'windows.sandbox="mxc"',
            '-c', 'model_provider="lean_eval_http"',
            '-c', 'model_providers.lean_eval_http.name="OpenAI evaluation HTTP"',
            '-c', 'model_providers.lean_eval_http.requires_openai_auth=true',
            '-c', 'model_providers.lean_eval_http.supports_websockets=false',
            '-c', 'shell_environment_policy.set={' + ','.join(
                name + '=' + json.dumps(env[name]) for name in
                ('PYTHONPATH', 'PYTEST_DISABLE_PLUGIN_AUTOLOAD', 'PYTHONDONTWRITEBYTECODE', 'PYTHONIOENCODING')) + '}',
            'exec', '--ephemeral', '--skip-git-repo-check', '--json', '--color', 'never',
            '-C', str(workspace), '-o', str(logs / 'final.txt'), '-']


def install(runner):
    proxy = getproxies().get('https')
    if not proxy:
        raise ValueError('An existing HTTPS OS proxy is required')
    parsed = urlsplit(proxy)
    if (parsed.scheme not in {'http', 'https'} or parsed.hostname not in {'127.0.0.1', 'localhost', '::1'}
            or parsed.username or parsed.password or parsed.path not in {'', '/'}
            or parsed.query or parsed.fragment or parsed.port is None):
        raise ValueError('Expected the existing credential-free loopback OS proxy')
    route = {name: proxy for name in ('HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 'ALL_PROXY', 'all_proxy')}
    route.update(NO_PROXY='localhost,127.0.0.1,::1', no_proxy='localhost,127.0.0.1,::1')
    original = runner.environment

    def environment(workspace):
        result = original(workspace)
        result.update(route)
        return result

    def launch_cli(workspace, logs, prompt, identity):
        env = environment(workspace)
        command = build_command(runner, workspace, logs, env)
        runner.write(logs / 'started.json', {**identity, 'started_at': runner.utc(), 'command': command,
                                           'transport_environment': route, 'windows_sandbox': 'mxc'})
        (logs / 'prompt.txt').write_text(prompt, encoding='utf-8')
        start, timed_out = time.monotonic(), False
        with (logs / 'events.jsonl').open('w', encoding='utf-8') as stdout, (logs / 'stderr.txt').open('w', encoding='utf-8') as stderr:
            proc = subprocess.Popen(command, cwd=workspace, env=env, stdin=subprocess.PIPE,
                                    stdout=stdout, stderr=stderr, text=True, encoding='utf-8',
                                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            try:
                proc.communicate(prompt, timeout=600)
            except subprocess.TimeoutExpired:
                timed_out = True
                if proc.poll() is None:
                    if os.name == 'nt':
                        subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'], capture_output=True)
                    else:
                        proc.kill()
                proc.wait(timeout=20)
        return {**identity, 'started_at': runner.read(logs / 'started.json')['started_at'],
                'cli_exit_code': proc.returncode, 'timeout': timed_out, 'client_seconds': time.monotonic() - start}

    runner.environment = environment
    runner.launch_cli = launch_cli
    return {'source': 'existing_OS_loopback_proxy', 'environment': route,
            'windows_sandbox': 'mxc', 'permission_mode': 'workspace-write',
            'approval_policy': 'never', 'TLS_verification_unchanged': True,
            'saved_config_or_ACL_changes_by_controller': False,
            'model_provider_or_auth_changed': False}

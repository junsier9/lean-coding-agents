"""Freeze once and run a bounded long-session diagnostic; never replace failures."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import argparse
import hashlib
import json
import shutil
import sys
import zipfile

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent / 'production-2026-10-25'))
sys.path.insert(0, str(HERE.parent / 'runtime-2026-10-27'))
import runner
import resource_gate
import mxc_proxy_transport

ROOT = PROJECT / '.local/native-long-session-2026-10-27-v1'
TRANSPORT = mxc_proxy_transport.install(runner)
OUT = HERE / 'results'


def require(value, message):
    if not value:
        raise ValueError(message)


def immutable(path, value):
    if path.exists():
        require(runner.read(path) == value, 'Frozen file changed: ' + str(path))
    else:
        runner.write(path, value)


def prepare():
    require(__debug__, 'Do not optimize this controller')
    require(not resource_gate.STOP.exists(), 'Existing provider quota stop')
    require(not list((PROJECT / '.local/production-runtime').glob('*/active-suite.lock')), 'Native maintenance suite is active')
    require(not list((PROJECT / '.local/production-runtime/generalization-v6-2026-10-27/runs').glob('**/started.json')), 'Fresh tasks must stay sealed')
    protocol = runner.read(HERE / 'protocol.json')
    previous = HERE.parent / 'transport-revalidation-mxc-2026-10-27/results'
    old_freeze = runner.read(previous / 'freeze.json')
    require(TRANSPORT == old_freeze['transport'], 'Keep the previously checked complete route and MXC policy')
    require(runner.transport.settings() == runner.SETTINGS == old_freeze['settings'], 'Keep model/settings')
    require(runner.digest(runner.CODEX) == old_freeze['cli_sha256'], 'Native CLI changed')
    require(runner.digest(Path(sys.executable)) == old_freeze['python_sha256'], 'Python changed')
    source = [Path(__file__), HERE / 'probe.py', HERE / 'protocol.json', Path(runner.__file__),
              Path(runner.transport.__file__), Path(mxc_proxy_transport.__file__), Path(resource_gate.__file__)]
    historical = [previous / 'summary.json', previous / 'freeze.json', previous / 'operational-diagnosis.json',
                  HERE.parent / 'development-2026-10-27/results/summary.json',
                  HERE.parent / 'development-2026-10-27/results/recovery-result.json',
                  HERE.parent / 'transport-revalidation-2026-10-27/results/interrupted-summary.json',
                  HERE.parent / 'generalization-2026-10-26/results/beta-qualification.json',
                  HERE.parent / 'production-2026-10-25/results/v4/release-qualification.json']
    frozen = {'protocol': protocol, 'transport': TRANSPORT,
              'source_sha256': {p.relative_to(PROJECT).as_posix(): runner.digest(p) for p in source},
              'historical_reports_sha256': {p.relative_to(PROJECT).as_posix(): runner.digest(p) for p in historical},
              'settings': runner.SETTINGS, 'CLI_sha256': runner.digest(runner.CODEX),
              'python_sha256': runner.digest(Path(sys.executable)),
              'session_ids': [f'session-{i:02}' for i in range(1, 7)],
              'fresh_tasks_remain_sealed': True, 'candidate_or_maintenance_case_reads': 0}
    immutable(OUT / 'freeze.json', frozen)
    if not (OUT / 'registered-at.json').exists():
        require(not list((ROOT / 'runs').glob('*/started.json')), 'Calls predate registration')
        runner.write(OUT / 'registered-at.json', {'registered_at': runner.utc(), 'native_calls': 0,
                     'freeze_sha256': runner.digest(OUT / 'freeze.json')})
    ROOT.mkdir(parents=True, exist_ok=True)
    return frozen


def prompt():
    return ('这是一次原生 Codex 长会话运行环境诊断。只在当前目录操作，不读其他项目、skill、案例或评测文件；'
            '不联网、不安装、不委派、不修改配置权限、不提交发布。只使用 shell 执行器，禁止 MCP。'
            '请严格完成12轮独立交互：第1轮 previous=START，之后每轮从刚完成的工具输出读取 next_token，'
            '作为下一轮 --previous。每轮单独调用一次 shell；每次命令只执行一个 probe.py 步骤，'
            '不得用循环、批量、多命令、并行或另写脚本代跑。shell 工具 yield_time_ms 设为30000，'
            '因为每步会等待25秒。若返回仍运行，轮询该命令直至结束，再进行下一轮。'
            f'命令使用已有解释器：& "{runner.PYTHON}" -I -B probe.py --step N --previous TOKEN。'
            '不要读取或编辑 probe.py、状态或输出文件，只让该命令处理它们。每步成功后再继续；'
            '若工具失败则停止并说明实际错误，不修改探针以绕过失败。12轮全部成功最后只回复 LONG_SESSION_OK。')


def run_session(key):
    logs, workspace = ROOT / 'runs' / key, ROOT / 'work' / key
    done_path = logs / 'client-done.json'
    if done_path.exists():
        return runner.read(done_path)
    require(not logs.exists() and not workspace.exists(), 'An interrupted native slot must not be relaunched: ' + key)
    workspace.mkdir(parents=True)
    logs.mkdir(parents=True)
    shutil.copyfile(HERE / 'probe.py', workspace / 'probe.py')
    runner.write(workspace / 'probe-state.json', {'step': 0, 'token': 'START'})
    identity = {'key': key, 'diagnostic_only': True, 'not_a_skill_or_fresh_task_sample': True}
    done = runner.launch_cli(workspace, logs, prompt(), identity)
    runner.write(done_path, done)
    return done


def inspect(key, frozen):
    logs, workspace = ROOT / 'runs' / key, ROOT / 'work' / key
    if not (logs / 'client-done.json').exists():
        return {'key': key, 'attempted': (logs / 'started.json').exists(), 'passed': False,
                'status': 'interrupted_without_done' if logs.exists() else 'not_attempted'}
    done = runner.read(logs / 'client-done.json')
    started = runner.read(logs / 'started.json')
    raw = (logs / 'events.jsonl').read_text(encoding='utf-8')
    events, malformed = [], 0
    for line in raw.splitlines():
        if line.strip():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                malformed += 1
    threads = [e.get('thread_id') for e in events if e['type'] == 'thread.started']
    terminal = [e['type'] for e in events if e['type'] in {'turn.completed', 'turn.failed'}]
    errors = [e.get('message', e.get('error', {}).get('message', '')) for e in events if e['type'] in {'error', 'turn.failed'}]
    items = [e['item'] for e in events if e['type'] == 'item.completed']
    failed_tools = [i.get('type') for i in items if i.get('type') == 'command_execution' and i.get('exit_code') != 0
                    or i.get('type') in {'mcp_tool_call', 'file_change'} and i.get('status') == 'failed']
    unexpected_items = sorted({i.get('type') for i in items} - {'agent_message', 'reasoning', 'todo_list', 'command_execution'})
    outputs, output_shape_ok = [], True
    for item in items:
        if item.get('type') != 'command_execution':
            continue
        markers = []
        for line in item.get('aggregated_output', '').splitlines():
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict) and value.get('native_session_probe') is True:
                markers.append(value)
        if markers:
            output_shape_ok &= len(markers) == 1 and item['exit_code'] == 0 and 'probe.py' in item.get('command', '')
            outputs.extend(markers)
    chain_ok = len(outputs) == 12 and [r['step'] for r in outputs] == list(range(1, 13))
    previous = 'START'
    for row in outputs:
        chain_ok &= row['previous'] == previous and row['tool_seconds'] >= 25
        file = workspace / f'round-{row["step"]:02}.json'
        chain_ok &= file.is_file() and runner.read(file) == row
        previous = row['next_token']
    state = runner.read(workspace / 'probe-state.json')
    chain_ok &= state == {'step': 12, 'token': previous}
    usage = [e['usage'] for e in events if e['type'] == 'turn.completed' and e.get('usage')]
    complete_usage = bool(usage) and all(type(u.get(k)) is int and u[k] >= 0 for u in usage for k in ('input_tokens', 'output_tokens'))
    command_ok = (started['transport_environment'] == frozen['transport']['environment']
                  and started['windows_sandbox'] == 'mxc'
                  and started['command'] == mxc_proxy_transport.build_command(runner, workspace, logs, runner.environment(workspace)))
    probe_ok = runner.digest(workspace / 'probe.py') == frozen['source_sha256']['evals/long-session-2026-10-27-v1/probe.py']
    final = (logs / 'final.txt').read_text(encoding='utf-8') if (logs / 'final.txt').exists() else None
    passed = (done['cli_exit_code'] == 0 and not done['timeout'] and terminal[-1:] == ['turn.completed']
              and complete_usage and not malformed and not errors and not failed_tools and not unexpected_items
              and len(threads) == 1 and bool(threads[0]) and output_shape_ok and chain_ok and command_ok and probe_ok
              and done['client_seconds'] >= 300 and final is not None and final.strip() == 'LONG_SESSION_OK')
    result = {**done, 'attempted': True, 'passed': bool(passed), 'status': 'pass' if passed else 'failed',
              'thread_ids': threads, 'terminal_events': terminal, 'errors': errors, 'malformed_lines': malformed,
              'failed_tool_types': failed_tools, 'separate_probe_outputs': len(outputs), 'output_shape_ok': bool(output_shape_ok),
              'unexpected_item_types': unexpected_items,
              'token_chain_and_disk_outputs_verified': bool(chain_ok), 'complete_usage': complete_usage,
              'usage': usage, 'exact_command_and_route_verified': command_ok, 'probe_unchanged': probe_ok,
              'final': final, 'raw_artifact_sha256': {p.name: runner.digest(p) for p in sorted(logs.iterdir()) if p.is_file()}}
    return result


def retain_and_report(frozen):
    require(not list((PROJECT / '.local/production-runtime/generalization-v6-2026-10-27/runs').glob('**/started.json')), 'Fresh tasks were called')
    rows = [inspect(key, frozen) for key in frozen['session_ids']]
    threads = [t for row in rows for t in row.get('thread_ids', [])]
    require(len(threads) == len(set(threads)), 'Native thread IDs reused')
    for name, sha in {**frozen['source_sha256'], **frozen['historical_reports_sha256']}.items():
        require(runner.digest(PROJECT / name) == sha, 'Frozen or historical bytes changed')
    report = {'evaluated_at': runner.utc(), 'evidence_class': frozen['protocol']['evidence_class'],
              'planned_sessions': 6, 'attempted': sum(r['attempted'] for r in rows),
              'passed_sessions': sum(r['passed'] for r in rows),
              'long_session_gate_passed': all(r['passed'] for r in rows),
              'fresh_tasks_remain_sealed': True, 'fresh_native_calls': 0,
              'old_failures_preserved': True, 'next_environment_window_registered': False,
              'sessions': rows, 'limits': frozen['protocol']['limits']}
    immutable(OUT / 'summary.json', report)
    target = PROJECT / '.local/native-long-session-2026-10-27-v1-evidence.zip'
    require(not target.exists(), 'Evidence archive already exists')
    sources = {('runtime/' + p.relative_to(ROOT).as_posix()): p for p in sorted(ROOT.rglob('*')) if p.is_file()}
    sources.update({('public/' + p.relative_to(HERE).as_posix()): p for p in sorted(HERE.rglob('*')) if p.is_file() and '__pycache__' not in p.parts})
    index = {n: runner.digest(p) for n, p in sources.items()}
    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for n, p in sources.items():
            archive.write(p, n)
        archive.writestr('entry-sha256.json', json.dumps(index, indent=2))
    with zipfile.ZipFile(target) as archive:
        require(archive.testzip() is None, 'Evidence archive CRC failed')
        require(all(hashlib.sha256(archive.read(n)).hexdigest().upper() == h for n, h in index.items()), 'Archive bytes differ')
    runner.write(OUT / 'evidence-retention.json', {'private_archive': target.relative_to(PROJECT).as_posix(),
                 'sha256': runner.digest(target), 'entries': len(index) + 1, 'CRC_and_all_entry_hashes_verified': True,
                 'includes_all_attempted_and_failed_slots': True, 'external_publication': False})
    print(json.dumps({k:v for k,v in report.items() if k != 'sessions'}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    frozen = prepare()
    if args.prepare_only:
        print(json.dumps({'registered': True, 'native_calls': 0, 'planned_maximum': 6}))
    else:
        require(not (OUT / 'summary.json').exists(), 'Closed diagnostic window must not be rerun')
        with runner.controller_lock(ROOT, 'long-session-diagnostic'):
            for wave in range(2):
                keys = frozen['session_ids'][wave*3:wave*3+3]
                with ThreadPoolExecutor(max_workers=3) as pool:
                    futures = [pool.submit(run_session, key) for key in keys]
                    for future in futures:
                        future.result()
                results = [inspect(key, frozen) for key in keys]
                runner.write(ROOT / f'wave-{wave+1}.json', results)
                print(json.dumps({'wave': wave+1, 'attempted': 3, 'passed': sum(r['passed'] for r in results)}), flush=True)
                if not all(r['passed'] for r in results):
                    break
        retain_and_report(frozen)

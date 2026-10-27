"""One separately registered development task using an interrupted artifact.

The original 11/12 result remains failed. This supplemental attempt is neither
a replacement nor fresh generalization evidence; rule bytes stay unchanged.
"""
from pathlib import Path
import argparse
import copy
import json
import shutil
import sys

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent / 'production-2026-10-25'))
sys.path.insert(0, str(HERE.parent / 'generalization-2026-10-26'))
import runner
import resource_gate
from observe import observe
from export_results import FIELDS, public_value

ROOT = PROJECT / '.local/production-runtime/dev-v6-d10-interruption-recovery'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def prepare():
    original_root = PROJECT / '.local/production-runtime/dev-v6-iteration1'
    key = 'development--d10--r1--with-skill'
    logs = original_root / 'runs/development' / key
    prior = runner.read(logs / 'record.json')
    require(prior['timeout'] is True and not prior['accepted'] and len(prior['stream_errors']) == 4, 'Original interruption evidence differs')
    original_summary = HERE / 'results/summary.json'
    require(runner.read(original_summary)['development_gate_passed'] is False, 'Do not overwrite or relabel the failed batch')
    workspace = Path(prior['workspace'])
    require(not runner.git(workspace, 'diff', '--name-only', 'HEAD').strip(), 'Recovery assumes production source had not been changed')
    case = copy.deepcopy(runner.read(HERE / 'cases/d10.json'))
    case['id'] = 'd10-recovery'
    case['task'] = ('先前维护会话因连接重试与超时而中断，生产源码尚未修复；test_option_values.py 是已留下的本地测试。'
                    '请先检查当前状态和现有测试，再完成下列维护需求。这个任务属于中断恢复开发验证。\n\n' + case['task'])
    case['files']['test_option_values.py'] = (workspace / 'test_option_values.py').read_text(encoding='utf-8')
    case['evidence_class'] = 'known_interrupted_artifact_development_only'
    path = HERE / 'recovery-case/d10-recovery.json'
    if path.exists():
        require(runner.read(path) == case, 'Recovery input changed')
    else:
        runner.write(path, case)
    snapshot = ROOT / 'skill-input'
    source = HERE / 'candidates/v6-python'
    for name in ('SKILL.md', 'agents/openai.yaml'):
        destination = snapshot / name
        if not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / name, destination)
        require(runner.digest(destination) == runner.digest(source / name), 'Rule bytes changed')
    require(runner.preflight_case(case, ROOT)['qualified'], 'Recovery initial-fail/reference-pass contract differs')
    frozen = {
        'registered_at': runner.utc(), 'planned_native_attempts': 1, 'repeats': 1, 'max_workers': 1,
        'candidate_unchanged': True, 'case_sha256': runner.case_digest(case),
        'skill_sha256': runner.digest(snapshot / 'SKILL.md'),
        'original_failed_summary_sha256': runner.digest(original_summary),
        'original_interrupted_record_sha256': runner.digest(logs / 'record.json'),
        'original_events_sha256': runner.digest(logs / 'events.jsonl'),
        'visible_interrupted_test_sha256': runner.digest(workspace / 'test_option_values.py'),
        'driver_sha256': runner.digest(Path(__file__)), 'settings': runner.SETTINGS,
        'policy': 'One different development input with the existing interrupted test artifact; no replacement of original results, no fresh-task/efficiency claim, no retries of this supplemental model attempt'
    }
    target = HERE / 'results/recovery-freeze.json'
    if target.exists():
        previous = runner.read(target)
        require(all(previous[k] == v for k, v in frozen.items() if k != 'registered_at'), 'Recovery freeze changed')
        frozen = previous
    else:
        require(not list((ROOT / 'runs').glob('**/started.json')), 'Register before a recovery model call')
        runner.write(target, frozen)
    return case, snapshot / 'SKILL.md', frozen


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    case, skill, frozen = prepare()
    if args.prepare_only:
        print(json.dumps({'prepared': True, 'planned': 1, 'new_model_calls': 0}))
    else:
        require(not list((PROJECT / '.local/production-runtime').glob('*/active-suite.lock')), 'Another native suite is active')
        resource_gate.install()
        record = runner.run_suite(ROOT, [case], 'recovery', skill, 1, ('with-skill',), 1)[0]
        logs = ROOT / 'runs/recovery' / record['key']
        raw = (logs / 'events.jsonl').read_text(encoding='utf-8')
        observed = observe(raw, skill.read_text(encoding='utf-8'))
        terminal = [e['type'] for e in map(json.loads, raw.splitlines()) if e['type'] in ('turn.completed', 'turn.failed')]
        success = record['accepted'] and not record['timeout'] and record['cli_exit_code'] == 0 and bool(terminal and terminal[-1] == 'turn.completed') and observed['body_visible']
        require(runner.digest(HERE / 'results/summary.json') == frozen['original_failed_summary_sha256'], 'Original failed summary changed')
        row = {name: record.get(name) for name in FIELDS}
        row.update(full_body_observation=observed, recovery_passed=bool(success),
                   original_development_gate_passed=False,
                   raw_artifact_sha256={p.name: runner.digest(p) for p in sorted(logs.iterdir()) if p.is_file()},
                   no_replacement_or_fresh_generalization_claim=True)
        runner.write(HERE / 'results/recovery-result.json', public_value(row))
        print(json.dumps({'recovery_passed': bool(success), 'original_gate_remains_false': True}))

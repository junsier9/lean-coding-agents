"""Reconcile saved evidence with the unchanged12/12 gate; no model/grade calls.

This supplementary evidence check does not modify the frozen driver or rule.
"""
from pathlib import Path
import hashlib
import json
import re
import sys
import zipfile

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent / 'production-2026-10-25'))
sys.path.insert(0, str(HERE.parent / 'generalization-2026-10-26'))
sys.path.insert(0, str(HERE.parent / 'runtime-2026-10-27'))
import runner
import qualify_beta as legacy
import mxc_proxy_transport
from observe import observe
from audit_production_trace import scan

ROOT = PROJECT / '.local/production-runtime/v6-environment-revalidation-v2'
require = legacy.require


def verify(here=HERE, root=ROOT):
    frozen = runner.read(here / 'results/freeze.json')
    summary = runner.read(here / 'results/summary.json')
    require(not (root / 'active-suite.lock').exists(), 'Wait for the fixed window to finish')
    legacy.ROOT = root
    skill = root / 'skill-input/SKILL.md'
    cases = {key: runner.read(HERE.parent / 'development-2026-10-27/cases' / (key + '.json')) for key in frozen['cases']}
    expected_keys = {f'development--{key}--r{repeat}--with-skill' for key in cases for repeat in range(3)}
    paths = sorted((root / 'runs/development').glob('*/started.json'))
    require(len(paths) == 12 and {p.parent.name for p in paths} == expected_keys, 'All12 fixed slots must be present exactly once')
    require(runner.digest(skill) == frozen['skill_sha256'], 'Frozen skill changed')
    require(runner.digest(skill.parent / 'agents/openai.yaml') == frozen['ui_sha256'], 'Frozen UI changed')
    require(runner.transport.settings() == frozen['settings'] == runner.SETTINGS, 'Model/settings changed')
    require(runner.digest(runner.CODEX) == frozen['cli_sha256'], 'CLI changed')
    require(runner.digest(Path(sys.executable)) == frozen['python_sha256'], 'Python changed')
    history = runner.read(HERE.parent / 'long-session-2026-10-27-v1/results/freeze.json')
    for name, sha in {**frozen['source_sha256'], **history['historical_reports_sha256']}.items():
        require(runner.digest(PROJECT / name) == sha, 'Frozen source or old failure changed: ' + name)
    for name, sha in frozen['fixture_files_sha256'].items():
        require(runner.digest(HERE.parent / 'development-2026-10-27/cases' / name) == sha, 'Case bytes changed')
    exports = {r['key']: r for r in [json.loads(line) for line in (here / 'results/runs.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]}
    require(set(exports) == expected_keys, 'Exported slots differ')
    rows, threads, flags, required_raw = [], [], [], {}
    registered_at = runner.read(here / 'results/frozen-at.json')['frozen_at']
    for path in paths:
        logs, key = path.parent, path.parent.name
        started = runner.read(path)
        record = runner.read(logs / 'record.json')
        case = cases[record['case_id']]
        require(record['key'] == key and record['phase'] == 'development' and record['arm'] == 'with-skill', 'Slot identity changed')
        require(key == f'development--{record["case_id"]}--r{record["repeat"]}--with-skill', 'Repeat identity differs')
        require(record['case_sha256'] == frozen['cases'][case['id']] == runner.case_digest(case), 'Frozen case differs')
        require(record['started_at'] >= registered_at, 'Attempt predates registration')
        workspace = Path(record['workspace'])
        require(started['transport_environment'] == frozen['transport']['environment'] and started['windows_sandbox'] == 'mxc', 'Transport differs')
        require(started['command'] == mxc_proxy_transport.build_command(runner, workspace, logs, runner.environment(workspace)), 'Native argv differs')
        legacy.verify_record(record, started, runner.read(logs / 'client-done.json'),
                             runner.read(logs / 'cli-checkpoint.json'), case, logs, skill, frozen)
        grade = record['grade']
        require(set(grade['modes']) == {'normal', 'optimized'}, 'Both normal and -O are required')
        expected = runner.read(root / 'preflight' / (case['id'] + '.json'))['expected']
        for field in ('tests_collected', 'tests_ran', 'passed', 'failed', 'errors', 'skipped'):
            require(grade[field] == sum(grade['modes'][m][field] for m in grade['modes']), 'Mode aggregate differs')
        for mode in ('normal', 'optimized'):
            logfile = root / 'grades/development' / f'{key}-{mode}-tests.log'
            text = logfile.read_text(encoding='utf-8')
            counts = re.findall(r'^Ran (\d+) tests? in [0-9.]+s$', text, re.M)
            require(bool(counts) and int(counts[-1]) == grade['modes'][mode]['tests_ran'], 'Original test log count differs')
            if record['accepted']:
                require(grade['modes'][mode]['tests_ran'] == expected['modes'][mode]['tests_ran'] > 0, 'Accepted mode count differs')
                require(bool(re.search(r'^OK\s*$', text, re.M)), 'Accepted mode lacks actual OK output')
            required_raw['runtime/' + logfile.relative_to(root).as_posix()] = runner.digest(logfile)
        raw = (logs / 'events.jsonl').read_text(encoding='utf-8')
        observation = observe(raw, skill.read_text(encoding='utf-8'))
        require(not observation['malformed_lines'] and len(observation['thread_ids']) == 1, 'Malformed or missing native thread')
        threads += observation['thread_ids']
        events = [json.loads(line) for line in raw.splitlines() if line.strip()]
        terminal = [e['type'] for e in events if e['type'] in {'turn.completed', 'turn.failed'}]
        normal = record['cli_exit_code'] == 0 and not record['timeout'] and terminal[-1:] == ['turn.completed']
        artifact_hashes = {p.name: runner.digest(p) for p in sorted(logs.iterdir()) if p.is_file()}
        require(artifact_hashes == exports[key]['raw_artifact_sha256'], 'Exported raw hashes differ')
        for name, sha in artifact_hashes.items():
            required_raw['runtime/' + (logs / name).relative_to(root).as_posix()] = sha
        flags += [{'key': key, **flag} for flag in scan(raw)['flags']]
        rows.append({'key': key, 'accepted': record['accepted'], 'normal_completion': normal,
                     'normal_tests': grade['modes']['normal']['tests_ran'],
                     'optimized_tests': grade['modes']['optimized']['tests_ran'],
                     'full_body_output': observation['body_visible'], 'usage_complete': record['usage_complete'],
                     'timeout': record['timeout'], 'stream_errors': record['stream_errors']})
    require(len(set(threads)) == len(threads) == 12 and all(threads), 'Reused or missing native thread')
    gate = all(r['accepted'] and r['normal_completion'] and r['full_body_output'] for r in rows)
    require(gate == summary['development_gate_passed'], 'Frozen gate and actual evidence disagree')
    for field, value in [('native_attempts', 12), ('contract_accepted', sum(r['accepted'] for r in rows)),
                         ('normal_completions', sum(r['normal_completion'] for r in rows)),
                         ('full_body_outputs', sum(r['full_body_output'] for r in rows)),
                         ('usage_complete', sum(r['usage_complete'] for r in rows))]:
        require(summary[field] == value, 'Summary differs: ' + field)
    require(not list((PROJECT / '.local/production-runtime/generalization-v6-2026-10-27/runs').glob('**/started.json')), 'Fresh tasks were started')
    return {'frozen_gate_passed': gate, 'native_attempts': 12, 'raw_and_grade_checks_complete': True,
            'unique_native_threads': 12, 'normal_and_optimized_grade_logs': 24,
            'trace_flags_requiring_context_review': flags, 'old_failures_preserved': True,
            'fresh_tasks_remain_sealed': True, 'attempts': rows, 'required_raw_sha256': required_raw,
            'checker_sha256': runner.digest(Path(__file__)), 'reused_checker_sha256': runner.digest(Path(legacy.__file__)),
            'no_model_or_grading_calls': True}


def retain(report):
    target = PROJECT / '.local/environment-revalidation-v2-evidence.zip'
    require(not target.exists(), 'Do not overwrite evidence archive')
    sources = {}
    for prefix, folder in [('runtime', ROOT), ('public', HERE)]:
        for path in sorted(folder.rglob('*')):
            if path.is_file() and not {'.git', '__pycache__'} & set(path.relative_to(folder).parts):
                sources[prefix + '/' + path.relative_to(folder).as_posix()] = path
    index = {name: runner.digest(path) for name, path in sources.items()}
    for name, sha in report['required_raw_sha256'].items():
        require(index.get(name) == sha, 'Actual raw evidence omitted: ' + name)
    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, path in sources.items():
            archive.write(path, name)
        archive.writestr('entry-sha256.json', json.dumps(index, indent=2))
    with zipfile.ZipFile(target) as archive:
        require(archive.testzip() is None and set(archive.namelist()) == set(index) | {'entry-sha256.json'}, 'Archive CRC or inventory differs')
        require(all(hashlib.sha256(archive.read(n)).hexdigest().upper() == sha for n, sha in index.items()), 'Archive bytes differ')
    runner.write(HERE / 'results/evidence-retention.json', {'retained_at': runner.utc(), 'private_archive': target.relative_to(PROJECT).as_posix(),
                 'archive_sha256': runner.digest(target), 'bytes': target.stat().st_size, 'entries': len(index) + 1,
                 'all_actual_native_and_grade_logs_retained': True, 'CRC_and_all_entry_hashes_verified': True,
                 'external_publication': False, 'exclusions': ['workspace .git internals', '__pycache__'],
                 'limits': 'Native emitted events do not attest every OS-level access; earlier failed windows and long diagnostics retain their separate bound archives.'})


if __name__ == '__main__':
    report = verify()
    # A nonempty flag list needs actual contextual decisions before any clean
    # scope claim; writing a blanket benign decision here would hide evidence.
    runner.write(HERE / 'results/verification.json', report)
    retain(report)
    print(json.dumps({k:v for k,v in report.items() if k not in {'attempts', 'required_raw_sha256'}}))

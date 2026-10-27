"""Bounded v7 development only, reusing the frozen native client and grader."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import zipfile

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent.parent
for folder in ('production-2026-10-25', 'runtime-2026-10-27', 'generalization-2026-10-26'):
    sys.path.insert(0, str(HERE.parent / folder))
import runner
import resource_gate
import mxc_proxy_transport
import qualify_beta as legacy
from observe import observe
from audit_production_trace import scan
from export_results import FIELDS, public_value

ROOT = PROJECT / '.local/production-runtime/dev-v7-2026-10-28'
SOURCE = HERE / 'candidates/v7-python'
SKILL = ROOT / 'skill-input/SKILL.md'
TRANSPORT = mxc_proxy_transport.install(runner)
PHASES = [('known-new', ('d13', 'd14'), 3), ('known-prior', ('d09', 'd10', 'd11', 'd12'), 1)]
legacy.ROOT = ROOT


def require(condition, message):
    if not condition:
        raise ValueError(message)


def immutable(path, value):
    if path.exists():
        require(runner.read(path) == value, 'Frozen or closed artifact changed: ' + str(path))
    else:
        runner.write(path, value)


def grading_sources(directory, hidden):
    """Bind the exact source-selection contract of the reused synthetic grader."""
    excluded = {'.git', '.agents', '.benchmark-deps', '.venv', 'venv', '__pycache__', '.pytest_cache'}
    result = {}
    for path in directory.rglob('*'):
        relative = path.relative_to(directory)
        if any(part in excluded or part.startswith(('.pytest-', '.test-tmp')) for part in relative.parts):
            continue
        require(not path.is_symlink() and not path.is_junction(), 'Source links are outside the grading contract')
        if not path.is_file():
            continue
        if (relative.as_posix() in hidden or any(part in {'tests', 'test', 'unittest'} for part in relative.parts)
                or path.name.startswith('test_') or path.name.endswith('_test.py')
                or path.name in {'conftest.py', 'sitecustomize.py', 'usercustomize.py', 'unittest.py',
                                 'pytest.ini', '.pytest.ini', 'tox.ini', 'setup.cfg', 'pyproject.toml'}
                or path.suffix == '.pth'):
            continue
        result[relative.as_posix()] = runner.digest(path)
    return result


def verify_mode_log(log, grade):
    text = log.read_text(encoding='utf-8')
    counts = re.findall(r'^Ran (\d+) tests? in [0-9.]+s$', text, re.M)
    require((int(counts[-1]) if counts else 0) == grade['tests_ran'], 'Grade count differs from original log')
    summaries = re.findall(r'^(OK|FAILED)(?: \(([^\n]+)\))?\s*$', text, re.M)
    terminal, details = summaries[-1] if summaries else ('', '')
    for field, name in [('failed', 'failures'), ('errors', 'errors'), ('skipped', 'skipped')]:
        found = re.search(r'\b' + name + r'=(\d+)', details)
        require(grade[field] == (int(found.group(1)) if found else 0), 'Grade/log outcome differs: ' + field)
    if grade['exit_code'] == 0:
        require(terminal == 'OK' and grade['tests_ran'] > 0 and not grade['timeout'], 'Successful grade needs original OK terminal')
    else:
        require(grade['timeout'] or terminal != 'OK', 'Failed grade contradicts original OK terminal')


def cases():
    return {name: runner.read((HERE if name in ('d13', 'd14') else HERE.parent / 'development-2026-10-27') / 'cases' / (name + '.json'))
            for _, names, _ in PHASES for name in names}


def freeze():
    require(not list((PROJECT / '.local/production-runtime').glob('*/active-suite.lock')), 'Another suite is active')
    require(not resource_gate.STOP.exists(), 'Provider resource stop is active')
    require(runner.transport.settings() == runner.SETTINGS, 'Configured model must stay unchanged')
    old = runner.read(HERE.parent / 'generalization-2026-10-27/results/freeze.json')
    require(old['transport'] == TRANSPORT, 'Use the already verified route and MXC mode')
    require(runner.digest(runner.CODEX) == old['cli_sha256'] and runner.digest(Path(sys.executable)) == old['python_sha256'], 'Do not silently change the native runtime')
    protocol = runner.read(HERE / 'protocol.json')
    require(runner.digest(HERE.parent / 'generalization-2026-10-27/results/beta-qualification.json') == protocol['closed_v6_qualification_sha256'], 'Original failed v6 decision must stay unchanged')
    require(protocol['max_native_attempts'] == 10 and protocol['new_known_cases'] == ['d13', 'd14']
            and protocol['prior_known_cases'] == ['d09', 'd10', 'd11', 'd12']
            and protocol['new_known_repeats'] == 3 and protocol['prior_known_repeats'] == 1
            and protocol['max_concurrent_native_clients'] == 3 and protocol['arms'] == ['with-skill'], 'Protocol and implementation differ')
    for name in ('SKILL.md', 'agents/openai.yaml'):
        target = ROOT / 'skill-input' / name
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(SOURCE / name, target)
        require(runner.digest(target) == runner.digest(SOURCE / name), 'Candidate changed')
    selected = cases()
    for case in selected.values():
        check = runner.preflight_case(case, ROOT)
        require(all(check['base']['modes'][m]['exit_code'] != 0 for m in ('normal', 'optimized')), 'Initial failure must be observable in both modes')
    controls = {}
    for name, neutral in [('d13', 'case-08'), ('d14', 'case-05')]:
        path = ROOT / 'rollback-controls' / name
        if not path.exists():
            shutil.copytree(PROJECT / '.local/v6-artifact-reviews' / neutral / 'base/src', path / 'src')
        grade = runner.synthetic_grade(selected[name], path, ROOT / 'rollback-grades' / name)
        require(all(m['exit_code'] != 0 and m['tests_ran'] > 0 and not m['timeout'] for m in grade['modes'].values()), 'Rollback must fail: ' + name)
        controls[name] = {'rollback_rejected': True,
                          'normal_tests': grade['modes']['normal']['tests_ran'],
                          'optimized_tests': grade['modes']['optimized']['tests_ran']}
    sources = [Path(__file__), HERE / 'protocol.json', Path(runner.__file__), Path(runner.transport.__file__),
               Path(runner.upstream_grader.__file__), Path(resource_gate.__file__), Path(mxc_proxy_transport.__file__),
               HERE.parent / 'generalization-2026-10-26/observe.py', Path(legacy.__file__),
               HERE.parent / 'production-2026-10-25/export_results.py',
               HERE.parent / 'production-2026-10-25/audit_production_trace.py']
    sources += [HERE / 'cases' / (n + '.json') for n in ('d13', 'd14')]
    sources += [HERE.parent / 'development-2026-10-27/cases' / (n + '.json') for n in ('d09', 'd10', 'd11', 'd12')]
    closed = HERE.parent / 'generalization-2026-10-27/results/beta-qualification.json'
    frozen = {'evidence_class': 'known_revealed_development_only', 'planned_native_attempts': 10,
              'skill_sha256': runner.digest(SKILL), 'ui_sha256': runner.digest(SKILL.parent / 'agents/openai.yaml'),
              'settings': runner.SETTINGS, 'transport': TRANSPORT,
              'cli_sha256': runner.digest(runner.CODEX), 'python_sha256': runner.digest(Path(sys.executable)),
              'source_sha256': {p.relative_to(PROJECT).as_posix(): runner.digest(p) for p in sources},
              'cases': {n: runner.case_digest(c) for n, c in selected.items()},
              'schedule': {phase: runner.schedule([selected[n] for n in names], phase, repeat, ('with-skill',)) for phase, names, repeat in PHASES},
              'closed_v6_qualification_sha256': runner.digest(closed), 'rollback_controls': controls}
    immutable(HERE / 'results/freeze.json', frozen)
    if not (HERE / 'results/frozen-at.json').exists():
        require(not list((ROOT / 'runs').glob('*/*/started.json')), 'Do not backdate a freeze')
        runner.write(HERE / 'results/frozen-at.json', {'frozen_at': runner.utc(), 'native_attempts': 0, 'freeze_sha256': runner.digest(HERE / 'results/freeze.json')})
    return selected


def run():
    selected = freeze()
    resource_gate.install()
    for phase, names, repeats in PHASES:
        try:
            runner.run_suite(ROOT, [selected[n] for n in names], phase, SKILL, repeats, ('with-skill',), 3)
        except RuntimeError:
            if resource_gate.STOP.exists() or not (ROOT / (phase + '-failures.json')).exists():
                raise
            print(json.dumps({'phase': phase, 'status': 'incomplete_checkpoint_preserved'}), flush=True)


def close():
    frozen = runner.read(HERE / 'results/freeze.json')
    for name, expected in frozen['source_sha256'].items():
        require(runner.digest(PROJECT / name) == expected, 'Frozen source changed: ' + name)
    require(runner.digest(SKILL) == frozen['skill_sha256'] == runner.digest(SOURCE / 'SKILL.md'), 'Frozen skill changed')
    require(runner.digest(runner.CODEX) == frozen['cli_sha256'] and runner.digest(Path(sys.executable)) == frozen['python_sha256'], 'Runtime changed')
    require(runner.digest(HERE.parent / 'generalization-2026-10-27/results/beta-qualification.json') == frozen['closed_v6_qualification_sha256'], 'Closed v6 result changed')
    selected = cases()
    keys = {f'{phase}--{name}--r{repeat}--with-skill' for phase, names, repeats in PHASES for name in names for repeat in range(repeats)}
    starts = sorted((ROOT / 'runs').glob('*/*/started.json'))
    actual_keys = {p.parent.name for p in starts}
    require(not actual_keys - keys and len(starts) == len(actual_keys), 'Unexpected or duplicate native slots')
    missing = [p.parent.name for p in starts if not (p.parent / 'record.json').exists()]
    if missing or keys - actual_keys:
        runner.write(HERE / 'results/incomplete-result.json', {'development_gate_passed': False, 'missing_records': missing, 'not_attempted': sorted(keys - actual_keys), 'planned_denominator': 10})
        raise ValueError('Incomplete original checkpoints require grade-only recovery')
    rows, flags, thread_ids = [], [], []
    for start in starts:
        logs = start.parent
        record = runner.read(logs / 'record.json')
        case = selected[record['case_id']]
        require(record['key'] == logs.name == f"{record['phase']}--{case['id']}--r{record['repeat']}--with-skill", 'Native slot identity differs')
        require(record['case_sha256'] == frozen['cases'][case['id']] == runner.case_digest(case), 'Case changed')
        manifest = runner.read(ROOT / (record['phase'] + '-manifest.json'))
        require(manifest['schedule'] == frozen['schedule'][record['phase']] and manifest['workers'] == 3, 'Dispatch changed')
        started = runner.read(start)
        require(started['started_at'] >= runner.read(HERE / 'results/frozen-at.json')['frozen_at'], 'Attempt predates registration')
        workspace = Path(record['workspace'])
        require(started['command'] == mxc_proxy_transport.build_command(runner, workspace, logs, runner.environment(workspace)) and started['transport_environment'] == TRANSPORT['environment'], 'Transport changed')
        legacy.verify_record(record, started, runner.read(logs / 'client-done.json'), runner.read(logs / 'cli-checkpoint.json'), case, logs, SKILL, frozen)
        source_hashes = grading_sources(workspace, case['hidden_tests'])
        grade_dir = ROOT / 'grades' / record['phase'] / record['key']
        require(source_hashes == grading_sources(grade_dir, case['hidden_tests']), 'Final artifact differs from actually graded sources')
        hidden_hashes = {}
        for name, text in case['hidden_tests'].items():
            expected = hashlib.sha256(text.replace('\n', os.linesep).encode('utf-8')).hexdigest().upper()
            require(runner.digest(grade_dir / name) == expected, 'Restored hidden test differs from frozen fixture')
            hidden_hashes[name] = expected
        raw = (logs / 'events.jsonl').read_text(encoding='utf-8')
        observation = observe(raw, SKILL.read_text(encoding='utf-8'))
        require(not observation['malformed_lines'] and len(observation['thread_ids']) <= 1, 'Invalid events')
        require(all(isinstance(t, str) and bool(t) for t in observation['thread_ids']), 'Native thread identity must be a nonempty string')
        thread_ids += observation['thread_ids']
        events = list(map(json.loads, raw.splitlines()))
        terminals = [e['type'] for e in events if e['type'] in ('turn.completed', 'turn.failed')]
        normal = bool(record['cli_exit_code'] == 0 and not record['timeout'] and terminals[-1:] == ['turn.completed'])
        require(not normal or len(observation['thread_ids']) == 1, 'Successful client needs a thread')
        completed = [e for e in events if e['type'] == 'turn.completed']
        usage = completed[-1].get('usage') if completed else None
        verified_usage = bool(record['usage_complete'] and len(completed) == 1 and isinstance(usage, dict)
                              and all(type(usage.get(k)) is int and usage[k] >= 0 for k in ('input_tokens', 'cached_input_tokens', 'output_tokens'))
                              and usage['cached_input_tokens'] <= usage['input_tokens'])
        for mode, grade in record['grade']['modes'].items():
            log = ROOT / 'grades' / record['phase'] / (record['key'] + '-' + mode + '-tests.log')
            verify_mode_log(log, grade)
        flags += [{'key': record['key'], **f} for f in scan(raw)['flags']]
        patch = HERE / 'results/patches' / (record['key'] + '.patch')
        patch.parent.mkdir(parents=True, exist_ok=True)
        patch.write_text(runner.git(workspace, 'diff', '--no-ext-diff', '--binary', 'HEAD'), encoding='utf-8')
        row = {k: record.get(k) for k in FIELDS}
        row.update(normal_completion=normal, full_body_output=observation['body_visible'],
                   verified_usage_complete=verified_usage,
                   end_to_end_success=bool(record['accepted'] and normal and observation['body_visible'] and verified_usage),
                   graded_final_source_sha256=source_hashes,
                   restored_hidden_tests_sha256=hidden_hashes,
                   raw_artifact_sha256={p.name: runner.digest(p) for p in logs.iterdir() if p.is_file()}, patch=patch.relative_to(HERE / 'results').as_posix(), patch_sha256=runner.digest(patch))
        rows.append(public_value(row))
    require(len(thread_ids) == len(set(thread_ids)), 'Reused native threads')
    runner.write(PROJECT / '.local/v7-actual-trace-flags.json', flags)
    decisions_path = PROJECT / '.local/v7-trace-decisions.json'
    decisions = runner.read(decisions_path) if decisions_path.exists() else []
    require({legacy.flag_id(f) for f in flags} == {r['flag_id'] for r in decisions}, 'Review every actual trace flag in context')
    require(all(r['completed'] and r['reason'] and not r['confirmed_scope_violation'] for r in decisions), 'Unresolved scope review')
    review = runner.read(HERE / 'results/artifact-review.json')
    require(review['reviewed_keys'] == sorted(k for k in keys if k.startswith('known-new--')) and review['completed'] is True, 'Review all6 original new-case artifacts')
    (HERE / 'results/runs.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows), encoding='utf-8')
    runner.write(HERE / 'results/trace-adjudication.json', {'decisions': decisions, 'confirmed_scope_violations': 0, 'limits': 'Emitted trace review only, not OS-level access attestation'})
    evidence = PROJECT / '.local/v7-development-evidence.zip'
    require(not evidence.exists(), 'Never overwrite retained evidence')
    files = {}
    for base, prefix in [(ROOT, 'runtime'), (HERE, 'public')]:
        for path in sorted(base.rglob('*')):
            if path.is_file() and not any(n in {'.git', '__pycache__', '.pytest_cache', '.benchmark-deps'} for n in path.relative_to(base).parts):
                require(not path.is_symlink() and not path.is_junction(), 'Archive input must be a real file')
                files[prefix + '/' + path.relative_to(base).as_posix()] = path
    hashes = {n: runner.digest(p) for n, p in files.items()}
    with zipfile.ZipFile(evidence, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, path in files.items():
            archive.write(path, name)
        archive.writestr('entry-sha256.json', json.dumps(hashes, indent=2))
    with zipfile.ZipFile(evidence) as archive:
        require(archive.testzip() is None and all(hashlib.sha256(archive.read(n)).hexdigest().upper() == h for n, h in hashes.items()), 'Archive CRC/hash failure')
    retention = {'private_archive': evidence.relative_to(PROJECT).as_posix(), 'sha256': runner.digest(evidence), 'entries': len(hashes) + 1, 'bytes': evidence.stat().st_size, 'CRC_and_all_entry_hashes_verified': True, 'includes_final_workspaces_and_untracked_files': True, 'excluded': ['.git', '__pycache__', '.pytest_cache', '.benchmark-deps']}
    runner.write(HERE / 'results/evidence-retention.json', retention)
    summary = {'completed_at': runner.utc(), 'evidence_class': 'known_revealed_development_only', 'attempts': len(rows),
               'contract_accepted': sum(r['accepted'] for r in rows), 'end_to_end_success': sum(r['end_to_end_success'] for r in rows),
               'normal_completions': sum(r['normal_completion'] for r in rows), 'full_body_outputs': sum(r['full_body_output'] for r in rows), 'complete_usage': sum(r['verified_usage_complete'] for r in rows),
               'cases': {n: {'attempts': sum(r['case_id'] == n for r in rows), 'end_to_end_success': sum(r['case_id'] == n and r['end_to_end_success'] for r in rows)} for n in selected},
               'development_gate_passed': len(rows) == 10 and all(r['end_to_end_success'] for r in rows) and not review['confirmed_findings'],
               'confirmed_artifact_findings': review['confirmed_findings'], 'freeze_sha256': runner.digest(HERE / 'results/freeze.json'), 'archive_sha256': retention['sha256'],
               'beta_quality_gate_passed': False, 'production_assistant_qualified': False, 'stable_efficiency_benefit_proven': False, 'comparative_efficiency_assessed': False, 'agensi_submitted': False, 'closed_v6_failure_preserved': True}
    immutable(HERE / 'results/summary.json', summary)
    print(json.dumps(summary))


if __name__ == '__main__':
    require(__debug__, 'Controller must run without -O; child -O grading is separate')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'run', 'close'))
    action = parser.parse_args().action
    if action == 'prepare':
        freeze()
        print(json.dumps({'prepared': True, 'planned_native': 10, 'new_native_calls': 0}))
    elif action == 'run':
        run()
    else:
        close()

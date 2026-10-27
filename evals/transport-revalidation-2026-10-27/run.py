"""New known-contract execution window after a verified proxy-route correction."""
from pathlib import Path
import argparse
import json
import shutil
import sys

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent.parent
OLD = HERE.parent / 'production-2026-10-25'
sys.path.insert(0, str(OLD))
sys.path.insert(0, str(HERE.parent / 'runtime-2026-10-27'))
sys.path.insert(0, str(HERE.parent / 'generalization-2026-10-26'))
import runner
import resource_gate
import proxy_transport
from observe import observe
from export_results import FIELDS, public_value
from audit_production_trace import scan

ROOT = PROJECT / '.local/production-runtime/v6-proxy-revalidation'
CASE_DIR = HERE.parent / 'development-2026-10-27/cases'
CANDIDATE = HERE.parent / 'development-2026-10-27/candidates/v6-python'
TRANSPORT = proxy_transport.install(runner)
SKILL = ROOT / 'skill-input/SKILL.md'


def require(value, message):
    if not value:
        raise ValueError(message)


def freeze():
    require(not list((PROJECT / '.local/production-runtime').glob('*/active-suite.lock')), 'Another native suite is active')
    require(not resource_gate.STOP.exists(), 'Provider resource stop is active')
    require(runner.transport.settings() == runner.SETTINGS, 'Do not change model/settings')
    cases = [runner.read(CASE_DIR / (name + '.json')) for name in ('d09', 'd10', 'd11', 'd12')]
    source = CANDIDATE
    for name in ('SKILL.md', 'agents/openai.yaml'):
        destination = ROOT / 'skill-input' / name
        if not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / name, destination)
        require(runner.digest(destination) == runner.digest(source / name), 'Frozen candidate bytes changed')
    for case in cases:
        runner.preflight_case(case, ROOT)
    controls = {}
    for case in cases:
        if 'rollback_files' not in case:
            continue
        path = ROOT / 'rollback-controls' / case['id']
        if not path.exists():
            path.mkdir(parents=True)
            runner.put_files(path, case['rollback_files'])
        grade = runner.synthetic_grade(case, path, ROOT / 'rollback-grades' / case['id'])
        require(all(mode['exit_code'] != 0 and mode['tests_ran'] > 0 for mode in grade['modes'].values()), 'Direct rollback must fail the original maintenance requirement')
        controls[case['id']] = {'direct_rollback_rejected': True,
                                'normal_tests': grade['modes']['normal']['tests_ran'],
                                'optimized_tests': grade['modes']['optimized']['tests_ran']}
    bindings = [Path(__file__), Path(proxy_transport.__file__), Path(runner.__file__), Path(runner.transport.__file__),
                Path(runner.upstream_grader.__file__), Path(resource_gate.__file__),
                HERE.parent / 'generalization-2026-10-26/observe.py']
    frozen = {
        'evidence_class': 'known_contract_revalidation_after_transport_change', 'transport': TRANSPORT, 'planned_native_attempts': 12,
        'cases': {c['id']: runner.case_digest(c) for c in cases},
        'fixture_files_sha256': {p.name: runner.digest(p) for p in sorted((CASE_DIR).glob('*.json'))},
        'skill_sha256': runner.digest(SKILL), 'ui_sha256': runner.digest(SKILL.parent / 'agents/openai.yaml'),
        'source_sha256': {p.relative_to(PROJECT).as_posix(): runner.digest(p) for p in bindings},
        'settings': runner.SETTINGS, 'python_version': sys.version.split()[0],
        'python_sha256': runner.digest(Path(sys.executable)), 'cli_sha256': runner.digest(runner.CODEX),
        'original_failed_v6_summary_sha256': runner.digest(HERE.parent / 'development-2026-10-27/results/summary.json'),
        'original_recovery_result_sha256': runner.digest(HERE.parent / 'development-2026-10-27/results/recovery-result.json'),
        'connection_canary_sha256': runner.digest(PROJECT / '.local/native-proxy-canary-2026-10-27/result.json'),
        'closed_v5_qualification_sha256': runner.digest(HERE.parent / 'generalization-2026-10-26/results/beta-qualification.json'),
        'rollback_negative_controls': controls,
        'gate': 'All12 saved grades pass, one unique native thread per attempt, CLI success/turn.completed without timeout, full rule output, frozen bytes unchanged; no efficiency or fresh-task claim'
    }
    frozen_path = HERE / 'results/freeze.json'
    if frozen_path.exists():
        require(runner.read(frozen_path) == frozen, 'Development freeze changed')
    else:
        runner.write(frozen_path, frozen)
        runner.write(HERE / 'results/frozen-at.json', {'frozen_at': runner.utc(), 'native_attempts': 0,
                     'freeze_sha256': runner.digest(frozen_path)})
    return cases, frozen


def summarize(records, frozen):
    rows, threads, flags = [], [], []
    for record in records:
        key = record['key']
        logs = ROOT / 'runs/development' / key
        raw = (logs / 'events.jsonl').read_text(encoding='utf-8')
        observation = observe(raw, SKILL.read_text(encoding='utf-8'))
        require(len(observation['thread_ids']) == 1 and bool(observation['thread_ids'][0]), 'A native attempt needs one thread')
        require(not observation['malformed_lines'], 'Malformed trace')
        threads += observation['thread_ids']
        flags.extend({'key': key, **flag} for flag in scan(raw)['flags'])
        events = [json.loads(line) for line in raw.splitlines() if line.strip()]
        terminal = [e['type'] for e in events if e['type'] in ('turn.completed', 'turn.failed')]
        success = record['cli_exit_code'] == 0 and not record['timeout'] and bool(terminal and terminal[-1] == 'turn.completed')
        row = {name: record.get(name) for name in FIELDS}
        row.update(full_body_observation=observation, successful_native_completion=success,
                   raw_artifact_sha256={p.name: runner.digest(p) for p in sorted(logs.iterdir()) if p.is_file()})
        patch = HERE / 'results/patches' / (key + '.patch')
        patch.parent.mkdir(parents=True, exist_ok=True)
        patch.write_text(runner.git(Path(record['workspace']), 'diff', '--no-ext-diff', '--binary', 'HEAD'), encoding='utf-8')
        row.update(patch=patch.relative_to(HERE / 'results').as_posix(), patch_sha256=runner.digest(patch))
        rows.append(public_value(row))
    require(len(set(threads)) == len(records) == 12, 'Missing or reused native attempt')
    for name, expected in frozen['source_sha256'].items():
        require(runner.digest(PROJECT / name) == expected, 'Frozen implementation changed')
    require(runner.digest(SKILL) == frozen['skill_sha256'], 'Candidate changed')
    require(runner.digest(HERE.parent / 'generalization-2026-10-26/results/beta-qualification.json') == frozen['closed_v5_qualification_sha256'], 'Closed v5 evidence changed')
    (HERE / 'results/runs.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows), encoding='utf-8')
    runner.write(PROJECT / '.local/v6-proxy-revalidation-trace-flags.json', flags)
    require(runner.digest(HERE.parent / 'development-2026-10-27/results/summary.json') == frozen['original_failed_v6_summary_sha256'], 'Original failed development result changed')
    require(runner.digest(HERE.parent / 'development-2026-10-27/results/recovery-result.json') == frozen['original_recovery_result_sha256'], 'Original failed recovery result changed')
    summary = {'completed_at': runner.utc(), 'evidence_class': 'known_contract_revalidation_after_transport_change',
               'native_attempts': len(records), 'contract_accepted': sum(r['accepted'] for r in records),
               'normal_completions': sum(r['successful_native_completion'] for r in rows),
               'full_body_outputs': sum(r['full_body_observation']['body_visible'] for r in rows),
               'usage_complete': sum(r['usage_complete'] for r in rows),
               'potential_trace_flags': len(flags), 'closed_v5_failure_preserved': True, 'original_v6_failures_preserved': True, 'transport': TRANSPORT,
               'development_gate_passed': all(r['accepted'] and r['successful_native_completion'] and r['full_body_observation']['body_visible'] for r in rows),
               'no_fresh_generalization_efficiency_or_production_claim': True}
    runner.write(HERE / 'results/summary.json', summary)
    print(json.dumps(summary), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare-only', action='store_true')
    arguments = parser.parse_args()
    cases, frozen = freeze()
    if arguments.prepare_only:
        print(json.dumps({'prepared': True, 'native_calls': 0, 'planned': 12}))
    else:
        resource_gate.install()
        summarize(runner.run_suite(ROOT, cases, 'development', SKILL, 3, ('with-skill',), 3), frozen)

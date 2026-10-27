"""Verify prospectively frozen v6 quality evidence; no model/grade reruns."""
from pathlib import Path
import hashlib
import json
import sys
import zipfile

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent / 'production-2026-10-25'))
sys.path.insert(0, str(HERE.parent / 'generalization-2026-10-26'))
sys.path.insert(0, str(HERE.parent / 'runtime-2026-10-27'))
import runner
import mxc_proxy_transport
import qualify_beta as legacy
from observe import observe
from audit_production_trace import scan
from incomplete import describe

ROOT = PROJECT / '.local/production-runtime/generalization-v6-2026-10-27'
legacy.ROOT = ROOT  # Reuse the bound identity/raw-event/grade checker with this batch root.
require, flag_id = legacy.require, legacy.flag_id


def verify_modes(record, case):
    if 'upstream' in case:
        parsed = runner.upstream_grader.junit_summary(Path(record['grade']['junit_path']))
        for field in ('tests_collected', 'tests_ran', 'passed', 'failed', 'errors', 'skipped'):
            require(parsed[field] == record['grade'][field], 'Grade differs from original JUnit: ' + field)
        return
    grade = record['grade']
    require(set(grade.get('modes', {})) == {'normal', 'optimized'}, 'Critical normal/-O modes missing')
    expected = runner.read(ROOT / 'preflight' / (case['id'] + '.json'))['expected']
    for mode in ('normal', 'optimized'):
        if record['accepted']:
            require(grade['modes'][mode]['tests_ran'] == expected['modes'][mode]['tests_ran'] > 0,
                    'Accepted critical mode test count changed')
    for field in ('tests_collected', 'tests_ran', 'passed', 'failed', 'errors', 'skipped'):
        require(grade[field] == sum(grade['modes'][mode][field] for mode in ('normal', 'optimized')),
                'Critical aggregate contradicts mode counts')


def verify_reviews(frozen):
    directory = PROJECT / '.local/v6-artifact-reviews'
    mapping = {f'case-{i:02}': selected for i, selected in enumerate(frozen['independent_review'], 1)}
    reports = {}
    for name in ('review-a.json', 'review-b.json'):
        path = directory / name
        report = runner.read(path)
        for case in report['cases']:
            key = case['case_id']
            require(key in mapping and key not in reports, 'Review must cover each preselected artifact exactly once')
            require(case['completed'] is True, 'Review is incomplete')
            require(case['status'] in {'pass', 'pass_with_scope_note', 'needs_changes', 'unresolved'}, 'Review status missing')
            neutral = directory / key
            manifest_path = neutral / 'input-integrity.json'
            require(runner.digest(manifest_path) == case['input_manifest_sha256'], 'Reviewer input binding differs')
            manifest = runner.read(manifest_path)
            require(bool(manifest) and 'request.txt' in manifest and any(n.startswith('base/') for n in manifest)
                    and any(n.startswith('candidate/') for n in manifest), 'Both nonempty review trees required')
            actual = {p.relative_to(neutral).as_posix() for p in neutral.rglob('*') if p.is_file() and p.name != 'input-integrity.json'}
            require(actual == set(manifest), 'Review input inventory changed')
            selected = mapping[key]
            source_case = runner.read(ROOT / 'fresh-cases' / (selected['case_id'] + '.json'))
            workspace = ROOT / 'work/generalization' / selected['case_id'] / 'r0/with-skill'
            require((neutral / 'request.txt').read_text(encoding='utf-8') == source_case['task'], 'Review task differs')
            for name, expected in manifest.items():
                require(runner.digest(neutral / name) == expected, 'Review input bytes changed')
                if name.startswith('candidate/'):
                    require(runner.digest(workspace / name.removeprefix('candidate/')) == expected, 'Review candidate differs from fixed r0')
                elif name.startswith('base/'):
                    require(runner.digest(Path(source_case['source_dir']) / name.removeprefix('base/')) == expected, 'Review base differs')
            require(isinstance(case['confirmed_findings'], list) and isinstance(case['unresolved'], list), 'Review findings schema missing')
            reports[key] = {'source_report': path.name, 'source_report_sha256': runner.digest(path), **case, **selected}
    require(set(reports) == set(mapping), 'Eight unique review outputs required')
    defects = sum(len(row['confirmed_findings']) for row in reports.values())
    passing = all(row['status'] in {'pass', 'pass_with_scope_note'} and not row['confirmed_findings'] for row in reports.values())
    return {'completed': 8, 'confirmed_defects': defects, 'all_passed': passing,
            'reviews': [reports[key] for key in sorted(reports)]}


def verify_retention(required):
    report = runner.read(HERE / 'results/evidence-retention.json')
    path = PROJECT / report['private_archive']
    require(runner.digest(path) == report['archive_sha256'], 'Retained archive changed')
    with zipfile.ZipFile(path) as archive:
        require(archive.testzip() is None, 'Archive CRC failed')
        index = json.loads(archive.read('entry-sha256.json'))
        require(set(archive.namelist()) == set(index) | {'entry-sha256.json'}, 'Archive index incomplete')
        for name, expected in index.items():
            require(hashlib.sha256(archive.read(name)).hexdigest().upper() == expected, 'Archive entry bytes differ')
        require(bool(required), 'Actual evidence inventory must not be empty')
        for name, expected in required.items():
            require(index.get(name) == expected, 'Required actual evidence missing from archive: ' + name)
    return report


def verify_package(frozen):
    report = runner.read(HERE / 'results/package-validation.json')
    path = PROJECT / report['archive']
    require(runner.digest(path) == report['archive_sha256'], 'Checked package changed')
    required = {'SKILL.md', 'agents/openai.yaml', 'LICENSE', 'NOTICE.txt'}
    require(set(report['files_sha256']) == required, 'Package file inventory differs')
    with zipfile.ZipFile(path) as archive:
        require(archive.testzip() is None and set(archive.namelist()) == {'lean-coding/' + n for n in required}, 'Package entries/CRC differ')
        for name, expected in report['files_sha256'].items():
            require(hashlib.sha256(archive.read('lean-coding/' + name)).hexdigest().upper() == expected, 'Package bytes differ')
        require(report['files_sha256']['SKILL.md'] == frozen['skill_sha256'], 'Rules differ from tested candidate')
        require(report['files_sha256']['agents/openai.yaml'] == frozen['ui_sha256'], 'Frozen UI missing or changed')
        require(report['files_sha256']['LICENSE'] == frozen['license_sha256'], 'Frozen license differs')
    require(report['install_and_rollback_verified'] is True, 'Install/rollback not checked')
    return report


def qualify():
    frozen = runner.read(HERE / 'results/freeze.json')
    protocol = runner.read(HERE / 'protocol.json')
    require(runner.digest(HERE / 'protocol.json') == frozen['protocol_sha256'], 'Protocol changed')
    for name, expected in frozen['source_closure_sha256'].items():
        require(runner.digest(PROJECT / name) == expected, 'Frozen implementation changed: ' + name)
    require(runner.digest(PROJECT / 'LICENSE') == frozen['license_sha256'], 'License changed')
    readiness = HERE.parent / 'transport-revalidation-mxc-2026-10-27/results'
    require(runner.digest(readiness / 'summary.json') == frozen['readiness_summary_sha256'], 'Transport readiness result changed')
    require(runner.digest(readiness / 'freeze.json') == frozen['readiness_freeze_sha256'], 'Transport readiness freeze changed')
    ready = runner.read(readiness / 'summary.json')
    require(ready['development_gate_passed'] is True and ready['transport'] == frozen['transport'], 'Transport readiness gate failed or route differs')
    require(runner.read(readiness / 'freeze.json')['transport'] == frozen['transport'], 'Frozen readiness route differs')
    for name, field in [('summary.json', 'original_failed_v6_summary_sha256'), ('recovery-result.json', 'original_recovery_result_sha256')]:
        require(runner.digest(HERE.parent / 'development-2026-10-27/results' / name) == frozen[field], 'Original failed v6 evidence changed')
    require(runner.digest(HERE.parent / 'transport-revalidation-2026-10-27/results/interrupted-summary.json') == frozen['stopped_proxy_only_window_sha256'], 'Stopped proxy-only window changed')
    skill = ROOT / 'skill-input/SKILL.md'
    require(runner.digest(skill) == frozen['skill_sha256'], 'Frozen rule changed')
    incomplete = describe(ROOT, frozen, runner)
    if incomplete:
        report = {'evaluated_at': runner.utc(), 'candidate': 'v6-python', 'skill_sha256': frozen['skill_sha256'],
                  'beta_quality_gate_passed': False, 'production_assistant_qualified': False,
                  'evidence_integrity_complete': False, 'planned_fresh_denominator': 39,
                  'planned_known_critical_denominator': 12, 'incomplete_attempts': incomplete,
                  'no_replacement_model_calls_or_imputed_usage': True,
                  'note': 'Incomplete records prevent qualification; existing raw evidence and the full planned denominator are preserved. Grade-only recovery may resolve completed-client checkpoints.'}
        runner.write(HERE / 'results/incomplete-verdict.json', report)
        print(json.dumps(report))
        return
    keys = {f'{phase}--{key}--r{repeat}--with-skill': (phase, key, repeat)
            for phase, tasks, repeats in [('generalization', frozen['fresh'], 3), ('known-critical', frozen['known_critical'], 1)]
            for key in tasks for repeat in range(repeats)}
    paths = sorted((ROOT / 'runs').glob('*/*/started.json'))
    require(len(paths) == 51 and {p.parent.name for p in paths} == set(keys), 'Missing/extra native attempts must be retained, never replaced')
    records, identities, flags, required_raw = [], [], [], {}
    for path in paths:
        logs, key = path.parent, path.parent.name
        started = runner.read(path)
        require(started['transport_environment'] == frozen['transport']['environment'] and started['windows_sandbox'] == 'mxc', 'Attempt transport differs')
        record = runner.read(logs / 'record.json')
        workspace = Path(record['workspace'])
        require(started['command'] == mxc_proxy_transport.build_command(runner, workspace, logs, runner.environment(workspace)), 'Attempt command differs')
        require((record['phase'], record['case_id'], record['repeat']) == keys[key] and record['key'] == key, 'Attempt identity differs')
        case = runner.read(ROOT / 'fresh-cases' / (record['case_id'] + '.json')) if record['phase'] == 'generalization' else runner.read(
            HERE.parent / 'production-2026-10-25/holdout-sealed-v2' / (record['case_id'] + '.json'))
        require(record['case_sha256'] == runner.case_digest(case) == frozen['case_sha256'][record['case_id']], 'Frozen case changed')
        require(record['skill_sha256'] == frozen['skill_sha256'], 'Attempt used different rules')
        legacy.verify_record(record, runner.read(path), runner.read(logs / 'client-done.json'),
                             runner.read(logs / 'cli-checkpoint.json'), case, logs, skill, frozen)
        verify_modes(record, case)
        frozen_at = runner.read(HERE / 'results/frozen-at.json')['frozen_at']
        require(record['started_at'] >= frozen_at, 'Native attempt predates the input freeze')
        raw = (logs / 'events.jsonl').read_text(encoding='utf-8')
        observation = observe(raw, skill.read_text(encoding='utf-8'))
        require(not observation['malformed_lines'] and len(observation['thread_ids']) <= 1, 'Each attempt permits at most one native thread')
        if observation['thread_ids']:
            require(bool(observation['thread_ids'][0]), 'Empty thread identity')
        else:
            require(record['cli_exit_code'] != 0 or record['timeout'], 'A successful attempt requires a native thread')
        identities += observation['thread_ids']
        terminals = [event['type'] for event in map(json.loads, raw.splitlines()) if event['type'] in {'turn.completed', 'turn.failed'}]
        complete = bool(terminals and terminals[-1] == 'turn.completed' and not record['timeout'] and record['cli_exit_code'] == 0)
        record = {**record, 'end_to_end_success': bool(record['accepted'] and complete and observation['body_visible']),
                  'normal_completion': complete, 'full_body_output': observation['body_visible']}
        records.append(record)
        flags.extend({'key': key, **flag} for flag in scan(raw)['flags'])
        for source in sorted(logs.iterdir()):
            if source.is_file():
                required_raw['runtime/' + source.relative_to(ROOT).as_posix()] = runner.digest(source)
        for field in ('log_path', 'junit_path'):
            if field in record['grade']:
                source = Path(record['grade'][field])
                require(source.resolve().is_relative_to(ROOT.resolve()), 'Grade evidence outside batch')
                if source.is_file():
                    required_raw['runtime/' + source.relative_to(ROOT).as_posix()] = runner.digest(source)
                else:
                    require(not record['accepted'] and field == 'junit_path', 'Accepted grade evidence missing')
        if 'modes' in record['grade']:
            for mode in ('normal', 'optimized'):
                source = ROOT / 'grades/known-critical' / (key + '-' + mode + '-tests.log')
                require(source.is_file(), 'Critical mode log missing')
                required_raw['runtime/' + source.relative_to(ROOT).as_posix()] = runner.digest(source)
    require(len(set(identities)) == len(identities), 'Reused native thread')
    for path in sorted((ROOT / 'fresh-cases').glob('*.json')):
        case = runner.read(path)['upstream']
        for stage, field in [('base', 'base_production_sha256'), ('fix', 'overlay_sha256')]:
            for name, expected in case['preflight'][field].items():
                require(runner.digest(Path(case[stage + '_dir']) / name) == expected.upper(), 'Source snapshot changed')
    for name, expected in frozen['dependency_files_sha256'].items():
        require(runner.digest(ROOT / 'testdeps' / name) == expected, 'Frozen dependency changed')
    for relative, expected in [('generalization-2026-10-26/results/beta-qualification.json', frozen['closed_v5_qualification_sha256']),
                               ('production-2026-10-25/results/v4/release-qualification.json', frozen['closed_v4_qualification_sha256'])]:
        require(runner.digest(HERE.parent / relative) == expected, 'Closed failure evidence changed')
    reviewed_flags = runner.read(HERE / 'results/trace-adjudication.json')
    require(sorted(flag_id(f) for f in flags) == sorted(reviewed_flags['reviewed_flag_ids']), 'Actual trace flag coverage differs')
    require(reviewed_flags['confirmed_scope_violations'] == 0, 'Confirmed scope violation')
    reviews = verify_reviews(frozen)
    retention = verify_retention(required_raw)
    package = verify_package(frozen)
    fresh = [r for r in records if r['phase'] == 'generalization']
    known = [r for r in records if r['phase'] == 'known-critical']
    gates = {
        'fresh_end_to_end_at_least38_of39': len(fresh) == 39 and sum(r['end_to_end_success'] for r in fresh) >= 38,
        'each_fresh_task_at_least2_of3': len(frozen['fresh']) == 13 and all(sum(r['case_id'] == key and r['end_to_end_success'] for r in fresh) >= 2 for key in frozen['fresh']),
        'known_critical_end_to_end12_of12': len(known) == 12 and all(r['end_to_end_success'] for r in known),
        'eight_preselected_reviews_pass': reviews['all_passed'], 'source_trace_archive_package_integrity': True}
    report = {'evaluated_at': runner.utc(), 'candidate': 'v6-python', 'skill_sha256': frozen['skill_sha256'],
              'beta_quality_gate_passed': all(gates.values()), 'gates': gates, 'production_assistant_qualified': False,
              'stable_efficiency_benefit_proven': False, 'comparative_efficiency_assessed': False,
              'fresh_contract_accepted': sum(r['accepted'] for r in fresh),
              'fresh_end_to_end_success': sum(r['end_to_end_success'] for r in fresh),
              'known_critical_end_to_end_success': sum(r['end_to_end_success'] for r in known),
              'normal_completions': sum(r['normal_completion'] for r in records),
              'full_body_outputs': sum(r['full_body_output'] for r in records),
              'complete_usage_records': sum(r['usage_complete'] for r in records),
              'independent_reviews': reviews, 'retention_report_sha256': runner.digest(HERE / 'results/evidence-retention.json'),
              'archive_sha256': retention['archive_sha256'], 'package_sha256': package['archive_sha256'],
              'freeze_sha256': runner.digest(HERE / 'results/freeze.json'), 'verifier_sha256': runner.digest(Path(__file__)),
              'limits': protocol['limitations'], 'old_v4_v5_failures_preserved': True}
    destination = HERE / 'results/beta-qualification.json'
    if destination.exists():
        previous = runner.read(destination)
        require(all(previous.get(k) == v for k, v in report.items() if k != 'evaluated_at'), 'Closed decision changed')
        report = previous
    else:
        runner.write(destination, report)
    print(json.dumps({k: v for k, v in report.items() if k != 'independent_reviews'}))


if __name__ == '__main__':
    qualify()

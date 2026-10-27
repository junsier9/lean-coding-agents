"""Evaluate preregistered beta-quality gates from retained attempts and reviews.

This is not a production/efficiency qualification. It supplements the frozen
driver with terminal-event checking after an outcome-blind method audit.
"""
from pathlib import Path
import hashlib
import json
import sys
import zipfile

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent / 'production-2026-10-25'))
import runner
from observe import observe
from audit_production_trace import scan

ROOT = PROJECT / '.local/production-runtime/generalization-v5-2026-10-26'


def require(condition, message='Evidence integrity check failed'):
    if not condition:
        raise ValueError(message)


def flag_id(flag):
    return hashlib.sha256(json.dumps(flag, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')).encode('utf-8')).hexdigest().upper()


def verify_record(record, started, done, checkpoint, case, logs, skill, frozen):
    identity = ('key', 'phase', 'case_id', 'repeat', 'arm', 'repo', 'case_sha256', 'skill_sha256', 'started_at')
    for field in identity:
        require(all(item[field] == record[field] for item in (started, done, checkpoint)), 'Identity mismatch: ' + field)
    for field in ('timeout', 'cli_exit_code', 'workspace'):
        require(record[field] == checkpoint[field] == done[field], 'Client state mismatch: ' + field)
    require(abs(record['client_seconds'] - done['client_seconds']) <= 0.000001, 'Rounded client time mismatch')
    require(checkpoint['client_seconds'] == done['client_seconds'], 'Checkpoint time mismatch')
    parsed = runner.parse_client(done, logs, skill)
    for field in ('usage', 'usage_complete', 'input_tokens', 'output_tokens', 'total_tokens',
                  'cached_input_tokens', 'thread_ids', 'stream_errors', 'skill_body_read_observed'):
        require(record[field] == checkpoint[field] == parsed[field], 'Raw event mismatch: ' + field)
    workspace = Path(record['workspace'])
    require(workspace.resolve().is_relative_to(ROOT.resolve()), 'Workspace outside batch')
    skill_ok = runner.digest(workspace / runner.SKILL_REL) == frozen['skill_sha256'] and runner.digest(
        workspace / '.agents/skills/lean-coding/agents/openai.yaml') == frozen['ui_sha256']
    expected = case['upstream']['preflight']['guard_fix'] if 'upstream' in case else runner.read(
        ROOT / 'preflight' / (case['id'] + '.json'))['expected']
    grade = record['grade']
    accepted = bool(grade['exit_code'] == 0 and grade['failed'] == grade['errors'] == 0 and
                    grade.get('skipped', 0) == expected.get('skipped', 0) and
                    grade['tests_collected'] == expected['tests_collected'] and
                    grade['tests_ran'] == expected['tests_ran'] > 0 and skill_ok)
    if 'modes' in grade:
        accepted = accepted and all(grade['modes'][mode]['exit_code'] == 0 and
            grade['modes'][mode]['tests_ran'] == expected['modes'][mode]['tests_ran'] > 0 and
            grade['modes'][mode]['skipped'] == expected['modes'][mode]['skipped'] for mode in ('normal', 'optimized'))
    require(type(record['accepted']) is bool and record['accepted'] == accepted, 'Acceptance contradicts saved grade')
    require(record['skill_files_unchanged'] == skill_ok, 'Installed skill state mismatch')


def gate_counts(fresh, known, fresh_ids, body_count, completed, review_count, defects, integrity):
    return {
        'all_planned_attempts_present': len(fresh) == 60 and len(known) == 12,
        'fresh_accepted_at_least57_of60': sum(row['accepted'] for row in fresh) >= 57,
        'each_fresh_task_at_least2_of3': len(fresh_ids) == 20 and all(
            sum(row['case_id'] == key for row in fresh) == 3 and
            sum(row['case_id'] == key and row['accepted'] for row in fresh) >= 2 for key in fresh_ids),
        'known_critical12_of12': len(known) == 12 and all(row['accepted'] for row in known),
        'all72_completed_without_timeout_with_successful_terminal_event': completed == 72,
        'all72_full_body_outputs_observed': body_count == 72,
        'eight_preselected_reviews_without_confirmed_defect': review_count == 8 and defects == 0,
        'source_trace_and_evidence_integrity': integrity is True,
    }


def qualify():
    frozen = runner.read(HERE / 'results/freeze.json')
    protocol = runner.read(HERE / 'protocol.json')
    require(runner.digest(HERE / 'protocol.json') == frozen['protocol_sha256'], 'Integrity condition at original line 80')
    require(runner.digest(HERE / 'run.py') == frozen['driver_sha256'], 'Integrity condition at original line 81')
    require(runner.digest(HERE / 'observe.py') == frozen['observer_sha256'], 'Integrity condition at original line 82')
    require(runner.digest(Path(runner.__file__)) == frozen['runner_sha256'], 'Integrity condition at original line 83')
    skill = ROOT / 'skill-input/SKILL.md'
    require(runner.digest(skill) == frozen['skill_sha256'], 'Integrity condition at original line 85')
    expected = {
        f'{phase}--{key}--r{repeat}--with-skill': (phase, key, repeat)
        for phase, keys, repeats in [('generalization', frozen['fresh'], 3),
                                   ('known-critical', frozen['known_critical'], 1)]
        for key in keys for repeat in range(repeats)
    }
    records, identities, issues, evidence, actual_flags = [], [], [], {}, []
    body_count = completed = 0
    started_paths = sorted((ROOT / 'runs').glob('*/*/started.json'))
    require(len(started_paths) == 72, 'Missing or extra native attempt: preserve it rather than omit or replace')
    require({p.parent.name for p in started_paths} == set(expected), 'Integrity condition at original line 96')
    for path in started_paths:
        logs, key = path.parent, path.parent.name
        record = runner.read(logs / 'record.json')
        case = runner.read(ROOT / 'fresh-cases' / (record['case_id'] + '.json')) if record['phase'] == 'generalization' else runner.read(
            HERE.parent / 'production-2026-10-25/holdout-sealed-v2' / (record['case_id'] + '.json'))
        verify_record(record, runner.read(path), runner.read(logs / 'client-done.json'),
                      runner.read(logs / 'cli-checkpoint.json'), case, logs, skill, frozen)
        require(record['case_sha256'] == runner.case_digest(case) == frozen['case_sha256'][record['case_id']], 'Integrity condition at original line 104')
        require((record['phase'], record['case_id'], record['repeat']) == expected[key], 'Integrity condition at original line 105')
        require(record['skill_sha256'] == frozen['skill_sha256'], 'Integrity condition at original line 106')
        require(runner.digest(Path(record['workspace']) / runner.SKILL_REL) == frozen['skill_sha256'], 'Integrity condition at original line 107')
        raw = (logs / 'events.jsonl').read_text(encoding='utf-8')
        events = [json.loads(line) for line in raw.splitlines() if line.strip()]
        observation = observe(raw, skill.read_text(encoding='utf-8'))
        require(not observation['malformed_lines'], 'Integrity condition at original line 111')
        require(len(observation['thread_ids']) == 1 and bool(observation['thread_ids'][0]), 'Each attempt requires exactly one thread')
        actual_flags.extend({'key': key, **flag} for flag in scan(raw)['flags'])
        identities += observation['thread_ids']
        body_count += observation['body_visible']
        terminals = [event['type'] for event in events if event.get('type') in {'turn.completed', 'turn.failed'}]
        successful = bool(terminals and terminals[-1] == 'turn.completed' and record['cli_exit_code'] == 0 and not record['timeout'])
        completed += successful
        if not successful:
            issues.append({'key': key, 'terminal_events': terminals, 'cli_exit_code': record['cli_exit_code'], 'timeout': record['timeout']})
        evidence[key] = {p.name: runner.digest(p) for p in sorted(logs.iterdir()) if p.is_file()}
        records.append(record)
    require(len(identities) == len(set(identities)) == 72 and None not in identities, 'Integrity condition at original line 123')
    # Source/guard/dependency bytes are checked again without running a model.
    for path in sorted((ROOT / 'fresh-cases').glob('*.json')):
        upstream = runner.read(path)['upstream']
        for stage, field in [('base', 'base_production_sha256'), ('fix', 'overlay_sha256')]:
            for name, expected_hash in upstream['preflight'][field].items():
                require(runner.digest(Path(upstream[stage + '_dir']) / name) == expected_hash.upper(), 'Integrity condition at original line 129')
        for stage in ('base', 'fix'):
            archive = upstream['preflight'][stage + '_archive']
            require(runner.digest(Path(archive['archive_path'])) == archive['sha256'].upper(), 'Integrity condition at original line 132')
    for name, expected_hash in frozen['dependency_files_sha256'].items():
        require(runner.digest(ROOT / 'testdeps' / name) == expected_hash, 'Integrity condition at original line 134')
    old = HERE.parent / 'production-2026-10-25/results/v4'
    require(runner.digest(old / 'release-qualification.json') == frozen['closed_v4_qualification_sha256'], 'Integrity condition at original line 136')
    for name, expected_hash in frozen['closed_v4_source_report_sha256'].items():
        require(runner.digest(old / name) == expected_hash, 'Integrity condition at original line 138')
    audits = {}
    for name in ('supplemental-input-integrity.json', 'transitive-source-integrity.json', 'trace-adjudication.json',
                 'artifact-review-summary.json', 'evidence-retention.json'):
        audits[name] = runner.read(HERE / 'results' / name)
    require(audits['supplemental-input-integrity.json']['prior_attempt_matches_selected'] == [], 'Integrity condition at original line 143')
    require(audits['supplemental-input-integrity.json']['source_all_match'] is True, 'Integrity condition at original line 144')
    require(audits['transitive-source-integrity.json']['all_match'] is True, 'Integrity condition at original line 145')
    trace = audits['trace-adjudication.json']
    require(trace['all_actual_flags_reviewed'] is True and trace['confirmed_scope_violations'] == 0, 'Integrity condition at original line 147')
    require(sorted(flag_id(flag) for flag in actual_flags) == sorted(trace['reviewed_flag_ids']), 'Actual trace flags differ from adjudication')
    for name, expected_hash in audits['transitive-source-integrity.json']['files_sha256'].items():
        require(runner.digest(PROJECT / name) == expected_hash, 'Transitive implementation changed')
    reviews = audits['artifact-review-summary.json']
    require(reviews['selection_matches_freeze'] is True and reviews['inputs_unchanged'] is True, 'Integrity condition at original line 152')
    require(reviews['parent_outcomes_hidden_from_reviewers'] is True, 'Integrity condition at original line 153')
    require(len(reviews['reviews']) == 8, 'Exactly eight selected artifacts must be reviewed')
    expected_review = {f'case-{index:02}': item for index, item in enumerate(frozen['independent_review'], 1)}
    require({row['review'] for row in reviews['reviews']} == set(expected_review), 'Review identities differ from freeze')
    for row in reviews['reviews']:
        require({'case_id': row['case_id'], 'repeat': row['repeat']} == expected_review[row['review']], 'Review task identity mismatch')
        neutral = PROJECT / '.local/beta-v5-artifact-reviews' / row['review']
        require(runner.digest(neutral / 'input-integrity.json') == row['input_manifest_sha256'], 'Review input manifest changed')
        for name, expected_hash in runner.read(neutral / 'input-integrity.json').items():
            require(runner.digest(neutral / name) == expected_hash, 'Review input bytes changed')
    package = runner.read(HERE / 'results/package-validation.json')
    require(package['install_and_rollback_verified'] is True and package['files_sha256']['SKILL.md'] == frozen['skill_sha256'], 'Distribution check not complete')
    package_path = PROJECT / package['archive']
    require(runner.digest(package_path) == package['archive_sha256'], 'Checked package changed')
    with zipfile.ZipFile(package_path) as archive:
        require(archive.testzip() is None and sorted(archive.namelist()) == sorted(package['entries']), 'Package entries/CRC differ')
        for name, expected_hash in package['files_sha256'].items():
            require(hashlib.sha256(archive.read('lean-coding/' + name)).hexdigest().upper() == expected_hash, 'Package bytes differ')
    require(audits['evidence-retention.json']['archive_crc_and_all_entry_hashes_verified'] is True, 'Integrity condition at original line 165')
    retention = audits['evidence-retention.json']
    evidence_archive = PROJECT / retention['private_archive']
    require(runner.digest(evidence_archive) == retention['archive_sha256'], 'Retained evidence archive changed')
    with zipfile.ZipFile(evidence_archive) as archive:
        require(archive.testzip() is None, 'Retained evidence CRC failed')
        for name, expected_hash in json.loads(archive.read('entry-sha256.json')).items():
            require(hashlib.sha256(archive.read(name)).hexdigest().upper() == expected_hash, 'Retained evidence entry changed')
    fresh = [row for row in records if row['phase'] == 'generalization']
    known = [row for row in records if row['phase'] == 'known-critical']
    gates = gate_counts(fresh, known, frozen['fresh'], body_count, completed, reviews['completed'],
                       reviews['confirmed_defects'], True)
    report = {
        'evaluated_at': runner.utc(), 'candidate': 'v5-python', 'skill_sha256': frozen['skill_sha256'],
        'beta_quality_gate_passed': all(gates.values()), 'gates': gates,
        'production_assistant_qualified': False, 'stable_efficiency_benefit_proven': False,
        'comparative_v5_efficiency_assessed': False, 'old_v4_qualification_unchanged': True,
        'fresh_attempts': len(fresh), 'fresh_accepted': sum(r['accepted'] for r in fresh),
        'known_critical_attempts': len(known), 'known_critical_accepted': sum(r['accepted'] for r in known),
        'successful_terminal_completions': completed, 'full_body_outputs': body_count,
        'usage_complete': sum(r['usage_complete'] for r in records),
        'completion_issues': issues, 'raw_artifact_sha256': evidence,
        'audit_report_sha256': {name: runner.digest(HERE / 'results' / name) for name in audits},
        'qualifier_sha256': runner.digest(Path(__file__)),
        'protocol_sha256': frozen['protocol_sha256'], 'freeze_sha256': runner.digest(HERE / 'results/freeze.json'),
        'limits': protocol['limitations'],
        'distribution_status': 'not_yet_published',
    }
    destination = HERE / 'results/beta-qualification.json'
    if destination.exists():
        prior = runner.read(destination)
        require(all(prior.get(key) == value for key, value in report.items() if key != 'evaluated_at'), 'Closed qualification changed')
        report = prior
    else:
        runner.write(destination, report)
    print(json.dumps({k: v for k, v in report.items() if k not in {'raw_artifact_sha256'}}))


if __name__ == '__main__':
    qualify()

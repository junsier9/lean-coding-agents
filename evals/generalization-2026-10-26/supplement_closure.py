"""Post-closure read-only checks for F1-F5; original failed decision stays fixed."""
from pathlib import Path
import hashlib
import json
import sys
import zipfile

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent / 'production-2026-10-25'))
import runner
from export_results import public_value

ROOT = PROJECT / '.local/production-runtime/generalization-v5-2026-10-26'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def main():
    frozen = runner.read(HERE / 'results/freeze.json')
    qualification = HERE / 'results/beta-qualification.json'
    closed_hash = runner.digest(qualification)
    require(runner.read(qualification)['beta_quality_gate_passed'] is False, 'Preserve the actual failed decision')
    known = []
    for path in sorted((ROOT / 'runs/known-critical').glob('*/record.json')):
        record = runner.read(path)
        require(set(record['grade']['modes']) == {'normal', 'optimized'}, 'Known contract modes missing')
        expected = runner.read(ROOT / 'preflight' / (record['case_id'] + '.json'))['expected']
        for mode in ('normal', 'optimized'):
            actual = record['grade']['modes'][mode]
            require(actual['exit_code'] == 0 and actual['tests_ran'] == expected['modes'][mode]['tests_ran'] > 0,
                    'Known mode does not match frozen contract')
        known.append(record['case_id'])
    require(sorted(known) == sorted(frozen['known_critical']), 'Known case set differs')
    summary = runner.read(HERE / 'results/artifact-review-summary.json')
    reviewed = {}
    for name in ('review-a.json', 'review-b.json'):
        report = HERE / 'results' / name
        for case in runner.read(report)['cases']:
            require(case['case_id'] not in reviewed, 'Duplicate actual review output')
            reviewed[case['case_id']] = (case, runner.digest(report))
    defects = 0
    input_files = 0
    for row in summary['reviews']:
        case, report_hash = reviewed.pop(row['review'])
        require(row['review_report_sha256'] == report_hash, 'Review output hash changed')
        require(row['confirmed_findings'] == case['confirmed_findings'] and row['conclusion'] == case['conclusion'],
                'Review summary contradicts actual output')
        defects += len(case['confirmed_findings'])
        neutral = PROJECT / '.local/beta-v5-artifact-reviews' / row['review']
        manifest = runner.read(neutral / 'input-integrity.json')
        require(bool(manifest) and 'request.txt' in manifest, 'Empty review input')
        require(any(n.startswith('base/') for n in manifest) and any(n.startswith('candidate/') for n in manifest),
                'Both actual input trees are required')
        actual_names = {p.relative_to(neutral).as_posix() for p in neutral.rglob('*') if p.is_file() and p.name != 'input-integrity.json'}
        require(actual_names == set(manifest), 'Review input inventory differs')
        source_case = runner.read(ROOT / 'fresh-cases' / (row['case_id'] + '.json'))
        require((neutral / 'request.txt').read_text(encoding='utf-8') == source_case['task'], 'Review request differs')
        workspace = ROOT / 'work/generalization' / row['case_id'] / 'r0/with-skill'
        for name, expected in manifest.items():
            require(runner.digest(neutral / name) == expected, 'Review input bytes changed')
            if name.startswith('candidate/'):
                require(runner.digest(workspace / name.removeprefix('candidate/')) == expected, 'Review candidate differs from preselected run')
            elif name.startswith('base/'):
                require(runner.digest(Path(source_case['source_dir']) / name.removeprefix('base/')) == expected, 'Review base differs')
        input_files += len(manifest)
    require(not reviewed and summary['completed'] == 8 and defects == summary['confirmed_defects'] == 2,
            'Review completion/defect count differs from actual output')
    retention = runner.read(HERE / 'results/evidence-retention.json')
    with zipfile.ZipFile(PROJECT / retention['private_archive']) as archive:
        index = json.loads(archive.read('entry-sha256.json'))
        require(set(archive.namelist()) == set(index) | {'entry-sha256.json'}, 'Evidence entry index incomplete')
        expected_raw = {str('runtime/' + p.relative_to(ROOT).as_posix()): runner.digest(p)
                        for p in sorted((ROOT / 'runs').glob('*/*/*')) if p.is_file()}
        require(bool(expected_raw) and {name.split('/')[3] for name in expected_raw} == {
            p.name for p in (ROOT / 'runs').glob('*/*') if p.is_dir()}, 'Raw run inventory differs')
        for name, expected in expected_raw.items():
            require(index.get(name) == expected, 'Raw evidence missing from retained archive')
            require(hashlib.sha256(archive.read(name)).hexdigest().upper() == expected, 'Raw retained bytes differ')
    package = runner.read(HERE / 'results/package-validation.json')
    with zipfile.ZipFile(PROJECT / package['archive']) as archive:
        required = {'SKILL.md', 'agents/openai.yaml', 'LICENSE', 'NOTICE.txt'}
        require(set(archive.namelist()) == {'lean-coding/' + name for name in required}, 'Package files missing/extra')
        require(hashlib.sha256(archive.read('lean-coding/SKILL.md')).hexdigest().upper() == frozen['skill_sha256'], 'Rule bytes differ')
        require(hashlib.sha256(archive.read('lean-coding/agents/openai.yaml')).hexdigest().upper() == frozen['ui_sha256'], 'Frozen UI missing or changed')
    require(runner.digest(qualification) == closed_hash, 'Original failed qualification changed')
    runner.write(HERE / 'results/supplemental-closure-integrity.json', {
        'checked_at': runner.utc(), 'audit_class': 'post_qualification_supplement_not_backdated',
        'F1_known_normal_and_optimized_modes_verified': 12,
        'F2_actual_review_outputs_bound': 8, 'actual_confirmed_review_defects': defects,
        'F3_raw_evidence_files_bound': len(expected_raw), 'F4_nonempty_review_input_files_bound': input_files,
        'F5_frozen_rule_and_UI_with_license_and_failure_notice_verified': True,
        'original_qualification_sha256': closed_hash, 'original_failed_qualification_unchanged': True,
        'qualifier_original_coverage_not_rewritten': True, 'native_model_calls': 0})


if __name__ == '__main__':
    main()

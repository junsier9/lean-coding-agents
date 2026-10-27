"""Read-only source/trace audits, neutral-review binding and private retention.

Run only after the fixed native batch and independent reviews are complete.
This does not launch models, rerun grades or change frozen inputs.
"""
from pathlib import Path
import hashlib
import json
import shutil
import subprocess
import sys
import zipfile

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent.parent
OLD = HERE.parent / 'production-2026-10-25'
sys.path.insert(0, str(OLD))
import runner
from audit_production_trace import scan
from export_results import public_value
from qualify_beta import flag_id

ROOT = PROJECT / '.local/production-runtime/generalization-v5-2026-10-26'


def audits():
    files = ['evals/production-2026-10-25/' + name for name in
             ('runner.py', 'prepare_second_real.py', 'audit_production_trace.py', 'export_results.py', 'resource_gate.py')]
    files += ['evals/full-validation/' + name for name in ('preflight.py', 'run_experiment.py')]
    checks = {}
    prior = runner.read(OLD / 'results/v4/candidate-freeze.json')
    old_names = {'runner.py': 'runner_sha256', 'resource_gate.py': 'resource_gate_sha256',
                 'preflight.py': 'upstream_grader_sha256', 'run_experiment.py': 'transport_sha256'}
    for name in files:
        actual = runner.digest(PROJECT / name)
        earlier = subprocess.check_output([str(runner.GIT), 'show', '64cede4:' + name], cwd=PROJECT)
        expected = hashlib.sha256(earlier).hexdigest().upper()
        if actual != expected:
            raise ValueError('Transitive implementation differs from pre-batch commit: ' + name)
        frozen_field = old_names.get(Path(name).name)
        if frozen_field and actual != prior[frozen_field]:
            raise ValueError('Existing v4 implementation binding differs: ' + name)
        checks[name] = actual
    runner.write(HERE / 'results/transitive-source-integrity.json', {
        'checked_at': runner.utc(), 'audit_class': 'post_run_read_only_supplement', 'all_match': True,
        'files_sha256': checks, 'pre_batch_commit': '64cede4',
        'v4_frozen_bindings_reused': list(old_names.values()),
        'limits': 'New freeze did not individually bind every transitive implementation. This later audit checks unchanged bytes against a pre-batch commit and existing v4 bindings; it does not backdate the new freeze or attest all imported OS/runtime code.'})
    flags, raw = [], {}
    for path in sorted((ROOT / 'runs').glob('*/*/events.jsonl')):
        flags += [{'key': path.parent.name, **flag} for flag in scan(path.read_text(encoding='utf-8'))['flags']]
        raw[path.parent.name] = runner.digest(path)
    if len(flags) != 1 or flags[0]['key'] != 'generalization--xb-019-click-2550--r2--with-skill':
        raise ValueError('Actual trace flags require additional review')
    flag = flags[0]
    if '.pytest-local-20261026-a1f8' not in flag['command'] or 'Remove-Item -LiteralPath' not in flag['command']:
        raise ValueError('Reviewed command changed')
    runner.write(HERE / 'results/trace-adjudication.json', {
        'checked_at': runner.utc(), 'all_actual_flags_reviewed': True, 'confirmed_scope_violations': 0,
        'reviewed_flag_ids': [flag_id(flag)], 'events_sha256': raw,
        'adjudications': [{**public_value(flag), 'decision': 'benign_own_test_temporary_directory_cleanup',
                          'evidence': 'Same trace item_22 rejects a pre-existing path then supplies it as pytest --basetemp. item_23 observes full path/attributes and Git untracked status. item_24 deletes that exact absolute child of the current workspace, exit0. No scorer/reference/other experiment access is present in the flagged command.'}],
        'limits': 'Native emitted traces are not OS-level filesystem/network attestations. Keyword flags require context; a workspace path containing production-runtime is not itself forbidden access.'})


def reviews():
    frozen = runner.read(HERE / 'results/freeze.json')
    neutral = PROJECT / '.local/beta-v5-artifact-reviews'
    rows = []
    for label in ('a', 'b'):
        path = neutral / ('review-' + label + '.json')
        report = runner.read(path)
        destination = HERE / 'results' / path.name
        runner.write(destination, public_value(report))
        for case in report['cases']:
            review_id = case['case_id']
            index = int(review_id.split('-')[1]) - 1
            selected = frozen['independent_review'][index]
            manifest = neutral / review_id / 'input-integrity.json'
            binding = case['input_hash_binding']
            expected = binding.get('manifest_sha256') or binding.get('input_manifest_sha256')
            if expected and runner.digest(manifest) != expected:
                raise ValueError('Reviewer input-manifest binding differs')
            for name, expected_hash in runner.read(manifest).items():
                if runner.digest(neutral / review_id / name) != expected_hash:
                    raise ValueError('Reviewed input bytes changed')
            findings = case.get('confirmed_findings', [])
            rows.append({'review': review_id, **selected, 'review_report': path.name,
                         'review_report_sha256': runner.digest(destination),
                         'input_manifest_sha256': runner.digest(manifest),
                         'confirmed_findings': findings, 'unresolved': case.get('unresolved', []),
                         'conclusion': case['conclusion']})
    if len(rows) != 8 or len({row['review'] for row in rows}) != 8:
        raise ValueError('Eight unique prospectively selected reviews are required')
    runner.write(HERE / 'results/artifact-review-summary.json', public_value({
        'completed_at': runner.utc(), 'completed': 8,
        'confirmed_defects': sum(len(row['confirmed_findings']) for row in rows),
        'unresolved_notes': sum(len(row['unresolved']) for row in rows),
        'selection_matches_freeze': True, 'inputs_unchanged': True,
        'parent_outcomes_hidden_from_reviewers': True,
        'parent_adjudication': 'A-01-1 is a positive __index__ compatibility regression under the explicit preserve-positive-boundaries requirement. A-03-1 is a reproducible narrower signature-probe exception fallback than the prior callable acceptance; the task explicitly preserves uninspectable-callable compatibility. Keep its medium confidence and limited custom-descriptor scope. Neither finding proves causation by the skill.',
        'reviews': sorted(rows, key=lambda row: row['review'])}))


def retain():
    target = PROJECT / '.local/generalization-v5-evidence.zip'
    if target.exists():
        raise FileExistsError('Do not overwrite retained evidence')
    sources = {}
    for folder in ('runs', 'fresh-cases', 'skill-input', 'archives', 'preflight'):
        for path in sorted((ROOT / folder).rglob('*')):
            if path.is_file() and not any(part in {'__pycache__', '.git'} for part in path.parts):
                sources['runtime/' + path.relative_to(ROOT).as_posix()] = path
    for path in sorted((ROOT / 'grades').glob('*/*-logs/*')):
        if path.is_file():
            sources['runtime/' + path.relative_to(ROOT).as_posix()] = path
    for path in sorted((ROOT / 'grades/known-critical').glob('*-tests.log')):
        sources['runtime/' + path.relative_to(ROOT).as_posix()] = path
    for path in sorted(HERE.rglob('*')):
        if path.is_file() and not any(part == '__pycache__' for part in path.parts) and path.name not in {
                'evidence-retention.json', 'beta-qualification.json'}:
            sources['public/' + path.relative_to(HERE).as_posix()] = path
    for path in sorted((PROJECT / '.local/beta-v5-artifact-reviews').rglob('*')):
        if path.is_file() and 'deps' not in path.parts and '__pycache__' not in path.parts:
            sources['independent-review/' + path.relative_to(PROJECT / '.local/beta-v5-artifact-reviews').as_posix()] = path
    hashes = {name: runner.digest(path) for name, path in sources.items()}
    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, path in sources.items():
            archive.write(path, name)
        archive.writestr('entry-sha256.json', json.dumps(hashes, indent=2))
    with zipfile.ZipFile(target) as archive:
        if archive.testzip() is not None:
            raise ValueError('Private archive CRC failed')
        for name, expected in hashes.items():
            if hashlib.sha256(archive.read(name)).hexdigest().upper() != expected:
                raise ValueError('Private archive entry differs')
    runner.write(HERE / 'results/evidence-retention.json', {
        'retained_at': runner.utc(), 'private_archive': '.local/generalization-v5-evidence.zip',
        'archive_sha256': runner.digest(target), 'bytes': target.stat().st_size, 'entries': len(hashes) + 1,
        'archive_crc_and_all_entry_hashes_verified': True, 'native_run_raw_directories': 72,
        'public_upload_of_private_archive': False,
        'included': 'Native raw traces/checkpoints/records, source archive inputs, grade logs, frozen fixtures, public export patches, neutral review inputs/reports',
        'excluded': 'Dependencies bound separately by file hash; OS/runtime import trace; .git/cache directories; complete arbitrary untracked workspace files. Live source workspaces are retained locally.'})


if __name__ == '__main__':
    audits()
    reviews()
    retain()

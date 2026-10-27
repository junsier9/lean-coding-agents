"""Stage a v6 package and retain raw evidence after fixed native/review work."""
from pathlib import Path
import hashlib
import json
import shutil
import sys
import tempfile
import zipfile

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent / 'production-2026-10-25'))
sys.path.insert(0, str(HERE.parent / 'generalization-2026-10-26'))
import runner
from audit_production_trace import scan
from qualify_beta import flag_id, require

ROOT = PROJECT / '.local/production-runtime/generalization-v6-2026-10-27'


def trace_review():
    flags = []
    for path in sorted((ROOT / 'runs').glob('*/*/events.jsonl')):
        flags.extend({'key': path.parent.name, **flag} for flag in scan(path.read_text(encoding='utf-8'))['flags'])
    runner.write(PROJECT / '.local/v6-actual-trace-flags.json', flags)
    manual_path = PROJECT / '.local/v6-trace-decisions.json'
    decisions = runner.read(manual_path) if manual_path.exists() else []
    require({row['flag_id'] for row in decisions} == {flag_id(flag) for flag in flags}, 'Every actual trace flag needs a contextual manual decision')
    require(all(row['completed'] is True and bool(row['reason']) for row in decisions), 'Trace review incomplete')
    runner.write(HERE / 'results/trace-adjudication.json', {
        'reviewed_at': runner.utc(), 'reviewed_flag_ids': [flag_id(flag) for flag in flags],
        'confirmed_scope_violations': sum(row['confirmed_scope_violation'] for row in decisions),
        'decisions': decisions,
        'limits': 'Native emitted events and contextual keyword review do not attest every OS-level file/network access.'})


def package():
    frozen = runner.read(HERE / 'results/freeze.json')
    output = PROJECT / '.local/v6-package-stage'
    output.mkdir(parents=True, exist_ok=True)
    target = output / 'lean-coding-0.4.0-beta.1.zip'
    candidate = ROOT / 'skill-input'
    files = {name: (candidate / name).read_bytes() for name in ('SKILL.md', 'agents/openai.yaml')}
    files['LICENSE'] = (PROJECT / 'LICENSE').read_bytes()
    files['NOTICE.txt'] = b'Experimental beta for Codex with existing Python maintenance code.\nNo proven speed or token savings; Claude is untested. Not industrial production qualification.\nCheck the repository evaluation and qualification reports before use.\nPrior v4 and v5 failures remain available. No service connections or added runtime dependencies.\n'
    require(hashlib.sha256(files['SKILL.md']).hexdigest().upper() == frozen['skill_sha256'], 'Candidate differs')
    with zipfile.ZipFile(target, 'w') as archive:
        for name, data in files.items():
            info = zipfile.ZipInfo('lean-coding/' + name, (1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    temporary_parent = PROJECT / '.local/package-smoke'
    temporary_parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='v6-', dir=temporary_parent) as temporary:
        directory = Path(temporary)
        require(directory.resolve().is_relative_to(temporary_parent.resolve()), 'Unexpected package-check directory')
        with zipfile.ZipFile(target) as archive:
            require(archive.testzip() is None and set(archive.namelist()) == {'lean-coding/' + n for n in files}, 'Unsafe or corrupt package')
            archive.extractall(directory)
        installed = directory / 'lean-coding'
        require(all((installed / n).read_bytes() == data for n, data in files.items()), 'Install bytes differ')
        previous = PROJECT / 'skills/lean-coding'
        prior = {n: (previous / n).read_bytes() for n in ('SKILL.md', 'agents/openai.yaml')}
        for name in prior:
            shutil.copyfile(previous / name, installed / name)
        require(all((installed / n).read_bytes() == data for n, data in prior.items()), 'Rollback bytes differ')
        for name, data in files.items():
            (installed / name).write_bytes(data)
        require(all((installed / n).read_bytes() == data for n, data in files.items()), 'Reinstall bytes differ')
    runner.write(HERE / 'results/package-validation.json', {
        'archive': target.relative_to(PROJECT).as_posix(), 'archive_sha256': runner.digest(target),
        'files_sha256': {n: hashlib.sha256(data).hexdigest().upper() for n, data in files.items()},
        'runtime_files': 2, 'license_and_status_notices': 2, 'install_and_rollback_verified': True,
        'external_publication': False, 'status': 'private_staging_pending_qualification'})


def retain():
    target = PROJECT / '.local/generalization-v6-evidence.zip'
    require(not target.exists(), 'Do not overwrite retained evidence')
    sources = {}
    for folder in ('runs', 'fresh-cases', 'skill-input', 'archives', 'preflight', 'recovery'):
        for path in sorted((ROOT / folder).rglob('*')):
            if path.is_file() and '__pycache__' not in path.parts:
                sources['runtime/' + path.relative_to(ROOT).as_posix()] = path
    for path in sorted((ROOT / 'grades').glob('*/*-logs/*')):
        if path.is_file():
            sources['runtime/' + path.relative_to(ROOT).as_posix()] = path
    for path in sorted((ROOT / 'grades/known-critical').glob('*-tests.log')):
        sources['runtime/' + path.relative_to(ROOT).as_posix()] = path
    for path in sorted((PROJECT / '.local/v6-artifact-reviews').rglob('*')):
        if path.is_file() and 'deps' not in path.parts and '__pycache__' not in path.parts:
            sources['independent-review/' + path.relative_to(PROJECT / '.local/v6-artifact-reviews').as_posix()] = path
    for path in sorted(HERE.rglob('*')):
        if path.is_file() and '__pycache__' not in path.parts and path.name not in {'evidence-retention.json', 'beta-qualification.json'}:
            sources['public/' + path.relative_to(HERE).as_posix()] = path
    index = {name: runner.digest(path) for name, path in sources.items()}
    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, path in sources.items():
            archive.write(path, name)
        archive.writestr('entry-sha256.json', json.dumps(index, indent=2))
    with zipfile.ZipFile(target) as archive:
        require(archive.testzip() is None, 'Evidence archive CRC failed')
        require(all(hashlib.sha256(archive.read(n)).hexdigest().upper() == h for n, h in index.items()), 'Evidence archive entry changed')
    runner.write(HERE / 'results/evidence-retention.json', {
        'retained_at': runner.utc(), 'private_archive': target.relative_to(PROJECT).as_posix(),
        'archive_sha256': runner.digest(target), 'entries': len(index) + 1, 'bytes': target.stat().st_size,
        'archive_crc_and_all_entry_hashes_verified': True, 'external_publication_of_private_archive': False,
        'limits': 'Bound dependencies and retained live workspaces are outside this archive. No OS-level access trace is claimed.'})


if __name__ == '__main__':
    trace_review()
    package()
    retain()

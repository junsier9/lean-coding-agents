"""Package the frozen candidate plus MIT notice after beta-quality gates pass."""
from pathlib import Path
import hashlib
import json
import shutil
import tempfile
import zipfile

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent.parent
VERSION = '0.4.0-beta.1'
FILES = ('SKILL.md', 'agents/openai.yaml', 'LICENSE')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def build():
    qualification = json.loads((HERE / 'results/beta-qualification.json').read_text(encoding='utf-8'))
    if qualification['beta_quality_gate_passed'] is not True or qualification['production_assistant_qualified'] is not False:
        raise ValueError('Distribution requires an honestly passed beta-quality qualification')
    candidate = HERE.parent / 'development-2026-10-26/candidates/v5-python'
    assert sha(candidate / 'SKILL.md') == qualification['skill_sha256']
    sources = {name: candidate / name for name in FILES[:2]}
    sources['LICENSE'] = PROJECT / 'LICENSE'
    output = PROJECT / 'dist'
    output.mkdir(exist_ok=True)
    archive_path = output / f'lean-coding-{VERSION}.zip'
    with zipfile.ZipFile(archive_path, 'w') as archive:
        for name in FILES:
            info = zipfile.ZipInfo('lean-coding/' + name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, sources[name].read_bytes())
    expected = {name: sha(path) for name, path in sources.items()}
    previous = PROJECT / 'skills/lean-coding'
    previous_hashes = {name: sha(previous / name) for name in FILES[:2]}
    with tempfile.TemporaryDirectory(prefix='lean-beta-install-') as temporary:
        installed = Path(temporary)
        with zipfile.ZipFile(archive_path) as archive:
            assert archive.testzip() is None
            assert sorted(archive.namelist()) == sorted('lean-coding/' + name for name in FILES)
            archive.extractall(installed)
        installed = installed / 'lean-coding'
        assert {name: sha(installed / name) for name in FILES} == expected
        for name in FILES[:2]:
            shutil.copyfile(previous / name, installed / name)
        assert {name: sha(installed / name) for name in FILES[:2]} == previous_hashes
        for name in FILES[:2]:
            shutil.copyfile(sources[name], installed / name)
        assert {name: sha(installed / name) for name in FILES} == expected
    manifest = {
        'version': VERSION, 'status': 'beta', 'price': 'free', 'license': 'MIT',
        'skill_sha256': expected['SKILL.md'], 'files_sha256': expected,
        'archive': archive_path.name, 'archive_sha256': sha(archive_path),
        'entries': ['lean-coding/' + name for name in FILES],
        'runtime_files': 2, 'license_notice_files': 1,
        'qualification': 'evals/generalization-2026-10-26/results/beta-qualification.json',
        'qualification_sha256': sha(HERE / 'results/beta-qualification.json'),
        'beta_quality_gate_passed': True, 'production_assistant_qualified': False,
        'stable_efficiency_benefit_proven': False, 'comparative_v5_efficiency_assessed': False,
        'supported_scope': 'Codex with existing Python maintenance code; scoped beta',
        'claude_tested': False, 'global_skill_directory_modified': False,
        'package_crc_and_paths_verified': True, 'install_and_rollback_verified': True,
        'previous_canonical_skill_sha256': previous_hashes['SKILL.md']
    }
    (output / f'lean-coding-{VERSION}.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(manifest))


if __name__ == '__main__':
    build()

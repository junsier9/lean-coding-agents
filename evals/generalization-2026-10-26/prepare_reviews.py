"""Create neutral review trees for the eight prospectively selected artifacts."""
from pathlib import Path
import json
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent / 'production-2026-10-25'))
import runner

ROOT = PROJECT / '.local/production-runtime/generalization-v5-2026-10-26'
DESTINATION = PROJECT / '.local/beta-v5-artifact-reviews'
EXCLUDE = {'.git', '.agents', '.benchmark-deps', '__pycache__', '.pytest_cache'}


def prepare():
    frozen = runner.read(HERE / 'results/freeze.json')
    mapping, ready = [], []
    for index, selected in enumerate(frozen['independent_review'], 1):
        key = f"generalization--{selected['case_id']}--r0--with-skill"
        logs = ROOT / 'runs/generalization' / key
        review = DESTINATION / f'case-{index:02}'
        mapping.append({'review': review.name, 'run': key})
        if not (logs / 'record.json').exists():
            continue
        case = runner.read(ROOT / 'fresh-cases' / (selected['case_id'] + '.json'))
        workspace = Path(runner.read(logs / 'record.json')['workspace'])
        if review.exists():
            ready.append(review.name)
            continue
        review.mkdir(parents=True)
        (review / 'request.txt').write_text(case['task'], encoding='utf-8')
        base = Path(case['source_dir'])
        names = set()
        for source in base.rglob('*'):
            relative = source.relative_to(base)
            if not source.is_file() or any(part in EXCLUDE for part in relative.parts):
                continue
            target = review / 'base' / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            names.add(relative.as_posix())
        untracked = runner.git(workspace, 'ls-files', '--others', '--exclude-standard', '-z').split('\0')
        for name in untracked:
            relative = Path(name)
            if name and relative.suffix == '.py' and not any(part in EXCLUDE or part.startswith(('.pytest', '.test-tmp')) for part in relative.parts):
                names.add(relative.as_posix())
        # Include changes to tracked source/configuration files and actual deletions.
        changed = runner.git(workspace, 'diff', '--name-only', '-z', 'HEAD').split('\0')
        names.update(name for name in changed if name and name != 'TASK.md' and not any(part in EXCLUDE for part in Path(name).parts))
        for name in sorted(names):
            source = workspace / name
            if not source.is_file():
                continue
            assert not source.is_symlink()
            target = review / 'candidate' / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        manifest = {p.relative_to(review).as_posix(): runner.digest(p) for p in sorted(review.rglob('*')) if p.is_file()}
        runner.write(review / 'input-integrity.json', manifest)
        ready.append(review.name)
    runner.write(PROJECT / '.local/beta-v5-review-mapping.json', mapping)
    print(json.dumps({'preselected': 8, 'ready': ready, 'no_outcome_selection': True}))


if __name__ == '__main__':
    prepare()

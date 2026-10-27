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

ROOT = PROJECT / '.local/production-runtime/generalization-v6-2026-10-27'
DESTINATION = PROJECT / '.local/v6-artifact-reviews'
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
        # Include untracked non-Python production assets as well. Recognized
        # runtime/scratch directories remain outside the neutral source input.
        names = set()
        for source in workspace.rglob('*'):
            relative = source.relative_to(workspace)
            if any(part in EXCLUDE or part.startswith(('.pytest-', '.test-tmp')) for part in relative.parts) or relative.as_posix() == 'TASK.md':
                continue
            if source.is_symlink() or source.is_junction():
                raise ValueError('Review source contains a symlink/junction: ' + str(relative))
            if source.is_file():
                names.add(relative.as_posix())
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
    runner.write(PROJECT / '.local/v6-review-mapping.json', mapping)
    print(json.dumps({'preselected': 8, 'ready': ready, 'no_outcome_selection': True}))


if __name__ == '__main__':
    prepare()

"""Describe incomplete scheduled attempts negatively, without inventing records."""
from pathlib import Path
import json


def describe(root, frozen, runner):
    expected = {f'{phase}--{case}--r{repeat}--with-skill': (phase, case, repeat)
                for phase, cases, repeats in [('generalization', frozen['fresh'], 3), ('known-critical', frozen['known_critical'], 1)]
                for case in cases for repeat in range(repeats)}
    missing = []
    for key, (phase, case, repeat) in expected.items():
        logs = root / 'runs' / phase / key
        if (logs / 'record.json').exists():
            continue
        failure_path = root / (phase + '-failures.json')
        failures = runner.read(failure_path) if failure_path.exists() else []
        matches = [row for row in failures if row.get('case_id') == case and row.get('repeat') == repeat]
        if len(matches) > 1:
            raise ValueError('Multiple failure summaries for one planned attempt')
        evidence = {p.relative_to(root).as_posix(): runner.digest(p) for p in sorted(logs.glob('*')) if p.is_file()}
        if failure_path.exists():
            evidence[failure_path.relative_to(root).as_posix()] = runner.digest(failure_path)
        for name in ('started.json', 'client-done.json', 'cli-checkpoint.json'):
            path = logs / name
            if not path.exists():
                continue
            item = runner.read(path)
            for field, wanted in {'key': key, 'phase': phase, 'case_id': case, 'repeat': repeat,
                                  'arm': 'with-skill', 'case_sha256': frozen['case_sha256'][case],
                                  'skill_sha256': frozen['skill_sha256']}.items():
                if item.get(field) != wanted:
                    raise ValueError('Incomplete raw metadata contradicts frozen identity')
        missing.append({'key': key, 'phase': phase, 'case_id': case, 'repeat': repeat, 'arm': 'with-skill',
                        'status': 'incomplete_attempt' if matches or (logs / 'started.json').exists() else 'not_attempted',
                        'end_to_end_success': False, 'usage': None, 'grade': None,
                        'failure_summary': matches, 'raw_evidence_sha256': evidence,
                        'recovery_policy': 'No replacement model call; existing completed-client evidence may support grade-only recovery'})
    return missing

"""Freeze and run a prospective v5 beta-quality batch. No comparison claims."""
from pathlib import Path
import argparse
import json
import shutil
import sys
import urllib.parse

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent.parent
OLD = HERE.parent / 'production-2026-10-25'
sys.path.insert(0, str(OLD))
import runner
import resource_gate
from prepare_second_real import restore_case
from export_results import FIELDS, public_value
from audit_production_trace import scan
from observe import observe, body

ROOT = PROJECT / '.local/production-runtime/generalization-v5-2026-10-26'
SKILL = ROOT / 'skill-input/SKILL.md'


def immutable(path, value):
    if path.exists():
        assert runner.read(path) == value, 'Frozen input changed: ' + str(path)
    else:
        runner.write(path, value)


def observer_checks():
    skill = (HERE.parent / 'development-2026-10-26/candidates/v5-python/SKILL.md').read_text(encoding='utf-8')
    examples = []
    for path in sorted((PROJECT / '.local/production-runtime/dev-v5-iteration2/runs/development').glob('*/events.jsonl')):
        observation = observe(path.read_text(encoding='utf-8'), skill)
        assert observation['body_visible'] and not observation['malformed_lines']
        examples.append({'run': path.parent.name, 'sha256': runner.digest(path), **observation})
    assert len(examples) == 6
    def event(item):
        return json.dumps({'type': 'item.completed', 'item': item})
    text = body(skill)
    negative = [
        {'type': 'mcp_tool_call', 'arguments': {'code': text}, 'result': {'content': [{'text': 'ENOENT'}]}},
        {'type': 'agent_message', 'text': text},
        {'type': 'command_execution', 'command': text, 'aggregated_output': 'not read'},
        {'type': 'mcp_tool_call', 'result': {'content': [{'text': text[:100]}]}}
    ]
    for item in negative:
        assert not observe(event(item), skill)['body_visible']
    for item in [
        {'type': 'command_execution', 'aggregated_output': skill},
        {'type': 'mcp_tool_call', 'result': {'content': [{'type': 'text', 'text': skill}]}},
        {'type': 'mcp_tool_call', 'result': {'content': [{'text': json.dumps({'content': skill})}]}},
        {'type': 'mcp_tool_call', 'result': {'content': [{'text': "{ content: " + repr(skill[:100]) + ' +\n' + repr(skill[100:]) + ' }'}]}}
    ]:
        assert observe(event(item), skill)['body_visible']
    immutable(HERE / 'results/observer-preflight.json', {'native_model_calls': 0, 'known_traces': examples,
              'synthetic_positive_probes': 4, 'synthetic_negative_probes': 4,
              'observer_sha256': runner.digest(HERE / 'observe.py'), 'all_passed': True})


def prepare():
    protocol = runner.read(HERE / 'protocol.json')
    oldroot = Path(runner.read(PROJECT / '.local/state.json')['runtime_root'])
    originals = {case['id']: case for case in runner.read(oldroot / 'eligible.json')['cases']}
    selection = runner.read(OLD / 'results/second-corpus-selection.json')['remaining_adequate'][:20]
    assert len(set(selection)) == 20
    seen = set()
    journals = [oldroot / 'records.jsonl', *sorted((PROJECT / '.local/production-runtime').glob('*/records.jsonl'))]
    prior_journals = {}
    for path in journals:
        if path.parent == ROOT or not path.is_file():
            continue
        prior_journals[path.relative_to(PROJECT).as_posix() if path.is_relative_to(PROJECT) else 'original-runtime/records.jsonl'] = runner.digest(path)
        for line in path.read_text(encoding='utf-8').splitlines():
            if line.strip():
                seen.add(json.loads(line)['case_id'])
    assert not set(selection) & seen, 'Fresh selection has prior model records'
    prior_pairs = {(originals[key]['repo'], originals[key]['fix_sha']) for key in seen if key in originals}
    def normalized_url(case):
        return urllib.parse.urlsplit(case['issue_or_pr_url'])._replace(query='', fragment='').geturl().rstrip('/')
    prior_urls = {normalized_url(originals[key]) for key in seen if key in originals}
    assert not prior_pairs & {(originals[key]['repo'], originals[key]['fix_sha']) for key in selection}
    assert not prior_urls & {normalized_url(originals[key]) for key in selection}
    ROOT.mkdir(parents=True, exist_ok=True)
    assert not list((PROJECT / '.local/production-runtime').glob('*/active-suite.lock'))
    assert not resource_gate.STOP.exists()
    assert runner.transport.settings() == runner.SETTINGS
    source = HERE.parent / 'development-2026-10-26/candidates/v5-python'
    for name, field in [('SKILL.md', 'frozen_candidate_sha256'), ('agents/openai.yaml', 'frozen_ui_sha256')]:
        assert runner.digest(source / name) == protocol[field]
        destination = ROOT / 'skill-input' / name
        if not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / name, destination)
        assert runner.digest(destination) == protocol[field]
    observer_checks()
    fresh, known = [], []
    for key in selection:
        target = ROOT / 'fresh-cases' / (key + '.json')
        if target.exists():
            wrapper = runner.read(target)
        else:
            case = restore_case(originals[key], ROOT)
            wrapper = {'id': key, 'repo': case['repo'], 'family': 'upstream-maintenance',
                       'task': case['task'], 'source_dir': case['base_dir'], 'upstream': case}
            runner.write(target, wrapper)
        runner.preflight_case(wrapper, ROOT)
        fresh.append(wrapper)
    for index in range(1, 13):
        path = OLD / 'holdout-sealed-v2' / f'h{index:02}.json'
        case = runner.read(path)
        runner.preflight_case(case, ROOT)
        known.append(case)
    if not (ROOT / 'testdeps').exists():
        shutil.copytree(PROJECT / '.local/production-runtime/real-v4/testdeps', ROOT / 'testdeps',
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    closed = runner.read(OLD / 'results/v4/release-qualification.json')
    assert closed['production_assistant_qualified'] is False
    for name, expected in closed['source_report_sha256'].items():
        assert runner.digest(OLD / 'results/v4' / name) == expected
    frozen = {
        'protocol_sha256': runner.digest(HERE / 'protocol.json'),
        'skill_sha256': runner.digest(SKILL), 'ui_sha256': runner.digest(ROOT / 'skill-input/agents/openai.yaml'),
        'driver_sha256': runner.digest(Path(__file__)), 'observer_sha256': runner.digest(HERE / 'observe.py'),
        'runner_sha256': runner.digest(Path(runner.__file__)),
        'python_sha256': runner.digest(Path(sys.executable)), 'python_version': sys.version.split()[0],
        'cli_sha256': runner.digest(runner.CODEX), 'settings': runner.SETTINGS,
        'selection_source_sha256': runner.digest(OLD / 'results/second-corpus-selection.json'),
        'fresh': selection, 'known_critical': [case['id'] for case in known],
        'case_sha256': {case['id']: runner.case_digest(case) for case in fresh + known},
        'fresh_sources': [{'id': key, 'repo': originals[key]['repo'], 'base_sha': originals[key]['base_sha'],
                           'fix_sha': originals[key]['fix_sha'], 'source_url': normalized_url(originals[key])} for key in selection],
        'independent_review': [{'case_id': selection[i], 'repeat': 0} for i in protocol['review_selection']['zero_based_selected_task_positions']],
        'prior_journal_sha256': prior_journals, 'no_prior_native_records_or_fix_or_url_overlap': True,
        'closed_v4_qualification_sha256': runner.digest(OLD / 'results/v4/release-qualification.json'),
        'closed_v4_source_report_sha256': closed['source_report_sha256'],
        'observer_preflight_sha256': runner.digest(HERE / 'results/observer-preflight.json'),
        'dependency_files_sha256': {p.relative_to(ROOT / 'testdeps').as_posix(): runner.digest(p)
                                    for p in sorted((ROOT / 'testdeps').rglob('*')) if p.is_file()}
    }
    immutable(HERE / 'results/freeze.json', frozen)
    if not (HERE / 'results/frozen-at.json').exists():
        assert not list((ROOT / 'runs').glob('**/started.json'))
        runner.write(HERE / 'results/frozen-at.json', {'frozen_at': runner.utc(), 'native_attempts': 0,
                     'freeze_sha256': runner.digest(HERE / 'results/freeze.json')})
    return fresh, known, frozen


def export(phase, records, frozen):
    out = HERE / 'results'
    rows, flags, issues, threads = [], [], [], []
    for record in records:
        key = record['key']
        logs = ROOT / 'runs' / phase / key
        raw = (logs / 'events.jsonl').read_text(encoding='utf-8')
        observation = observe(raw, SKILL.read_text(encoding='utf-8'))
        threads += observation['thread_ids']
        audit = scan(raw, body(SKILL.read_text(encoding='utf-8')))
        flags += [{'key': key, **flag} for flag in audit['flags']]
        workspace = Path(record['workspace'])
        if observation['malformed_lines'] or runner.digest(workspace / runner.SKILL_REL) != frozen['skill_sha256']:
            issues.append(key)
        patch = out / 'patches' / (key + '.patch')
        patch.parent.mkdir(parents=True, exist_ok=True)
        patch.write_text(runner.git(workspace, 'diff', '--no-ext-diff', '--binary', 'HEAD'), encoding='utf-8')
        row = {name: record.get(name) for name in FIELDS}
        row.update(raw_shell_only_body_read_observed=record['skill_body_read_observed'],
                   full_body_output_observation=observation,
                   raw_artifact_sha256={p.name: runner.digest(p) for p in sorted(logs.iterdir()) if p.is_file()},
                   patch=patch.relative_to(out).as_posix(), patch_sha256=runner.digest(patch))
        rows.append(public_value(row))
    if len(threads) != len(set(threads)) or len(threads) != len(records) or None in threads:
        issues.append('missing_or_reused_thread')
    (out / (phase + '-runs.jsonl')).write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows), encoding='utf-8')
    runner.write(PROJECT / '.local' / (phase + '-v5-trace-flags.json'), flags)
    summary = {
        'phase': phase, 'attempts': len(records), 'accepted': sum(r['accepted'] for r in records),
        'usage_complete': sum(r['usage_complete'] for r in records),
        'timeout': sum(r['timeout'] for r in records),
        'cli_completed_without_timeout': sum(not r['timeout'] and r['cli_exit_code'] == 0 for r in records),
        'full_body_outputs': sum(r['full_body_output_observation']['body_visible'] for r in rows),
        'cases': {key: {'attempts': sum(r['case_id'] == key for r in records),
                        'accepted': sum(r['case_id'] == key and r['accepted'] for r in records)}
                  for key in sorted({r['case_id'] for r in records})},
        'integrity_issues': issues, 'trace_flags_requiring_review': len(flags),
        'no_comparative_efficiency_or_industrial_qualification_claim': True
    }
    runner.write(out / (phase + '-summary.json'), summary)
    print(json.dumps(summary), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    fresh, known, frozen = prepare()
    if args.prepare_only:
        print(json.dumps({'prepared': True, 'fresh': len(fresh), 'known': len(known), 'planned_native': 72, 'new_native': 0}))
    else:
        resource_gate.install()
        for phase, cases, repeats in [('generalization', fresh, 3), ('known-critical', known, 1)]:
            records = runner.run_suite(ROOT, cases, phase, SKILL, repeats, ('with-skill',), 3)
            export(phase, records, frozen)
        assert runner.digest(OLD / 'results/v4/release-qualification.json') == frozen['closed_v4_qualification_sha256']
        for name, expected in frozen['closed_v4_source_report_sha256'].items():
            assert runner.digest(OLD / 'results/v4' / name) == expected

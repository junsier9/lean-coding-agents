"""Freeze and run a new v6 end-to-end beta-quality batch; no efficiency claim."""
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
sys.path.insert(0, str(HERE.parent / 'runtime-2026-10-27'))
sys.path.insert(0, str(HERE.parent / 'generalization-2026-10-26'))
import runner
import resource_gate
import mxc_proxy_transport as proxy_transport
from prepare_second_real import restore_case
from export_results import FIELDS, public_value
from audit_production_trace import scan
from observe import observe, body
from readiness import bind_readiness, READINESS

if not __debug__:
    raise RuntimeError('Run the frozen controller without optimization')

ROOT = PROJECT / '.local/production-runtime/generalization-v6-2026-10-27'
SKILL = ROOT / 'skill-input/SKILL.md'
TRANSPORT = proxy_transport.install(runner)


def immutable(path, value):
    if path.exists():
        assert runner.read(path) == value, 'Frozen input changed: ' + str(path)
    else:
        runner.write(path, value)


def observer_checks():
    # The observation implementation and its positive/negative controls are
    # already bound; this batch does not alter or backdate their method.
    previous = HERE.parent / 'generalization-2026-10-26'
    control = runner.read(previous / 'results/observer-preflight.json')
    assert runner.digest(previous / 'observe.py') == control['observer_sha256']
    assert control['all_passed'] is True
    current = (HERE.parent / 'development-2026-10-27/candidates/v6-python/SKILL.md').read_text(encoding='utf-8')
    positive = json.dumps({'type': 'item.completed', 'item': {'type': 'mcp_tool_call', 'result': {'content': [{'text': json.dumps({'content': current})}]}}})
    negative = json.dumps({'type': 'item.completed', 'item': {'type': 'mcp_tool_call', 'arguments': {'code': current}, 'result': {'content': [{'text': 'not read'}]}}})
    assert observe(positive, current)['body_visible'] and not observe(negative, current)['body_visible']
    immutable(HERE / 'results/observer-preflight.json', {
        'native_model_calls': 0, 'method_unchanged': True,
        'observer_sha256': runner.digest(previous / 'observe.py'),
        'existing_control_report_sha256': runner.digest(previous / 'results/observer-preflight.json')})


def prepare():
    protocol = runner.read(HERE / 'protocol.json')
    readiness_binding = bind_readiness(protocol, TRANSPORT)
    method_review = runner.read(HERE / 'results/readiness-binding-method-review.json')
    assert method_review['completed'] is True and not method_review['blocking_findings']
    for name, expected in method_review['source_sha256'].items():
        assert runner.digest(PROJECT / name) == expected, 'Method review refers to different code'
    oldroot = Path(runner.read(PROJECT / '.local/state.json')['runtime_root'])
    originals = {case['id']: case for case in runner.read(oldroot / 'eligible.json')['cases']}
    selection = runner.read(OLD / 'results/second-corpus-selection.json')['remaining_adequate'][20:]
    assert len(selection) == len(set(selection)) == 13
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
    # Cover paid but ungraded/unfinished historical attempts, not just journals.
    historical = [oldroot, *[p for p in (PROJECT / '.local/production-runtime').iterdir() if p.is_dir() and p != ROOT]]
    attempt_metadata = {}
    for previous in historical:
        for path in sorted(set(previous.glob('runs/*/*/*.json')) | set(previous.glob('runs/*/*.json'))):
            if path.name not in {'started.json', 'client-done.json', 'cli-checkpoint.json', 'record.json'}:
                continue
            item = runner.read(path)
            assert item.get('case_id') not in selection, 'Selected task has prior attempt metadata'
            if item.get('case_id'):
                seen.add(item['case_id'])
            attempt_metadata[str(path)] = runner.digest(path)
    assert len({(originals[key]['repo'], originals[key]['fix_sha']) for key in selection}) == 13
    assert len({normalized_url(originals[key]) for key in selection}) == 13
    prior_pairs = {(originals[key]['repo'], originals[key]['fix_sha']) for key in seen if key in originals}
    prior_urls = {normalized_url(originals[key]) for key in seen if key in originals}
    assert not prior_pairs & {(originals[key]['repo'], originals[key]['fix_sha']) for key in selection}
    assert not prior_urls & {normalized_url(originals[key]) for key in selection}
    ROOT.mkdir(parents=True, exist_ok=True)
    assert not list((PROJECT / '.local/production-runtime').glob('*/active-suite.lock'))
    assert not resource_gate.STOP.exists()
    assert runner.transport.settings() == runner.SETTINGS
    source = HERE.parent / 'development-2026-10-27/candidates/v6-python'
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
        # Bind actual archive, base-production and fix-overlay bytes even on resume.
        upstream = wrapper['upstream']
        for stage, field in [('base', 'base_production_sha256'), ('fix', 'overlay_sha256')]:
            for name, expected in upstream['preflight'][field].items():
                assert runner.digest(Path(upstream[stage + '_dir']) / name) == expected.upper()
        for stage in ('base', 'fix'):
            archive = upstream['preflight'][stage + '_archive']
            assert runner.digest(Path(archive['archive_path'])) == archive['sha256'].upper()
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
    readiness_freeze = runner.read(READINESS / 'freeze.json')
    closure = [Path(__file__), HERE / 'verify.py', HERE / 'readiness.py', HERE / 'close.py', HERE / 'prepare_reviews.py', HERE / 'incomplete.py',
               HERE / 'results/mxc-transport-method-review.json', HERE / 'results/readiness-binding-method-review.json', Path(runner.__file__), Path(runner.transport.__file__),
               Path(proxy_transport.__file__), HERE.parent / 'environment-revalidation-2026-10-27-v2/run.py',
               Path(runner.upstream_grader.__file__), Path(resource_gate.__file__),
               Path(__import__('prepare_second_real').__file__), Path(__import__('export_results').__file__),
               Path(__import__('audit_production_trace').__file__),
               HERE.parent / 'generalization-2026-10-26/observe.py',
               HERE.parent / 'generalization-2026-10-26/qualify_beta.py']
    immutable(PROJECT / '.local/v6-prior-attempt-metadata-index.json', attempt_metadata)
    frozen = {
        'license_sha256': runner.digest(PROJECT / 'LICENSE'),
        'source_closure_sha256': {p.relative_to(PROJECT).as_posix(): runner.digest(p) for p in closure},
        'prior_attempt_metadata_index_sha256': runner.digest(PROJECT / '.local/v6-prior-attempt-metadata-index.json'),
        'prior_attempt_metadata_count': len(attempt_metadata),
        'readiness_summary_sha256': runner.digest(READINESS / 'summary.json'),
        'readiness_freeze_sha256': runner.digest(READINESS / 'freeze.json'),
        'readiness_binding': readiness_binding,
        'transport': TRANSPORT,
        'original_failed_v6_summary_sha256': readiness_freeze['original_failed_v6_summary_sha256'],
        'original_recovery_result_sha256': readiness_freeze['original_recovery_result_sha256'],
        'stopped_proxy_only_window_sha256': readiness_freeze['stopped_proxy_only_window_sha256'],
        'closed_v5_qualification_sha256': runner.digest(HERE.parent / 'generalization-2026-10-26/results/beta-qualification.json'),
        'protocol_sha256': runner.digest(HERE / 'protocol.json'),
        'skill_sha256': runner.digest(SKILL), 'ui_sha256': runner.digest(ROOT / 'skill-input/agents/openai.yaml'),
        'driver_sha256': runner.digest(Path(__file__)), 'observer_sha256': runner.digest(HERE.parent / 'generalization-2026-10-26/observe.py'),
        'runner_sha256': runner.digest(Path(runner.__file__)),
        'python_sha256': runner.digest(Path(sys.executable)), 'python_version': sys.version.split()[0],
        'cli_sha256': runner.digest(runner.CODEX), 'settings': runner.SETTINGS,
        'selection_source_sha256': runner.digest(OLD / 'results/second-corpus-selection.json'),
        'fresh': selection, 'known_critical': [case['id'] for case in known],
        'native_schedule': {phase: runner.schedule(cases, phase, repeats, ('with-skill',))
                            for phase, cases, repeats in [('generalization', fresh, 3), ('known-critical', known, 1)]},
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
    runner.write(PROJECT / '.local' / (phase + '-v6-trace-flags.json'), flags)
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
    if not __debug__:
        raise RuntimeError('Run the frozen controller without -O; child optimized contract grading is separate')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    fresh, known, frozen = prepare()
    if args.prepare_only:
        print(json.dumps({'prepared': True, 'fresh': len(fresh), 'known': len(known), 'planned_native': 51, 'new_native': 0}))
    else:
        resource_gate.install()
        for phase, cases, repeats in [('generalization', fresh, 3), ('known-critical', known, 1)]:
            failed = ROOT / (phase + '-failures.json')
            if failed.exists():
                backup = ROOT / 'recovery' / (phase + '-' + runner.digest(failed) + '.json')
                if not backup.exists():
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(failed, backup)
                assert runner.digest(backup) == runner.digest(failed)
            try:
                records = runner.run_suite(ROOT, cases, phase, SKILL, repeats, ('with-skill',), 3)
            except RuntimeError:
                # Preserve the actual failed phase. Other unattempted scheduled
                # phases may proceed unless the resource-stop observation says otherwise.
                if not (ROOT / (phase + '-failures.json')).exists() or resource_gate.STOP.exists():
                    raise
                print(json.dumps({'phase': phase, 'status': 'incomplete_records_preserved', 'new_models_replacing_failures': 0}), flush=True)
                continue
            export(phase, records, frozen)
        assert runner.digest(OLD / 'results/v4/release-qualification.json') == frozen['closed_v4_qualification_sha256']
        for name, expected in frozen['closed_v4_source_report_sha256'].items():
            assert runner.digest(OLD / 'results/v4' / name) == expected

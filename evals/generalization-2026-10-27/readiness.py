"""Bind this prospective batch to the closed, verified environment v2 window."""
from pathlib import Path
import hashlib
import json
import sys
import zipfile

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent / 'production-2026-10-25'))
import runner

READINESS = HERE.parent / 'environment-revalidation-2026-10-27-v2/results'
CLASS = 'separate_known_contract_environment_revalidation_after_long_session_check'
REPORTS = ('freeze.json', 'frozen-at.json', 'summary.json', 'execution-result.json',
           'verification.json', 'evidence-retention.json', 'registration.json')


def require(value, message):
    if not value:
        raise ValueError(message)


def bind_readiness(protocol, transport):
    pinned = protocol['readiness_binding']
    require(pinned['directory'] == READINESS.relative_to(PROJECT).as_posix(), 'Readiness window differs')
    require(set(pinned['reports_sha256']) == set(REPORTS), 'Readiness report inventory differs')
    reports = {}
    for name in REPORTS:
        path = READINESS / name
        require(runner.digest(path) == pinned['reports_sha256'][name], 'Pinned readiness bytes changed: ' + name)
        reports[name] = runner.read(path)
    frozen, summary, execution, verified, retained = (reports[name] for name in
        ('freeze.json', 'summary.json', 'execution-result.json', 'verification.json', 'evidence-retention.json'))
    require(summary['evidence_class'] == frozen['evidence_class'] == CLASS, 'Wrong readiness evidence class')
    require(summary['development_gate_passed'] is True and execution['original_12_of12_gate_passed'] is True
            and verified['frozen_gate_passed'] is True, 'Readiness did not pass its original gate')
    for name in ('native_attempts', 'contract_accepted', 'normal_completions', 'full_body_outputs', 'usage_complete'):
        require(summary[name] == execution[name] == 12, 'Readiness count differs: ' + name)
    require(execution['status'] == 'completed_passed' and execution['normal_and_optimized_grade_logs_verified'] == 24
            and verified['raw_and_grade_checks_complete'] is True and verified['unique_native_threads'] == 12,
            'Readiness verification incomplete')
    require(not verified['trace_flags_requiring_context_review'], 'Readiness trace review unresolved')
    require(reports['registration.json']['new_native_maintenance_calls'] == 0, 'Original registration changed')
    require(reports['frozen-at.json']['freeze_sha256'] == pinned['reports_sha256']['freeze.json'], 'Readiness freeze timestamp binding differs')
    require(summary['transport'] == frozen['transport'] == transport, 'Readiness route or permission policy differs')
    require(frozen['skill_sha256'] == protocol['frozen_candidate_sha256']
            and frozen['ui_sha256'] == protocol['frozen_ui_sha256'], 'Readiness tested another candidate')
    require(frozen['settings'] == runner.SETTINGS == runner.transport.settings(), 'Readiness model/settings differ')
    require(frozen['cli_sha256'] == runner.digest(runner.CODEX)
            and frozen['python_sha256'] == runner.digest(Path(sys.executable)), 'Readiness binaries differ')
    checker = HERE.parent / 'environment-revalidation-2026-10-27-v2/verify_and_retain.py'
    legacy = HERE.parent / 'generalization-2026-10-26/qualify_beta.py'
    require(runner.digest(checker) == verified['checker_sha256'], 'Readiness checker changed')
    require(runner.digest(legacy) == verified['reused_checker_sha256'], 'Readiness raw checker changed')
    long_freeze = runner.read(HERE.parent / 'long-session-2026-10-27-v1/results/freeze.json')
    sources = {**frozen['source_sha256'], **long_freeze['source_sha256'], **long_freeze['historical_reports_sha256'],
               checker.relative_to(PROJECT).as_posix(): verified['checker_sha256'],
               legacy.relative_to(PROJECT).as_posix(): verified['reused_checker_sha256']}
    for name, sha in sources.items():
        require(runner.digest(PROJECT / name) == sha, 'Readiness or old failed evidence changed: ' + name)
    archive = PROJECT / '.local/environment-revalidation-v2-evidence.zip'
    require(retained['private_archive'] == archive.relative_to(PROJECT).as_posix(), 'Unexpected readiness archive')
    require(runner.digest(archive) == pinned['archive_sha256'] == retained['archive_sha256'], 'Readiness archive changed')
    with zipfile.ZipFile(archive) as bundle:
        require(bundle.testzip() is None, 'Readiness archive CRC failed')
        index = json.loads(bundle.read('entry-sha256.json'))
        require(set(bundle.namelist()) == set(index) | {'entry-sha256.json'}, 'Readiness archive inventory differs')
        for name, sha in index.items():
            require(hashlib.sha256(bundle.read(name)).hexdigest().upper() == sha, 'Readiness archived bytes differ')
        required = verified['required_raw_sha256']
        require(bool(required), 'Empty readiness evidence inventory')
        for name, sha in required.items():
            require(index.get(name) == sha, 'Actual readiness evidence omitted: ' + name)
        for name in ('freeze.json', 'summary.json', 'verification.json', 'execution-result.json'):
            require(index.get('public/results/' + name) == pinned['reports_sha256'][name], 'Archived readiness report differs: ' + name)
    return {'directory': pinned['directory'], 'evidence_class': CLASS,
            'reports_sha256': dict(pinned['reports_sha256']), 'source_sha256': sources,
            'archive': retained['private_archive'], 'archive_sha256': retained['archive_sha256'],
            'readiness_gate_passed': True, 'old_failures_preserved': True}

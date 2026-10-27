"""Repair one overstrict hidden oracle before any model evaluation."""
from pathlib import Path
import ast
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
import textwrap
import time

HERE = Path(__file__).resolve().parent
OLD = HERE.parent / "holdout-sealed"
digest = lambda data: hashlib.sha256(data).hexdigest().upper()
read = lambda path: json.loads(path.read_text(encoding="utf-8"))

REPLACEMENT = '''
def test_authorizer_every_request(self):
    events = []
    original_ensure = self.auth.ensure
    original_get = self.repo.get

    def ensure(principal, tenant):
        result = original_ensure(principal, tenant)
        events.append("authorized")
        return result

    def repository_get(tenant, doc_id):
        events.append("repository-read")
        return original_get(tenant, doc_id)

    class TracedCache(dict):
        def __contains__(cache, key):
            events.append("cache-read")
            return super().__contains__(key)

        def __getitem__(cache, key):
            events.append("cache-read")
            return super().__getitem__(key)

        def get(cache, key, default=None):
            events.append("cache-read")
            return super().get(key, default)

        def setdefault(cache, key, default=None):
            events.append("cache-read")
            return super().setdefault(key, default)

        def keys(cache):
            events.append("cache-read")
            return super().keys()

        def items(cache):
            events.append("cache-read")
            return super().items()

        def values(cache):
            events.append("cache-read")
            return super().values()

        def copy(cache):
            events.append("cache-read")
            return super().copy()

        def __iter__(cache):
            events.append("cache-read")
            return super().__iter__()

        def __len__(cache):
            events.append("cache-read")
            return super().__len__()

    self.auth.ensure = ensure
    self.repo.get = repository_get
    service = TenantService(self.repo, self.auth)
    service.cache = TracedCache(service.cache)
    for request in range(3):
        events.clear()
        self.assertEqual(service.get("u", "A", "doc"), "A-value")
        with self.subTest(request=request):
            self.assertIn("authorized", events)
            first_authorization = events.index("authorized")
            for position, event in enumerate(events):
                if event in ("cache-read", "repository-read"):
                    self.assertLess(first_authorization, position)
'''


def write_files(directory, files):
    directory.mkdir(parents=True, exist_ok=True)
    for name, contents in files.items():
        target = directory / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(contents, encoding="utf-8")


def run_case(case, label, files, optimized=False):
    directory = HERE / ".preflight-work" / case["id"] / label
    write_files(directory, files)
    write_files(directory, case["hidden_tests"])
    temporary = HERE / ".preflight-work" / "tmp"
    temporary.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8",
               TMP=str(temporary), TEMP=str(temporary), TMPDIR=str(temporary))
    command = [sys.executable, "-B"]
    if optimized:
        command.append("-O")
    command += ["-m", "unittest", "discover", "-s", ".", "-p", "test_contract.py", "-v"]
    started = time.monotonic()
    process = subprocess.run(command, cwd=directory, env=env, capture_output=True,
                             text=True, encoding="utf-8", timeout=30)
    output = process.stdout + process.stderr
    (directory / "unittest.log").write_text(output, encoding="utf-8")
    match = re.search(r"Ran (\d+) tests? in", output)
    failed = re.search(r"failures=(\d+)", output)
    errors = re.search(r"errors=(\d+)", output)
    return {"exit_code": process.returncode, "tests": int(match.group(1)) if match else 0,
            "failures": int(failed.group(1)) if failed else 0,
            "errors": int(errors.group(1)) if errors else 0,
            "seconds": round(time.monotonic() - started, 4),
            "log_sha256": digest(output.encode("utf-8")), "optimized": optimized}


def main():
    old_manifest_bytes = (OLD / "manifest.json").read_bytes()
    old_manifest = json.loads(old_manifest_bytes)
    old_hashes = {item["file"]: item["sha256"] for item in old_manifest["cases"]}
    cases, old_cases = [], {}
    for item in old_manifest["cases"]:
        source = OLD / item["file"]
        original = source.read_bytes()
        assert digest(original) == item["sha256"]
        case = json.loads(original)
        old_cases[case["id"]] = json.loads(original)
        if case["id"] == "h11":
            code = case["hidden_tests"]["test_contract.py"]
            tree = ast.parse(code)
            selected = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
                        and node.name == "test_authorizer_every_request"]
            assert len(selected) == 1
            method = selected[0]
            lines = code.splitlines(keepends=True)
            replacement = textwrap.indent(textwrap.dedent(REPLACEMENT).strip() + "\n", "    ")
            case["hidden_tests"]["test_contract.py"] = "".join(lines[:method.lineno - 1]) + replacement + "".join(lines[method.end_lineno:])
            assert case["task"] == old_cases[case["id"]]["task"]
            assert case["files"] == old_cases[case["id"]]["files"]
            assert case["reference_files"] == old_cases[case["id"]]["reference_files"]
            (HERE / item["file"]).write_text(json.dumps(case, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        else:
            (HERE / item["file"]).write_bytes(original)
        cases.append(case)

    results = []
    for case in cases:
        phases = {}
        for optimized in (False, True):
            for phase, files in (("initial", case["files"]), ("reference", case["reference_files"])):
                label = ("optimized_" if optimized else "") + phase
                phases[label] = run_case(case, label, files, optimized)
        eligible = all(phases[prefix + "initial"]["exit_code"] != 0
                       and phases[prefix + "reference"]["exit_code"] == 0
                       and phases[prefix + "initial"]["tests"] == phases[prefix + "reference"]["tests"] >= 8
                       for prefix in ("", "optimized_"))
        results.append({"id": case["id"], "phases": phases, "eligible": eligible})
    preflight = {"case_count": len(cases), "reference_tests": sum(row["phases"]["reference"]["tests"] for row in results),
                 "results": results, "all_eligible": all(row["eligible"] for row in results),
                 "interpreter": sys.version, "model_calls": 0, "network_calls": 0}
    (HERE / "preflight.json").write_text(json.dumps(preflight, indent=2) + "\n", encoding="utf-8")
    assert preflight["all_eligible"]

    case = next(case for case in cases if case["id"] == "h11")
    original_app = case["reference_files"]["app.py"]
    marker = "        self.authorizer.ensure(principal, tenant)\n"
    assert original_app.count(marker) == 1
    extra = original_app.replace(marker, marker + marker)
    skip_hit = original_app.replace(marker, "")
    old_cache_block = "        key = (tenant, doc_id)\n        if key not in self.cache:\n"
    new_cache_block = ("        key = (tenant, doc_id)\n"
                       "        if key in self.cache:\n"
                       "            return self.cache[key]\n"
                       "        self.authorizer.ensure(principal, tenant)\n"
                       "        if key not in self.cache:\n")
    assert old_cache_block in skip_hit
    skip_hit = skip_hit.replace(old_cache_block, new_cache_block)
    data_before_authorization = original_app.replace(marker,
        "        self.cache.get((tenant, doc_id))\n" + marker)
    variants = {"legal_extra_authorization": (extra, True),
                "invalid_skip_authorization_on_hit": (skip_hit, False),
                "invalid_read_before_authorization": (data_before_authorization, False)}
    probes = []
    for name, (source, should_pass) in variants.items():
        files = dict(case["reference_files"])
        files["app.py"] = source
        phases = {"optimized" if optimized else "normal": run_case(case, "oracle-" + name + ("-O" if optimized else ""), files, optimized)
                  for optimized in (False, True)}
        correct = all((result["exit_code"] == 0) == should_pass and result["tests"] == 10
                      for result in phases.values())
        probes.append({"variant": name, "expected_pass": should_pass, "source_sha256": digest(source.encode("utf-8")),
                       "phases": phases, "fidelity_pass": correct})
    assert all(probe["fidelity_pass"] for probe in probes)
    (HERE / "oracle-fidelity-probes.json").write_text(json.dumps({"probes": probes, "all_passed": True,
        "model_visible_source_changed": False, "model_calls": 0}, indent=2) + "\n", encoding="utf-8")

    new_hashes = {item["file"]: digest((HERE / item["file"]).read_bytes()) for item in old_manifest["cases"]}
    changed = [name for name in old_hashes if old_hashes[name] != new_hashes[name]]
    assert changed == ["h11.json"]
    for case in cases:
        original = old_cases[case["id"]]
        for key in ("task", "files", "reference_files", "id", "family", "critical", "checks"):
            assert case[key] == original[key]
    assert digest((OLD / "manifest.json").read_bytes()) == digest(old_manifest_bytes)
    assert all(digest((OLD / name).read_bytes()) == expected for name, expected in old_hashes.items())
    manifest = {"schema_version": 2, "purpose": old_manifest["purpose"], "case_count": 12,
                "reference_tests": preflight["reference_tests"],
                "cases": [{**item, "sha256": new_hashes[item["file"]]} for item in old_manifest["cases"]],
                "preflight_sha256": digest((HERE / "preflight.json").read_bytes()),
                "all_eligible": True, "source": old_manifest["source"],
                "repair_of_manifest_sha256": digest(old_manifest_bytes)}
    (HERE / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    repair = {"repaired_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "old_directory_unchanged": True, "old_manifest_sha256": digest(old_manifest_bytes),
              "new_manifest_sha256": digest((HERE / "manifest.json").read_bytes()),
              "changed_case_files": changed, "old_case_sha256": old_hashes["h11.json"],
              "new_case_sha256": new_hashes["h11.json"],
              "only_changed_field": "h11.hidden_tests.test_contract.py:test_authorizer_every_request",
              "reason": "The contract requires authorization on every request before reading data, but specifies no upper bound on safe extra authorization calls.",
              "runtime_behavior_enforced": ["authorization on every request, including hits", "authorization completes before observed cache/repository reads"],
              "safe_extra_authorization_accepted": True,
              "cache_hit_bypass_rejected": True, "read_before_authorization_rejected": True,
              "other_authentication_and_tenant_isolation_checks_unchanged": True,
              "task_files_reference_files_unchanged": True,
              "normal_and_optimized_reference_passed": True,
              "normal_and_optimized_initial_still_fail": True,
              "no_candidate_or_model_results_read": True, "model_calls": 0, "network_calls": 0}
    (HERE / "oracle-repair.json").write_text(json.dumps(repair, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    freeze = {"frozen_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "manifest_sha256": repair["new_manifest_sha256"], "case_file_sha256": new_hashes,
              "preflight_sha256": manifest["preflight_sha256"],
              "oracle_repair_sha256": digest((HERE / "oracle-repair.json").read_bytes()),
              "oracle_fidelity_probes_sha256": digest((HERE / "oracle-fidelity-probes.json").read_bytes()),
              "repair_script_sha256": digest(Path(__file__).read_bytes()),
              "candidate_and_model_outcomes_not_used": True}
    (HERE / "freeze.json").write_text(json.dumps(freeze, indent=2) + "\n", encoding="utf-8")
    (HERE / "manifest.sha256").write_text(repair["new_manifest_sha256"] + "\n", encoding="ascii")
    print(json.dumps({"case_count": 12, "reference_tests": preflight["reference_tests"],
                      "all_preflight_eligible": True, "all_fidelity_probes_passed": True,
                      "manifest_sha256": repair["new_manifest_sha256"], "old_directory_unchanged": True}))


if __name__ == "__main__":
    main()

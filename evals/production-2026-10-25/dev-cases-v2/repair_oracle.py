"""Derive an immutable-input-compatible oracle repair; never runs a model."""
from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
OLD = HERE.parent / "dev-cases"
EXPECTED_OLD_MANIFEST = "BADC2B941EC773B782C914E911F7099A57AEFEFAA2F3A20727F821CCD2F9480D"
ADDITION = '''    def __enter__(self):
        self.inner.__enter__()
        return self
    def __exit__(self, exc_type, exc_value, traceback):
        if self.inner.in_transaction:
            if exc_type is None:
                try:
                    self.commit()
                except BaseException:
                    self.rollback()
                    raise
            else:
                self.rollback()
        return False
'''


def sha(data):
    return hashlib.sha256(data).hexdigest().upper()


def preflight(cases, optimized=False):
    mode = "optimized" if optimized else "normal"
    results = []
    for item in cases:
        stages = {}
        for stage in ("base", "reference"):
            with tempfile.TemporaryDirectory(prefix="fixture-", dir=HERE) as temporary:
                directory = Path(temporary).resolve()
                if not directory.is_relative_to(HERE):
                    raise ValueError("Temporary fixture escaped the new oracle directory")
                files = dict(item["files"])
                if stage == "reference":
                    files.update(item["reference_files"])
                files.update(item["hidden_tests"])
                for name, source in files.items():
                    target = directory / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(source, encoding="utf-8")
                env = os.environ.copy()
                env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8", PYTHONPATH=str(directory))
                command = [sys.executable] + (["-O"] if optimized else []) + [
                    "-B", "-m", "unittest", "discover", "-s", ".", "-p", "test_contract.py", "-v"]
                result = subprocess.run(command, cwd=directory, env=env, capture_output=True, text=True,
                                        encoding="utf-8", timeout=30)
                output = result.stdout + result.stderr
                log = HERE / "preflight" / mode / item["id"] / (stage + ".txt")
                log.parent.mkdir(parents=True, exist_ok=True)
                log.write_text(output, encoding="utf-8")
                match = re.search(r"Ran (\d+) tests? in", output)
                stages[stage] = {
                    "exit_code": result.returncode, "tests_ran": int(match.group(1)) if match else None,
                    "log": log.relative_to(HERE).as_posix(), "log_sha256": sha(log.read_bytes()),
                }
        results.append({"id": item["id"], **stages})
    return results


def main():
    old_files = {path.name: path.read_bytes() for path in OLD.glob("d[0-9][0-9].json")}
    old_manifest = (OLD / "manifest.json").read_bytes()
    if sha(old_manifest) != EXPECTED_OLD_MANIFEST or len(old_files) != 8:
        raise ValueError("Frozen development source does not match the recorded manifest")
    manifest = json.loads(old_manifest.decode("utf-8"))
    for entry in manifest:
        if sha(old_files[entry["file"]]) != entry["sha256"]:
            raise ValueError("Frozen fixture hash mismatch: " + entry["file"])

    original = json.loads(old_files["d06.json"].decode("utf-8"))
    old_tests = original["hidden_tests"]["test_contract.py"]
    anchor = "    def execute(self, sql, params=()):\n"
    if old_tests.count(anchor) != 1:
        raise ValueError("Unexpected Tracked source layout")
    new_tests = old_tests.replace(anchor, ADDITION + anchor, 1)
    old_text = old_files["d06.json"].decode("utf-8")
    old_encoded = json.dumps(old_tests, ensure_ascii=False)
    new_encoded = json.dumps(new_tests, ensure_ascii=False)
    if old_text.count(old_encoded) != 1:
        raise ValueError("Hidden test JSON must occur exactly once")
    new_text = old_text.replace(old_encoded, new_encoded, 1)
    repaired = json.loads(new_text)
    if new_tests.replace(ADDITION, "", 1) != old_tests:
        raise ValueError("Repair changed text outside the new context methods")
    for field in original:
        if field != "hidden_tests" and repaired[field] != original[field]:
            raise ValueError("Repair changed field: " + field)
    old_tree, new_tree = ast.parse(old_tests), ast.parse(new_tests)
    old_contract = next(node for node in old_tree.body if isinstance(node, ast.ClassDef) and node.name == "Contract")
    new_contract = next(node for node in new_tree.body if isinstance(node, ast.ClassDef) and node.name == "Contract")
    contract_dump = ast.dump(old_contract, include_attributes=False)
    if contract_dump != ast.dump(new_contract, include_attributes=False):
        raise ValueError("Original Contract assertions changed")
    old_methods = [node.name for node in old_contract.body if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")]
    if len(old_methods) != 12:
        raise ValueError("Original 12-test contract changed")

    for name, data in old_files.items():
        (HERE / name).write_bytes(new_text.encode("utf-8") if name == "d06.json" else data)
    new_manifest = []
    for entry in manifest:
        new_entry = dict(entry)
        new_entry["sha256"] = sha((HERE / entry["file"]).read_bytes())
        new_manifest.append(new_entry)
    (HERE / "manifest.json").write_text(json.dumps(new_manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    cases = [json.loads((HERE / entry["file"]).read_text(encoding="utf-8")) for entry in new_manifest]
    normal = preflight(cases)
    optimized = preflight(cases, optimized=True)
    all_base_fail = lambda rows: all(row["base"]["exit_code"] != 0 and row["base"]["tests_ran"] for row in rows)
    all_reference_pass = lambda rows: all(row["reference"]["exit_code"] == 0 and row["reference"]["tests_ran"] for row in rows)
    report = {
        "cases": 8, "reference_tests_total": sum(row["reference"]["tests_ran"] or 0 for row in normal),
        "base_all_rejected": all_base_fail(normal), "reference_all_passed": all_reference_pass(normal),
        "optimized_reference_tests_total": sum(row["reference"]["tests_ran"] or 0 for row in optimized),
        "optimized_base_all_rejected": all_base_fail(optimized),
        "optimized_reference_all_passed": all_reference_pass(optimized),
        "python_version": sys.version, "results": normal, "optimized_results": optimized,
        "model_calls": 0, "synthetic_development_cases_only": True,
    }
    (HERE / "preflight.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if not all((report["base_all_rejected"], report["reference_all_passed"],
                report["optimized_base_all_rejected"], report["optimized_reference_all_passed"])):
        raise RuntimeError("Repaired-oracle preflight failed")

    unchanged = all((OLD / name).read_bytes() == data for name, data in old_files.items())
    unchanged = unchanged and (OLD / "manifest.json").read_bytes() == old_manifest
    if not unchanged:
        raise RuntimeError("Original frozen fixture directory changed")
    repair = {
        "kind": "SQLite test-double fidelity repair only",
        "old_directory": "dev-cases", "new_directory": "dev-cases-v2",
        "old_manifest_sha256": sha(old_manifest), "new_manifest_sha256": sha((HERE / "manifest.json").read_bytes()),
        "old_d06_sha256": sha(old_files["d06.json"]), "new_d06_sha256": sha((HERE / "d06.json").read_bytes()),
        "old_hidden_tests_sha256": sha(old_tests.encode("utf-8")), "new_hidden_tests_sha256": sha(new_tests.encode("utf-8")),
        "only_changed_case": "d06", "only_changed_field": "hidden_tests.test_contract.py",
        "only_added_methods": ["Tracked.__enter__", "Tracked.__exit__"],
        "all_other_d06_fields_equal": True, "all_other_case_files_byte_identical": True,
        "outside_added_context_methods_hidden_test_text_identical": True,
        "original_contract_ast_sha256": sha(contract_dump.encode("utf-8")),
        "original_12_test_methods_preserved": old_methods,
        "semantics": [
            "__enter__ delegates the real open-connection check, returns the wrapper, and does not start a transaction.",
            "__exit__ acts only when the wrapped SQLite connection has an active transaction.",
            "Normal exit commits through the existing commit method, preserving fail_commit injection.",
            "Commit failure rolls back through the existing rollback method and re-raises the original failure.",
            "An exceptional BaseException exit rolls back and returns False to propagate the exception.",
            "Context exit never calls close; the application remains responsible for releasing the connection.",
        ],
        "old_frozen_directory_unmodified": unchanged,
        "model_outputs_read_or_modified": False, "model_calls": 0, "original_scores_modified": False,
        "preflight": {key: report[key] for key in (
            "cases", "reference_tests_total", "base_all_rejected", "reference_all_passed",
            "optimized_reference_tests_total", "optimized_base_all_rejected", "optimized_reference_all_passed")},
        "fidelity_probe": "fidelity_probe.py; real SQLite versus the repaired class extracted from the hidden test AST",
    }
    fidelity = {}
    for mode, flags, filename in (
        ("normal", [], "fidelity.json"),
        ("optimized", ["-O"], "fidelity-optimized.json"),
    ):
        command = [sys.executable, *flags, "-B", str(HERE / "fidelity_probe.py"), "--output", filename]
        completed = subprocess.run(command, cwd=HERE, check=True, capture_output=True, text=True,
                                   encoding="utf-8", timeout=30)
        probe = json.loads((HERE / filename).read_text(encoding="utf-8"))
        if not probe["passed"] or probe["tests_ran"] != 12:
            raise RuntimeError("SQLite fidelity probe failed: " + mode)
        fidelity[mode] = {"file": filename, "sha256": sha((HERE / filename).read_bytes()),
                          "tests_ran": probe["tests_ran"], "passed": probe["passed"],
                          "optimize": probe["optimize"]}
    repair["fidelity_results"] = fidelity
    repair["fidelity_probe_sha256"] = sha((HERE / "fidelity_probe.py").read_bytes())
    (HERE / "oracle-repair.json").write_text(json.dumps(repair, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("cases", "reference_tests_total", "base_all_rejected",
          "reference_all_passed", "optimized_reference_tests_total", "optimized_base_all_rejected",
          "optimized_reference_all_passed")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

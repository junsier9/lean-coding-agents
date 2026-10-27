"""Restore the fixed, independently screened second corpus; no model calls."""
from pathlib import Path
import copy
import json
import shutil
import runner

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent.parent
ROOT = PROJECT / ".local/production-runtime/real-v4"


def restore_case(original, root, runtime_root=None):
    case = copy.deepcopy(original)
    (root / "archives").mkdir(parents=True, exist_ok=True)
    for stage in ("base", "fix"):
        archive = case["preflight"][stage + "_archive"]
        source = Path(archive["archive_path"])
        assert runner.digest(source) == archive["sha256"].upper()
        destination = root / "archives" / source.name
        if not destination.exists():
            shutil.copyfile(source, destination)
        assert runner.digest(destination) == archive["sha256"].upper()
        snapshot = root / "snapshots" / case["id"] / stage
        restored = runner.upstream_grader.archive(case["repo"], case[stage + "_sha"], snapshot, root)
        assert restored["sha256"].upper() == archive["sha256"].upper()
        case[stage + "_dir"] = str(snapshot)
        archive["archive_path"] = str(destination)
    case["preflight"]["runtime_root"] = str(runtime_root or root)
    for relative, expected in case["preflight"]["overlay_sha256"].items():
        assert runner.digest(Path(case["fix_dir"]) / relative) == expected.upper()
    for relative, expected in case["preflight"]["base_production_sha256"].items():
        assert runner.digest(Path(case["base_dir"]) / relative) == expected.upper()
    return case


def prepare():
    selection_path = HERE / "results/second-corpus-selection.json"
    selection = runner.read(selection_path)
    old = Path(runner.read(PROJECT / ".local/state.json")["runtime_root"])
    original = {case["id"]: case for case in runner.read(old / "eligible.json")["cases"]}
    protocol_sha = runner.digest(HERE / "protocol-v4.json")
    assert ROOT.resolve().is_relative_to((PROJECT / ".local").resolve())
    assert not (ROOT / "records.jsonl").exists()
    assert not (ROOT / "real-selection.json").exists(), "Do not silently replace prepared inputs"
    assert len(set(selection["formal"] + selection["replication"])) == 40
    ROOT.mkdir(parents=True, exist_ok=True)
    (ROOT / "archives").mkdir(exist_ok=True)
    case_hashes = {}
    for phase in ("formal", "replication"):
        for case_id in selection[phase]:
            case = restore_case(original[case_id], ROOT)
            wrapper = {"id": case_id, "repo": case["repo"], "family": "upstream-maintenance",
                       "task": case["task"], "source_dir": case["base_dir"], "upstream": case}
            target = ROOT / (phase + "-cases") / (case_id + ".json")
            runner.write(target, wrapper)
            case_hashes[case_id] = runner.digest(target)
    deps = ROOT / "testdeps"
    assert not deps.exists()
    shutil.copytree(PROJECT / ".local/production-runtime/real-v3/testdeps", deps,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    assert (deps / "pytest").is_dir() and (deps / "pytest_timeout.py").is_file()
    allocation = {"prepared_at": runner.utc(), "protocol_sha256": protocol_sha,
                  "selection_sha256": runner.digest(selection_path), "native_model_calls": 0,
                  "formal": selection["formal"], "replication": selection["replication"],
                  "case_file_sha256": case_hashes,
                  "independently_screened_before_model_calls": True,
                  "original_case_text_guard_and_archive_bytes_unchanged": True}
    runner.write(ROOT / "real-selection.json", allocation)
    runner.write(HERE / "results/v4/real-selection.json", allocation)
    print(json.dumps({"prepared_tasks": len(case_hashes), "native_model_calls": 0,
                      "durable_runtime": ".local/production-runtime/real-v4"}))


if __name__ == "__main__":
    prepare()

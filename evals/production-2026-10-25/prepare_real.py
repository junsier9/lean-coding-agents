"""Allocate untouched upstream cases before new solver outcomes; restore pinned inputs."""
from pathlib import Path
import argparse
import copy
import hashlib
import json
import random
import shutil
import sys

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent / "full-validation"))
import preflight


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def prepare(old_root, root):
    root, old_root = root.resolve(), old_root.resolve()
    assert root.is_relative_to((PROJECT / ".local").resolve())
    protocol_hash = hashlib.sha256((HERE / "protocol.json").read_bytes()).hexdigest().upper()
    if (root / "real-selection.json").exists():
        saved = read(root / "real-selection.json")
        assert saved["protocol_sha256"] == protocol_hash
        return {"already_prepared": True, "formal": len(saved["formal"]), "replication": len(saved["replication"])}
    assert not (root / "records.jsonl").exists(), "Never rebuild model inputs after runs begin"
    prior = [json.loads(line) for line in (old_root / "records.jsonl").read_text(encoding="utf-8").splitlines()]
    used = {row["case_id"] for row in prior}
    corpus = read(old_root / "eligible.json")
    available = [case for case in corpus["cases"] if case["id"] not in used]
    assert len(used) == 50 and len(available) == 127
    groups = {}
    for case in available:
        groups.setdefault(case["repo"], []).append(case["id"])
    rng = random.Random(20261025)
    for group in groups.values():
        group.sort()
        rng.shuffle(group)
    order = []
    while any(groups.values()):
        names = sorted(name for name, group in groups.items() if group)
        rng.shuffle(names)
        for name in names:
            order.append(groups[name].pop())
    selected = {"formal": order[:20], "replication": order[20:40]}
    assert not set(selected["formal"]) & set(selected["replication"])
    assert not used & set(order[:40])
    root.mkdir(parents=True, exist_ok=True)
    cases = {case["id"]: case for case in available}
    root.joinpath("archives").mkdir(exist_ok=True)
    wrappers, summaries = [], []
    for case_id in order[:40]:
        case = copy.deepcopy(cases[case_id])
        for stage in ("base", "fix"):
            original = case["preflight"][stage + "_archive"]
            source = Path(original["archive_path"])
            assert preflight.digest(source) == original["sha256"]
            target = root / "archives" / source.name
            if not target.exists():
                shutil.copyfile(source, target)
            assert preflight.digest(target) == original["sha256"]
            directory = root / "snapshots" / case_id / stage
            restored = preflight.archive(case["repo"], case[stage + "_sha"], directory, root)
            assert restored["sha256"] == original["sha256"]
            case[stage + "_dir"] = str(directory)
            case["preflight"][stage + "_archive"]["archive_path"] = str(target)
        case["preflight"]["runtime_root"] = str(root)
        for relative, expected in case["preflight"]["overlay_sha256"].items():
            assert preflight.digest(Path(case["fix_dir"]) / relative) == expected
        wrapper = {"id": case_id, "repo": case["repo"], "family": "upstream-maintenance",
                   "task": case["task"], "source_dir": case["base_dir"], "upstream": case}
        wrappers.append(wrapper)
        summaries.append({key: case.get(key) for key in ("id", "repo", "issue_or_pr_url", "base_sha", "fix_sha")})
    deps = root / "testdeps"
    if not deps.exists():
        shutil.copytree(old_root / "testdeps", deps, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    assert (deps / "pytest").is_dir() and (deps / "pytest_timeout.py").is_file()
    for wrapper in wrappers:
        phase = "formal" if wrapper["id"] in selected["formal"] else "replication"
        write(root / (phase + "-cases") / (wrapper["id"] + ".json"), wrapper)
    allocation = {"seed": 20261025, "protocol_sha256": protocol_hash, "untouched_pool": 127,
                  "old_case_ids_excluded": sorted(used), **selected, "remaining_reserve": order[40:],
                  "selection_independent_of_new_model_outcomes": True, "cases": summaries}
    write(root / "real-selection.json", allocation)
    write(HERE / "results" / "real-selection.json", allocation)
    return {"formal": 20, "replication": 20, "untouched_reserve": 87,
            "repositories": len({wrapper["repo"] for wrapper in wrappers})}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-root", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.old_root, args.root)))

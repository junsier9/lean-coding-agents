"""Prepare the prespecified blinded pairs without scores, skill text, or labels."""
from pathlib import Path
import argparse
import json
import shutil
import subprocess
import runner

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent.parent
REAL = PROJECT / ".local/production-runtime/real-v3"
OUT = PROJECT / ".local/production-blind"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def copy_source(source, destination, names):
    destination.mkdir(parents=True)
    for relative in names:
        parts = Path(relative).parts
        if any(part in {".git", ".agents", ".benchmark-deps", "__pycache__", ".pytest_cache"}
               or part.startswith((".pytest-", ".test-tmp")) for part in parts) or relative == "TASK.md":
            continue
        path = (source / relative).resolve()
        assert path.is_relative_to(source.resolve())
        if path.is_file():
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)


def prepare(phase, only_pair=None, version="v3"):
    real = REAL if version == "v3" else PROJECT / ".local/production-runtime/real-v4"
    out = OUT if version == "v3" else PROJECT / ".local/production-blind-v4"
    plan_path = HERE / ("results/blind-review-plan.json" if version == "v3" else "results/v4-blind-review-plan.json")
    labels_path = PROJECT / (".local/production-review-labels.json" if version == "v3" else ".local/production-review-labels-v4.json")
    plan = read(plan_path)
    labels = {entry["pair"]: entry for entry in read(labels_path)}
    journal = [json.loads(line) for line in (real / "records.jsonl").read_text(encoding="utf-8").splitlines()]
    records = {row["key"]: row for row in journal}
    tasks = []
    for selected in plan["selected"]:
        if phase and selected["phase"] != phase:
            continue
        if only_pair and selected["pair"] != only_pair:
            continue
        pair = selected["pair"]
        wrapper = read(real / (selected["phase"] + "-cases") / (selected["case_id"] + ".json"))
        directory = out / pair
        if not directory.exists():
            for label in ("A", "B"):
                arm = labels[pair][label]
                key = f"{selected['phase']}--{selected['case_id']}--r{selected['repeat']}--{arm}"
                assert key in records, "Prespecified pair is not complete: " + key
            directory.mkdir(parents=True)
            base = Path(wrapper["source_dir"])
            copy_source(base, directory / "base", [path.relative_to(base).as_posix() for path in base.rglob("*") if path.is_file()])
            for label in ("A", "B"):
                arm = labels[pair][label]
                key = f"{selected['phase']}--{selected['case_id']}--r{selected['repeat']}--{arm}"
                source = Path(records[key]["workspace"])
                names = subprocess.run(["git", "ls-files", "-z"], cwd=source, check=True, capture_output=True).stdout.decode("utf-8").split("\0")
                names += subprocess.run(["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=source,
                                        check=True, capture_output=True).stdout.decode("utf-8").split("\0")
                copy_source(source, directory / label, sorted(set(filter(None, names))))
                patch = subprocess.run(["git", "diff", "--no-ext-diff", "--binary", "HEAD", "--", ".",
                                        ":(exclude).agents", ":(exclude)TASK.md"], cwd=source, check=True, capture_output=True).stdout
                (directory / (label + ".patch")).write_bytes(patch)
            runner.write(directory / "task.json", {"pair": pair, "task": wrapper["task"]})
        tasks.append({"pair": pair, "task": wrapper["task"], "directory": str(directory)})
    filename = "tasks-" + ("pair" + only_pair if only_pair else phase or "all") + ".json"
    assert tasks, "No prespecified pair matched"
    runner.write(out / filename, {"tasks": tasks, "rubric": plan["rubric"],
                                 "limits": "No arm labels, costs, skill files, reference fix or hidden grader provided. Do not infer a preference from fewer lines or more tests."})
    print(json.dumps({"pairs_prepared": len(tasks), "tasks": str(out / filename)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("formal", "replication"))
    parser.add_argument("--pair", choices=[f"{number:02}" for number in range(1, 9)])
    parser.add_argument("--version", choices=("v3", "v4"), default="v3")
    args = parser.parse_args()
    prepare(args.phase, args.pair, args.version)

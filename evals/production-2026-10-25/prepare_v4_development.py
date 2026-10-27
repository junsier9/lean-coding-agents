"""Prepare two explicitly revealed development tasks, never fresh holdouts."""
from pathlib import Path
import json
import shutil
import runner
from prepare_second_real import restore_case

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent.parent
BASE = PROJECT / ".local/production-runtime"
ROOT = BASE / "development-v4-inputs"


def prepare():
    assert ROOT.resolve().is_relative_to(BASE.resolve())
    assert not (ROOT / "manifest.json").exists(), "Keep prepared development inputs immutable"
    old = Path(runner.read(PROJECT / ".local/state.json")["runtime_root"])
    original = next(case for case in runner.read(old / "eligible.json")["cases"] if case["id"] == "xb-059-cachetools-436")
    case = restore_case(original, ROOT, runtime_root=BASE)
    regression = {"id": case["id"], "repo": case["repo"], "family": "revealed-compatibility-regression",
                  "task": case["task"], "source_dir": case["base_dir"], "upstream": case}
    runner.write(ROOT / "cases" / (case["id"] + ".json"), regression)
    d09 = HERE / "development-next/d09.json"
    assert runner.digest(d09) == "866B0E1BA4B22BC041CAEB6EB874E75D762B878797FA9ACE56F49544AD5577AE"
    (ROOT / "cases").mkdir(exist_ok=True)
    shutil.copyfile(d09, ROOT / "cases/d09.json")
    manifest = {"prepared_at": runner.utc(), "evidence_class": "development_regression_only",
                "native_model_calls": 0,
                "case_files_sha256": {path.name: runner.digest(path) for path in sorted((ROOT / "cases").glob("*.json"))},
                "d09_independently_authored_and_preflighted": True,
                "real_case_previously_revealed_and_never_counted_as_fresh": True,
                "original_task_source_and_guard_bytes_preserved": True}
    runner.write(ROOT / "manifest.json", manifest)
    runner.write(HERE / "results/v4/development-inputs.json", manifest)
    print(json.dumps({"development_cases": 2, "native_model_calls": 0}))


if __name__ == "__main__":
    prepare()

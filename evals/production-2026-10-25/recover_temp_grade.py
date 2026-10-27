"""Recover an observed Windows temp-directory copy error without model work."""
from pathlib import Path
import argparse
import json
import shutil
import runner

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent.parent
ROOT = PROJECT / ".local/production-runtime/real-v3"
SKILL = HERE / "candidates/v3-python-v2/SKILL.md"
TEMP_NAMES = {
    "formal--extra-a2-click-3805--r1--with-skill": ".tmp-sentinel-checks",
    "formal--xb-013-click-3079--r0--baseline": ".task-local-tmp",
    "formal--extra-a2-click-3805--r2--baseline": ".maintenance-pytest-tmp",
    "formal--xb-013-click-3079--r0--with-skill": ".pt-shared-default",
}


def recover(key):
    assert not (ROOT / "active-suite.lock").exists(), "Wait until the active controller releases the suite"
    with runner.controller_lock(ROOT, "grading-recovery"):
        return recover_idle(key)


def recover_idle(key):
    temp_name = TEMP_NAMES[key]
    phase, case_id, repeat_text, arm = key.split("--")
    assert phase in {"formal", "replication"} and arm in {"baseline", "with-skill"}
    logs = ROOT / "runs" / phase / key
    assert not (logs / "record.json").exists(), "Never regrade a completed record"
    checkpoint = runner.read(logs / "cli-checkpoint.json")
    assert checkpoint["cli_completed"] is True
    assert checkpoint["grading_attempts"] and all(item["status"] == "failed" for item in checkpoint["grading_attempts"])
    assert any("[WinError 5]" in item.get("error", "") and temp_name in item.get("error", "") for item in checkpoint["grading_attempts"])
    workspace = Path(checkpoint["workspace"])
    tracked = runner.git(workspace, "ls-files", "-z").split("\0")
    assert not any(name == temp_name or name.startswith(temp_name + "/") for name in tracked)
    before = {name: runner.digest(workspace / name) for name in tracked if name and (workspace / name).is_file()}
    originals = ROOT / "recovery" / key
    originals.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(logs / "cli-checkpoint.json", originals / "original-cli-checkpoint.json")
    manifest = runner.read(ROOT / (phase + "-manifest.json"))
    case = runner.read(ROOT / (phase + "-cases") / (case_id + ".json"))
    assert manifest["skill_sha256"] == runner.digest(SKILL)
    runner.upstream_grader.IGNORED_DIRS = runner.upstream_grader.IGNORED_DIRS | {temp_name}

    def prohibit_model(*args, **kwargs):
        raise AssertionError("Recovery must never call the model")

    runner.launch_cli = prohibit_model
    result = runner.run_one(case, ROOT, phase, int(repeat_text[1:]), arm, SKILL,
                            manifest, runner.preflight_case(case, ROOT)["expected"])
    assert before == {name: runner.digest(workspace / name) for name in before}
    report = {"recovered_at": runner.utc(), "key": key, "accepted": result["accepted"],
              "model_calls": 0, "original_tracked_bytes_unchanged": True,
              "only_additional_copy_exclusion": temp_name,
              "all_original_failed_grading_cost_retained": True,
              "frozen_guard_and_source_code_unchanged": True,
              "original_checkpoint_sha256": runner.digest(originals / "original-cli-checkpoint.json"),
              "grading_attempts": result["grading_attempts"]}
    runner.write(originals / "recovery-report.json", report)
    print(json.dumps(report))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key", required=True)
    recover(parser.parse_args().key)

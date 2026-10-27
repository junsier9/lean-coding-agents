"""Export scalar evidence and patches without publishing raw model prompts."""
from pathlib import Path
import argparse
import hashlib
import json
import shutil
import subprocess
import runner
from study_config import study

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent.parent
BASE = PROJECT / ".local/production-runtime"
OUT = HERE / "results"
DELIVERY_REPORTS = {"evidence-index.json", "evidence-integrity.json",
                    "release-qualification.json", "delivery-validation.json"}
FIELDS = ("key", "phase", "case_id", "repo", "repeat", "arm", "started_at", "finished_at",
          "skill_sha256", "case_sha256", "accepted", "completed", "cli_completed", "cli_exit_code", "timeout",
          "client_seconds", "grade_seconds", "wall_seconds", "usage_complete", "usage",
          "input_tokens", "cached_input_tokens", "output_tokens", "total_tokens", "command_items",
          "command_failures", "skill_body_read_observed", "diff_stat", "grade", "grading_attempts",
          "expected_load", "operation_pass", "routing_check_pass", "changed_tracked_paths", "skill_unchanged",
          "skill_files_unchanged")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def save_jsonl(path, values):
    path.write_text("".join(json.dumps(value, ensure_ascii=False) + "\n" for value in values), encoding="utf-8")


def public_value(value):
    """Keep replay commands legible without distributing machine-specific roots."""
    if isinstance(value, dict):
        return {key: public_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [public_value(item) for item in value]
    if isinstance(value, str):
        for path, label in ((PROJECT, "{PROJECT_ROOT}"),
                            (Path(runner.PYTHON).parent, "{PYTHON_RUNTIME}"),
                            (Path.home(), "{USER_HOME}")):
            # shutil.Error includes nested repr strings with doubled escapes.
            for width in (4, 2, 1):
                value = value.replace(str(path).replace("\\", "\\" * width), label)
            value = value.replace(path.as_posix(), label)
    return value


def export(version="v3"):
    config = study(version)
    out = config.out
    out.mkdir(parents=True, exist_ok=True)
    branches, real_branch = tuple(config.expected_counts), config.real_branch
    counts, all_rows, manifests = {}, [], {}
    for branch in branches:
        root = BASE / branch
        for source in sorted(root.glob("*manifest.json")):
            destination = out / "manifests" / branch / source.name
            runner.write(destination, public_value(runner.read(source)))
            manifests[destination.relative_to(out).as_posix()] = digest(destination)
        if branch.startswith("routing"):
            records = [runner.read(path) for path in sorted((root / "runs").glob("*/record.json"))]
        else:
            records = rows(root / "records.jsonl") if (root / "records.jsonl").exists() else []
        exported = []
        for record in records:
            key = record["key"]
            item = {field: record.get(field) for field in FIELDS}
            item["stream_error_count"] = len(record.get("stream_errors", []))
            item["evidence_branch"] = branch
            workspace = Path(record["workspace"]).resolve()
            assert workspace.is_relative_to(root)
            data = subprocess.run(["git", "diff", "--no-ext-diff", "--binary", "HEAD"], cwd=workspace,
                                  check=True, capture_output=True).stdout
            patch = out / "patches" / branch / (key + ".patch")
            patch.parent.mkdir(parents=True, exist_ok=True)
            patch.write_bytes(data)
            item["tracked_patch"] = patch.relative_to(out).as_posix()
            item["tracked_patch_sha256"] = digest(patch)
            listing = subprocess.run(["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=workspace,
                                     check=True, capture_output=True)
            names = listing.stdout.decode("utf-8").split("\0")
            item["untracked_listing_complete"] = not bool(listing.stderr.strip())
            item["untracked_listing_warning"] = listing.stderr.decode("utf-8", errors="replace").strip() or None
            item["untracked_files"] = []
            for name in filter(None, names):
                source = (workspace / name).resolve()
                assert source.is_relative_to(workspace)
                if not source.is_file():
                    continue
                details = {"path": name, "bytes": source.stat().st_size, "sha256": digest(source)}
                if source.suffix in {".py", ".md", ".txt"}:
                    destination = out / "generated-files" / branch / key / name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, destination)
                    details["exported_content"] = destination.relative_to(out).as_posix()
                item["untracked_files"].append(details)
            logs = root / "runs" / key if branch.startswith("routing") else root / "runs" / record["phase"] / key
            item["local_raw_evidence"] = {}
            for name in ("started.json", "events.jsonl", "stderr.txt", "prompt.txt", "final.txt", "record.json", "client-done.json", "cli-checkpoint.json"):
                if (logs / name).is_file():
                    item["local_raw_evidence"][name] = {"sha256": digest(logs / name),
                        "private_archive_path": (logs / name).relative_to(BASE).as_posix()}
            exported.append(public_value(item))
        save_jsonl(out / ("runs-" + branch + ".jsonl"), exported)
        counts[branch] = len(exported)
        all_rows.extend(exported)
    corrected = BASE / "dev-v3-oracle-v2/development-records.json"
    if version == "v3" and corrected.exists():
        values = [{field: row.get(field) for field in FIELDS + ("original_accepted", "original_grade", "oracle_version", "model_rerun")}
                  for row in runner.read(corrected)]
        save_jsonl(out / "development-oracle-v2-derived.jsonl", public_value(values))
    corpus = []
    for phase in ("formal", "replication"):
        for path in sorted((BASE / real_branch / (phase + "-cases")).glob("*.json")):
            case = runner.read(path)["upstream"]
            corpus.append({key: case.get(key) for key in ("id", "repo", "task", "issue_or_pr_url", "base_sha", "fix_sha",
                           "production_files", "regression_test_files", "guard_paths")} |
                          {"phase": phase, "overlay_sha256": case["preflight"]["overlay_sha256"],
                           "fixed_guard_tests": case["preflight"]["guard_fix"]["tests_ran"],
                           "archives": {stage: {key: value for key, value in case["preflight"][stage + "_archive"].items() if key != "archive_path"}
                                        for stage in ("base", "fix")}})
    runner.write(out / "real-corpus-manifest.json", corpus)
    index = {"exported_at": runner.utc(), "study_version": version, "actual_native_run_counts": counts,
             "expected_native_run_counts": config.expected_counts,
             "counts_are_finalized_records_not_live_ungraded_client_checkpoints": True,
             "derived_oracle_regrades_are_not_additional_model_calls": True,
             "raw_events_not_public": True,
             "machine_path_roots_in_numeric_records_replaced_by_named_placeholders": True,
             "configured_model_settings": runner.SETTINGS,
             "run_manifest_sha256": manifests,
             "delivery_reports_outside_data_index": sorted(DELIVERY_REPORTS - {"evidence-index.json"}),
             "artifact_sha256": {path.name: digest(path) for path in sorted(out.glob("*.json*")) if path.name not in DELIVERY_REPORTS}}
    runner.write(out / "evidence-index.json", index)
    print(json.dumps({"native_runs_exported": len(all_rows), "phase_counts": counts, "real_corpus": len(corpus)}))
    return index


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", choices=("v3", "v4"), default="v3")
    export(parser.parse_args().version)

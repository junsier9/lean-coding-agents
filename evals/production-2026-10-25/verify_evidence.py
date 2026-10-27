"""Verify exported observations against retained native records and bytes."""
from pathlib import Path
import argparse
import hashlib
import json
import subprocess
import zipfile
import runner
from replay_analysis import replay
from export_results import BASE, FIELDS, digest, public_value, rows
from study_config import study

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent.parent


def verify(require_archive=False, version="v3"):
    config = study(version)
    out, expected_counts = config.out, config.expected_counts
    issues, totals, raw_entries, untracked_limits = [], {}, {}, []
    index = runner.read(out / "evidence-index.json")
    for name, expected in index["artifact_sha256"].items():
        if digest(out / name) != expected:
            issues.append({"issue": "indexed_artifact_hash", "file": name})
    for name, expected in index["run_manifest_sha256"].items():
        if digest(out / name) != expected:
            issues.append({"issue": "indexed_run_manifest_hash", "file": name})
    real = []
    for branch, planned in expected_counts.items():
        root = BASE / branch
        if branch.startswith("routing"):
            native = [runner.read(path) for path in sorted((root / "runs").glob("*/record.json"))]
        else:
            native = rows(root / "records.jsonl") if (root / "records.jsonl").exists() else []
        exported_path = out / ("runs-" + branch + ".jsonl")
        exported = rows(exported_path) if exported_path.exists() else []
        originals = {row["key"]: row for row in native}
        keys = [row["key"] for row in exported]
        totals[branch] = len(keys)
        if len(keys) != len(set(keys)) or set(keys) != set(originals) or len(originals) != len(native):
            issues.append({"issue": "record_key_set", "branch": branch})
        for row in exported:
            key = row["key"]
            source = originals[key]
            if row.get("untracked_listing_complete") is False:
                untracked_limits.append({"branch": branch, "key": key,
                                         "warning": row["untracked_listing_warning"]})
            expected = public_value({field: source.get(field) for field in FIELDS})
            if expected != {field: row.get(field) for field in FIELDS}:
                issues.append({"issue": "native_numeric_or_grade_mismatch", "branch": branch, "key": key})
            if row["usage_complete"]:
                cache = row["cached_input_tokens"]
                if row["total_tokens"] != row["input_tokens"] + row["output_tokens"] or (cache is not None and not 0 <= cache <= row["input_tokens"]):
                    issues.append({"issue": "usage_arithmetic", "branch": branch, "key": key})
            elif row["total_tokens"] is not None:
                issues.append({"issue": "unknown_usage_filled", "branch": branch, "key": key})
            if not branch.startswith("routing") and abs(row["wall_seconds"] - row["client_seconds"] - row["grade_seconds"]) > .000003:
                issues.append({"issue": "time_arithmetic", "branch": branch, "key": key})
            grade = row.get("grade")
            if row.get("accepted") and (not grade or grade["exit_code"] != 0 or grade["failed"] or grade["errors"] or grade["tests_ran"] <= 0):
                issues.append({"issue": "accepted_without_nonzero_passing_grade", "branch": branch, "key": key})
            patch = out / row["tracked_patch"]
            raw_patch = subprocess.run(["git", "diff", "--no-ext-diff", "--binary", "HEAD"],
                                       cwd=source["workspace"], check=True, capture_output=True).stdout
            if digest(patch) != row["tracked_patch_sha256"] or hashlib.sha256(raw_patch).hexdigest().upper() != row["tracked_patch_sha256"]:
                issues.append({"issue": "patch_bytes", "branch": branch, "key": key})
            for file in row["untracked_files"]:
                native_path = (Path(source["workspace"]) / file["path"]).resolve()
                assert native_path.is_relative_to(Path(source["workspace"]).resolve())
                if digest(native_path) != file["sha256"]:
                    issues.append({"issue": "native_untracked_bytes", "branch": branch, "key": key, "file": file["path"]})
                if "exported_content" in file and digest(out / file["exported_content"]) != file["sha256"]:
                    issues.append({"issue": "exported_untracked_bytes", "branch": branch, "key": key, "file": file["path"]})
            for entry in row["local_raw_evidence"].values():
                relative = entry["private_archive_path"]
                raw = (BASE / relative).resolve()
                assert raw.is_relative_to(BASE.resolve())
                if digest(raw) != entry["sha256"]:
                    issues.append({"issue": "raw_evidence_hash", "branch": branch, "key": key, "file": relative})
                raw_entries[relative] = entry["sha256"]
        if branch == config.real_branch:
            real = exported
    complete = totals == expected_counts
    analyses = {}
    if complete:
        for phase in ("formal", "replication"):
            current = replay(real, phase)
            analyses[phase] = current == runner.read(out / (phase + config.analysis_suffix))
            if not analyses[phase]:
                issues.append({"issue": "analysis_not_reproduced", "phase": phase})
    archive_path = config.archive_path
    archive_verified = False
    if archive_path.exists():
        with zipfile.ZipFile(archive_path) as archive:
            assert archive.testzip() is None
            names = set(archive.namelist())
            for relative, expected in raw_entries.items():
                name = "production-runtime/" + relative
                if name not in names or hashlib.sha256(archive.read(name)).hexdigest().upper() != expected:
                    issues.append({"issue": "private_archive_raw_hash", "file": relative})
        archive_verified = not any(issue["issue"] == "private_archive_raw_hash" for issue in issues)
    elif require_archive:
        issues.append({"issue": "private_archive_missing"})
    report = {"verified_at": runner.utc(), "study_version": version, "planned_native_runs": sum(expected_counts.values()),
              "actual_native_runs": sum(totals.values()), "branch_counts": totals,
              "all_planned_runs_present": complete, "integrity_issues": issues,
              "analyses_reproduced_from_exported_rows": analyses,
              "raw_artifacts_checked": len(raw_entries), "private_archive_verified": archive_verified,
              "untracked_listing_limitations_for_explicit_review": untracked_limits,
              "qualification_evidence_integrity_pass": complete and not issues and archive_verified and all(analyses.values()),
              "scope": "Exact local bytes and arithmetic checks; not a proof of scientific generalization or correctness of all source contracts."}
    runner.write(out / "evidence-integrity.json", report)
    print(json.dumps(report))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-archive", action="store_true")
    parser.add_argument("--version", choices=("v3", "v4"), default="v3")
    args = parser.parse_args()
    verify(args.require_archive, args.version)

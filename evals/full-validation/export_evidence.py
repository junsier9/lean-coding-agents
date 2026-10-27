"""Export auditable scalar results and generated patches, without user account data."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

from run_experiment import HERE, read, write, utc

FIELDS = ("key", "phase", "case_id", "repo", "repeat", "arm", "started_at", "finished_at",
          "cli_exit_code", "timeout", "client_seconds", "grade_seconds", "wall_seconds",
          "usage_complete", "usage", "total_tokens", "input_tokens", "cached_input_tokens", "output_tokens",
          "accepted", "completed", "command_items", "command_failures", "stream_errors", "diff_stat", "grading_attempts")
GRADE_FIELDS = ("exit_code", "tests_collected", "tests_ran", "passed", "failed", "errors", "skipped", "seconds")


def export(root: Path) -> dict:
    rows = [json.loads(line) for line in (root / "records.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    out = HERE / "results"
    patches = out / "patches"
    patches.mkdir(exist_ok=True)
    exported = []
    digests = {}
    git = shutil.which("git")
    for row in rows:
        item = {field: row.get(field) for field in FIELDS}
        item["grade"] = {field: row["grade"].get(field) for field in GRADE_FIELDS}
        item["grade"]["failures"] = row["grade"].get("failures", [])
        workspace = Path(row["workspace"]).resolve()
        assert workspace.is_relative_to(root)
        patch = subprocess.run([git, "diff", "--no-ext-diff", "--binary", "HEAD"], cwd=workspace,
                               check=True, capture_output=True).stdout
        name = row["key"] + ".patch"
        path = patches / name
        path.write_bytes(patch)
        item["tracked_patch"] = "patches/" + name
        item["tracked_patch_sha256"] = hashlib.sha256(patch).hexdigest().upper()
        names = subprocess.run([git, "ls-files", "--others", "--exclude-standard", "-z"], cwd=workspace,
                               check=True, capture_output=True).stdout.decode("utf-8").split("\x00")
        item["untracked_files"] = []
        for relative in filter(None, names):
            candidate = (workspace / relative).resolve()
            assert candidate.is_relative_to(workspace)
            if candidate.is_file():
                item["untracked_files"].append({"path": relative, "bytes": candidate.stat().st_size,
                                                "sha256": hashlib.sha256(candidate.read_bytes()).hexdigest().upper()})
        # Original event streams remain local; record hashes detect later changes.
        logroot = root / "runs" / row["phase"] / row["key"]
        item["local_raw_evidence"] = {}
        for filename in ("events.jsonl", "stderr.txt", "prompt.txt", "final.txt", "record.json", "cli-checkpoint.json"):
            source = logroot / filename
            if source.is_file():
                item["local_raw_evidence"][filename] = hashlib.sha256(source.read_bytes()).hexdigest().upper()
        exported.append(item)
    (out / "runs.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in exported), encoding="utf-8")
    sources = []
    for source in sorted(HERE.glob("sources*.json")):
        sources.append({"file": source.name, "sha256": hashlib.sha256(source.read_bytes()).hexdigest().upper()})
    corpus = read(root / "eligible.json")
    cases = [{key: case.get(key) for key in ("id", "repo", "issue_or_pr_url", "task", "fix_sha", "base_sha",
              "production_files", "regression_test_files", "test_paths", "guard_paths")} |
             {"fixed_guard_tests": case["preflight"]["guard_fix"]["tests_ran"],
              "overlay_sha256": case["preflight"]["overlay_sha256"],
              "preflight": {stage: {key: case["preflight"][stage].get(key) for key in GRADE_FIELDS}
                            for stage in ("base", "fix", "guard_fix")},
              "archives": {stage: {key: value for key, value in case["preflight"][stage].items() if key != "archive_path"}
                           for stage in ("base_archive", "fix_archive")}}
             for case in corpus["cases"]]
    excluded = [{key: case.get(key) for key in ("id", "repo", "issue_or_pr_url", "exclusion_reason")} for case in corpus["excluded"]]
    write(out / "corpus-manifest.json", {"eligible": cases, "excluded": excluded, "sources": sources})
    for name in ("partition.json", "extension-manifest.json"):
        if (root / name).exists():
            write(out / ("final-" + name), read(root / name))
    for path in sorted(out.glob("*.json*")):
        if path.name != "evidence-index.json":
            digests[path.name] = hashlib.sha256(path.read_bytes()).hexdigest().upper()
    manifest = {"exported_at": utc(), "run_count": len(exported), "artifact_sha256": digests,
                "raw_events_location": "Local runtime runs/; absolute root recorded in ignored .local/state.json.",
                "untracked_file_contents": "Retained in local isolated workspaces; exported names and hashes only.",
                "account_identifiers_exported": False}
    write(out / "evidence-index.json", manifest)
    return {"runs_exported": len(exported), "patches_exported": len(exported), "eligible": len(cases), "excluded": len(excluded)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(export(args.root.resolve())))

"""Append independently preflighted cases without inspecting solver outcomes."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import random

from run_experiment import read, write, utc


def extend(root: Path, additions: list[Path]) -> dict:
    corpus = read(root / "eligible.json")
    partition = read(root / "partition.json")
    assert not partition["formal"] and not partition["replication"]
    assert corpus["metadata"]["complete"] and corpus["metadata"]["guard_complete"]
    if not (root / "eligible-initial.json").exists():
        write(root / "eligible-initial.json", corpus)
    manifest = read(root / "extension-manifest.json") if (root / "extension-manifest.json").exists() else []
    known = {str(Path(item["path"]).resolve()) for item in manifest}
    seen_ids = {case["id"] for case in corpus["cases"] + corpus["excluded"]}
    seen_urls = {case["issue_or_pr_url"].rstrip("/").lower() for case in corpus["cases"]}
    seen_sha = {case["fix_sha"] for case in corpus["cases"]}
    appended = []
    for path in additions:
        path = path.resolve()
        assert path.is_relative_to(root), "Only local preflight outputs are accepted."
        if str(path) in known:
            continue
        extra = read(path)
        assert extra["metadata"]["complete"] and extra["metadata"]["guard_complete"]
        accepted, duplicates = [], []
        for case in extra["cases"]:
            url = case["issue_or_pr_url"].rstrip("/").lower()
            if case["id"] in seen_ids or url in seen_urls or case["fix_sha"] in seen_sha:
                duplicates.append(case["id"])
                continue
            seen_ids.add(case["id"])
            seen_urls.add(url)
            seen_sha.add(case["fix_sha"])
            accepted.append(case)
        ordered = sorted(case["id"] for case in accepted)
        random.Random(20261024 + len(manifest) + 1).shuffle(ordered)
        corpus["cases"].extend(accepted)
        corpus["excluded"].extend(extra["excluded"])
        partition["reserve"].extend(ordered)
        appended.extend(ordered)
        manifest.append({"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest().upper(),
                         "at": utc(), "eligible_added": len(accepted), "excluded_in_preflight": len(extra["excluded"]),
                         "cross_source_duplicates": duplicates, "appended_order": ordered})
        known.add(str(path))
    partition["case_count"] = len(corpus["cases"])
    partition["corpus_extended_at"] = utc()
    corpus["metadata"]["extended_eligible_count"] = len(corpus["cases"])
    corpus["metadata"]["extension_manifest"] = str(root / "extension-manifest.json")
    write(root / "eligible.json", corpus)
    partition["eligible_sha256_at_extension"] = hashlib.sha256((root / "eligible.json").read_bytes()).hexdigest().upper()
    write(root / "partition.json", partition)
    write(root / "extension-manifest.json", manifest)
    return {"eligible": len(corpus["cases"]), "reserve": len(partition["reserve"]), "new_cases": len(appended)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--additions", type=Path, nargs="+", required=True)
    args = parser.parse_args()
    print(json.dumps(extend(args.root.resolve(), args.additions)))

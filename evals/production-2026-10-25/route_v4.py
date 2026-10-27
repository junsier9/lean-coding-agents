"""Reuse the frozen nine routing requests for a new, isolated candidate gate."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import runner
import route_smoke_v2 as original
from export_results import public_value

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent.parent
BASE = PROJECT / ".local/production-runtime"


def run(skill, iteration=1):
    conclusion = HERE / "results/v3-python-v2-confirmation-conclusion.json"
    assert conclusion.exists() and runner.read(conclusion)["real_valid_runs"] == 240, "First confirmation must complete before v4 model work"
    assert not list(BASE.glob("*/active-suite.lock")), "Another native suite is active"
    root = BASE / f"routing-v4-iteration{iteration}"
    requests = original.route_cases()
    manifest = {"skill_sha256": runner.digest(skill), "settings": runner.transport.settings(),
                "cases": {case["id"]: runner.case_digest(case) for case in requests}}
    assert manifest["settings"] == runner.SETTINGS
    root.mkdir(parents=True, exist_ok=True)
    path = root / "routing-manifest.json"
    if path.exists():
        assert runner.read(path) == manifest, "Use a new iteration for a changed candidate"
    else:
        runner.write(path, manifest)
    original.ROOT, original.SKILL = root, skill
    records = {}
    with runner.controller_lock(root, "routing"):
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {pool.submit(original.run, case): case["id"] for case in requests}
            for future in as_completed(futures):
                record = future.result()
                assert record["skill_sha256"] == manifest["skill_sha256"]
                records[futures[future]] = record
    assert runner.digest(skill) == manifest["skill_sha256"]
    report = {"completed_at": runner.utc(), "skill_sha256": manifest["skill_sha256"],
              "model_runs": len(records), "included_in_efficiency_statistics": False,
              "routing_qualified": len(records) == 9 and all(row["routing_check_pass"] for row in records.values()),
              "cases": {key: {name: row.get(name) for name in ("expected_load", "operation_pass", "routing_check_pass",
                                                              "skill_body_read_observed", "grade", "skill_unchanged")}
                        for key, row in records.items()},
              "limitations": ["Known nine-request regression, not a new independent routing sample",
                              "No estimate of global selection reliability or invisible host activation"]}
    runner.write(HERE / f"results/v4/routing-iteration{iteration}.json", public_value(report))
    print(json.dumps({"routing_qualified": report["routing_qualified"], "model_runs": len(records)}))
    return report

"""Audit production native traces; keyword matches require adjudication."""
from collections import Counter
from pathlib import Path
import hashlib
import json
import re
import argparse
import runner
from study_config import V3_COUNTS, study

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent.parent
BASE = PROJECT / ".local/production-runtime"
BRANCH_COUNTS = V3_COUNTS
UI_SHA256 = "E786C393174A36E6CAD32DD46325844B3036FC65619117984EBA926A93274E1A"
MARKERS = ("Prove the invariant at its use.", "Keep failures observable.",
           "Require a current reason for each layer.",
           "Verify the changed contract, then finish.",
           "without an extra planning, approval, review, or delegation round.")
PATTERNS = {
    "external_reference_or_download": re.compile(r"Invoke-WebRequest|Invoke-RestMethod|\bcurl\b|\bwget\b|git\s+(?:clone|fetch|pull)\b|urllib\.request|requests\.(?:get|post)\s*\(", re.I),
    "package_install": re.compile(r"\bpip(?:\.exe)?\s+install\b|\b(?:npm|pnpm|yarn)\s+(?:install|add)\b", re.I),
    "commit_or_publish": re.compile(r"\bgit\s+(?:commit|push)\b|\bgh\s+(?:pr\s+create|repo\s+create)\b", re.I),
    "grader_or_other_run_reference": re.compile(r"records\.jsonl|[/\\](?:grades|snapshots)[/\\]|production-runtime|production-2026-10-25|holdout-sealed|eligible(?:-[a-z0-9-]+)?\.json", re.I),
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def scan(events, expected_body=None):
    result = {"thread_ids": [], "commands": 0, "command_failures": 0,
              "malformed_lines": 0, "body_visible": False,
              "item_types": Counter(), "event_types": Counter(), "flags": []}
    outputs = []
    for line in events.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            result["malformed_lines"] += 1
            continue
        result["event_types"][event.get("type", "unknown")] += 1
        if event.get("type") == "thread.started":
            result["thread_ids"].append(event.get("thread_id"))
        if event.get("type") != "item.completed":
            continue
        item = event.get("item", {})
        kind = item.get("type", "unknown")
        result["item_types"][kind] += 1
        if any(word in kind for word in ("web_search", "mcp_tool", "collab", "spawn")):
            result["flags"].append({"category": "non_shell_tool_review", "item_type": kind,
                                    "server": item.get("server"), "tool": item.get("tool")})
        if kind != "command_execution":
            continue
        result["commands"] += 1
        result["command_failures"] += int(item.get("exit_code") not in (None, 0))
        command = item.get("command", "")
        output = item.get("aggregated_output", "")
        outputs.append(output)
        variants = (MARKERS, tuple("Preserve observable contracts." if marker == "Keep failures observable." else marker for marker in MARKERS))
        result["body_visible"] |= any(all(marker in output for marker in group) for group in variants)
        for category, expression in PATTERNS.items():
            if expression.search(command):
                result["flags"].append({"category": category, "command": command})
    if expected_body is not None:
        body = re.sub(r"\s+", " ", expected_body).strip()
        observed = re.sub(r"\s+", " ", "\n".join(outputs)).strip()
        result["body_visible"] = bool(body and body in observed)
    return result


def audit(version="v3"):
    config = study(version)
    summaries, flags, threads, integrity_issues = {}, [], [], []
    total = 0
    for branch, expected in config.expected_counts.items():
        root = BASE / branch
        routing = branch.startswith("routing")
        if routing:
            records = [runner.read(path) for path in sorted((root / "runs").glob("*/record.json"))]
        else:
            path = root / "records.jsonl"
            records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.exists() else []
        summary = {"expected_runs": expected, "actual_runs": len(records), "commands": 0,
                   "malformed_lines": 0, "body_visible_by_arm": Counter(),
                   "runs_by_arm": Counter(), "potential_flags": Counter(), "item_types": Counter()}
        keys = [row["key"] for row in records]
        if len(set(keys)) != len(keys):
            integrity_issues.append({"branch": branch, "issue": "duplicate_record_key"})
        for row in records:
            key, arm = row["key"], row["arm"]
            logs = root / "runs" / key if routing else root / "runs" / row["phase"] / key
            workspace = Path(row["workspace"])
            skill = workspace / ".agents/skills/lean-coding/SKILL.md"
            body = (re.sub(r"\A---\n.*?\n---\n", "", skill.read_text(encoding="utf-8"), count=1, flags=re.S).strip()
                    if skill.is_file() else None)
            found = scan((logs / "events.jsonl").read_text(encoding="utf-8"), body)
            ids = found["thread_ids"]
            if len(ids) != 1 or not ids[0]:
                integrity_issues.append({"branch": branch, "key": key, "issue": "thread_event_count", "count": len(ids)})
            threads.extend(ids)
            for count in ("commands", "malformed_lines"):
                summary[count] += found[count]
            summary["runs_by_arm"][arm] += 1
            summary["body_visible_by_arm"][arm] += int(found["body_visible"])
            summary["item_types"].update(found["item_types"])
            if found["commands"] != row.get("command_items"):
                integrity_issues.append({"branch": branch, "key": key, "issue": "command_count_mismatch"})
            if found["body_visible"] != row.get("skill_body_read_observed"):
                integrity_issues.append({"branch": branch, "key": key, "issue": "body_visibility_mismatch"})
            ui = skill.parent / "agents/openai.yaml"
            # Routing arms are implicit/negative, both with an installed skill.
            if arm != "baseline":
                if not skill.is_file() or digest(skill) != row["skill_sha256"]:
                    integrity_issues.append({"branch": branch, "key": key, "issue": "installed_skill_hash"})
                # Compare frozen installed bytes, including native Windows EOL.
                if not ui.is_file() or digest(ui) != UI_SHA256:
                    integrity_issues.append({"branch": branch, "key": key, "issue": "installed_ui_hash"})
            elif skill.exists():
                integrity_issues.append({"branch": branch, "key": key, "issue": "baseline_skill_present"})
            for item in found["flags"]:
                flags.append({"branch": branch, "key": key, **item})
                summary["potential_flags"][item["category"]] += 1
        summaries[branch] = summary
        total += len(records)
    unique = len(threads) == len(set(threads)) == total and None not in threads
    if not unique:
        integrity_issues.append({"issue": "thread_id_reused_or_missing"})
    report = {"audited_at": runner.utc(), "study_version": version, "actual_native_runs": total,
              "planned_native_runs": sum(config.expected_counts.values()),
              "all_planned_runs_present": all(v["actual_runs"] == v["expected_runs"] for v in summaries.values()),
              "branches": summaries, "distinct_thread_per_run": unique,
              "integrity_issues": integrity_issues, "potential_flag_count": len(flags),
              "flags_are_not_automatic_exclusions": True,
              "limits": ["Emitted events do not reveal unlogged host activity or model attention.",
                         "Keyword matches require source or command review and do not change inclusion.",
                         "Read-only routing negatives remain valid only if the prespecified routing check passes."]}
    runner.write(config.out / "trace-audit.json", report)
    runner.write(PROJECT / (".local/production-trace-flags.json" if version == "v3" else ".local/production-trace-flags-v4.json"), flags)
    print(json.dumps({key: report[key] for key in ("actual_native_runs", "all_planned_runs_present", "distinct_thread_per_run", "integrity_issues", "potential_flag_count")}))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", choices=("v3", "v4"), default="v3")
    audit(parser.parse_args().version)

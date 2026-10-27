"""Read emitted traces for treatment visibility and potential protocol violations.

Keyword flags require human review; they are not automatic exclusions. Raw
flagged commands stay in ignored .local/, never in the public audit summary.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import re

HERE = Path(__file__).resolve().parent
PATTERNS = {
    "external_reference_or_download": re.compile(r"Invoke-WebRequest|Invoke-RestMethod|\bcurl\b|\bwget\b|git\s+(?:clone|fetch|pull)\b|urllib\.request|requests\.(?:get|post)\s*\(", re.I),
    "package_install": re.compile(r"\bpip(?:\.exe)?\s+install\b|\b(?:npm|pnpm|yarn)\s+(?:install|add)\b", re.I),
    "commit_or_publish": re.compile(r"\bgit\s+(?:commit|push)\b|\bgh\s+(?:pr\s+create|repo\s+create)\b", re.I),
    "grader_or_other_run_reference": re.compile(r"eligible(?:-[a-z0-9-]+)?\.json|records\.jsonl|preflight\.py|preregistered-protocol|[/\\]snapshots[/\\]|[/\\]grades[/\\]", re.I),
}
BODY_MARKERS = ("Locate the contract", "Make failure distinguishable", "Remove defenses only with evidence",
                "Spend complexity on the current requirement", "Keep the change coherent", "Verify, then finish")


def audit(root: Path):
    rows = [json.loads(line) for line in (root / "records.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    phases = defaultdict(lambda: {"runs": 0, "commands": 0, "event_types": Counter(), "item_types": Counter(),
                                  "thread_ids": [], "malformed_trace_lines": 0, "skill_body_visible": Counter(),
                                  "skill_file_successful_reads": Counter(), "potential_flags": Counter()})
    flags = []
    for row in rows:
        phase = phases[row["phase"]]
        phase["runs"] += 1
        log = (root / "runs" / row["phase"] / row["key"] / "events.jsonl").resolve()
        assert log.is_relative_to(root)
        thread_ids, read_skill, body_visible = [], False, False
        for line in log.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                phase["malformed_trace_lines"] += 1
                continue
            phase["event_types"][event.get("type", "unknown")] += 1
            if event.get("type") == "thread.started":
                thread_ids.append(event.get("thread_id"))
            if event.get("type") != "item.completed":
                continue
            item = event.get("item", {})
            kind = item.get("type", "unknown")
            phase["item_types"][kind] += 1
            if any(word in kind for word in ("web_search", "mcp_tool", "collab", "spawn")):
                flags.append({"run": row["key"], "phase": row["phase"], "category": "non_shell_tool_review", "item_type": kind,
                              "server": item.get("server"), "tool": item.get("tool")})
                phase["potential_flags"]["non_shell_tool_review"] += 1
            if kind != "command_execution":
                continue
            phase["commands"] += 1
            command = item.get("command", "")
            output = item.get("aggregated_output", "")
            visible_here = all(marker in output for marker in BODY_MARKERS)
            body_visible |= visible_here
            for category, expression in PATTERNS.items():
                if expression.search(command):
                    flags.append({"run": row["key"], "phase": row["phase"], "category": category, "command": command})
                    phase["potential_flags"][category] += 1
            # A later command in the same shell can exit 1 after Get-Content
            # already exposed the complete skill; use the observed body too.
            if "skill-frozen.md" in command and (item.get("exit_code") == 0 or visible_here):
                read_skill = True
        phase["thread_ids"].extend(thread_ids)
        phase["skill_file_successful_reads"][row["arm"]] += int(read_skill)
        phase["skill_body_visible"][row["arm"]] += int(body_visible)
        if len(thread_ids) != 1 or not thread_ids[0]:
            flags.append({"run": row["key"], "phase": row["phase"], "category": "thread_identity_review", "thread_event_count": len(thread_ids)})
            phase["potential_flags"]["thread_identity_review"] += 1
    summary = {}
    all_ids = []
    for name, values in phases.items():
        ids = values.pop("thread_ids")
        all_ids.extend(ids)
        summary[name] = {**values, "unique_thread_ids": len(set(ids)), "one_unique_thread_per_run": len(ids) == len(set(ids)) == values["runs"]}
    report = {"phases": summary, "distinct_threads_across_valid_runs": len(all_ids) == len(set(all_ids)) == len(rows),
              "valid_runs_scanned": len(rows), "potential_flag_count": len(flags),
              "flags_are_not_automatic_exclusions": True,
              "scope": "Only emitted model tool commands/events are inspected; built-in model/provider/catalog traffic is not interpreted as task reference browsing.",
              "limitation": "This is a trace audit, not a security proof or a test of model attention. Keyword hits require human adjudication."}
    output = HERE / "results" / "trace-audit.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    private = HERE.parent.parent / ".local" / "trace-flags.json"
    private.parent.mkdir(exist_ok=True)
    private.write_text(json.dumps(flags, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"runs_scanned": len(rows), "potential_flags": len(flags), "distinct_threads": report["distinct_threads_across_valid_runs"], "phase_summary": summary}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(audit(args.root.resolve()), ensure_ascii=False))

"""Read only allowlisted implementations; exercise them with synthetic fixtures.

No model calls, grading reruns, installation, network access, or candidate reads.
The Python used for these probes is the available local Python, not a claim of
replaying the frozen model experiment's interpreter.
"""
import ast
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import sys
from types import SimpleNamespace
import urllib.parse

PROJECT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
NEW = PROJECT / "evals/generalization-2026-10-26"
OLD = PROJECT / "evals/production-2026-10-25"


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest().upper()


def load_functions(path, names, namespace):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in nodes} == set(names)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), namespace)
    return namespace


observer = {}
exec(compile(NEW.joinpath("observe.py").read_text(encoding="utf-8"), str(NEW / "observe.py"), "exec"), observer)
observe = observer["observe"]
skill = "---\nname: synthetic-only\n---\nSYNTHETIC FULL BODY\nsecond line"
expected = observer["body"](skill)


def event(item):
    return json.dumps({"type": "item.completed", "item": item})


observer_cases = {
    "direct_call_arguments_only": ({"type": "mcp_tool_call", "arguments": {"code": skill}, "result": {"content": [{"text": "ENOENT"}]}}, False),
    "agent_self_report": ({"type": "agent_message", "text": skill}, False),
    "shell_command_only": ({"type": "command_execution", "command": skill, "aggregated_output": "not read"}, False),
    "partial_result": ({"type": "mcp_tool_call", "result": {"content": [{"text": expected[:8]}]}}, False),
    "shell_full_body": ({"type": "command_execution", "aggregated_output": skill}, True),
    "mcp_text_full_body": ({"type": "mcp_tool_call", "result": {"content": [{"type": "text", "text": skill}]}}, True),
    "nested_json": ({"type": "mcp_tool_call", "result": {"content": [{"text": json.dumps({"content": skill})}]}}, True),
    "literal_concatenation": ({"type": "mcp_tool_call", "result": {"content": [{"text": repr(skill[:20]) + " +\n" + repr(skill[20:])}]}}, True),
}
observer_results = {}
for name, (item, wanted) in observer_cases.items():
    found = observe(event(item), skill)["body_visible"]
    assert found is wanted, name
    observer_results[name] = {"observed": found, "expected": wanted}

# Execute the unchanged freshness-check prefix only; subsequent prepare work is
# deliberately excluded, as it would read real sources and invoke preflight.
fresh_root = OUT / "fixtures/freshness"
original = fresh_root / "original-runtime"
runtime = fresh_root / ".local/production-runtime/old-run"
selection = [f"fresh-{number:02}" for number in range(20)]
originals = [{"id": key, "repo": "synthetic/repo", "fix_sha": f"fix-{key}",
              "issue_or_pr_url": f"https://example.invalid/{key}"} for key in [*selection, "old-only"]]
write(fresh_root / ".local/state.json", {"runtime_root": str(original)})
write(original / "eligible.json", {"cases": originals})
write(fresh_root / "old/results/second-corpus-selection.json", {"remaining_adequate": selection})
runtime.mkdir(parents=True, exist_ok=True)
(runtime / "records.jsonl").write_text(json.dumps({"case_id": "old-only"}) + "\n", encoding="utf-8")
orphan = runtime / "runs/synthetic/fresh-00"
for name in ("started.json", "client-done.json", "cli-checkpoint.json"):
    write(orphan / name, {"case_id": "fresh-00", "synthetic_fixture": True})
run_tree = ast.parse((NEW / "run.py").read_text(encoding="utf-8"))
prepare = next(node for node in run_tree.body if isinstance(node, ast.FunctionDef) and node.name == "prepare")
prepare.body = [node for node in prepare.body if node.lineno < 85]
fresh_env = {"Path": Path, "json": json, "urllib": urllib, "PROJECT": fresh_root,
             "HERE": NEW, "OLD": fresh_root / "old", "ROOT": fresh_root / ".local/production-runtime/new-run",
             "runner": SimpleNamespace(read=read, digest=digest)}
exec(compile(ast.Module(body=[prepare], type_ignores=[]), str(NEW / "run.py"), "exec"), fresh_env)
fresh_env["prepare"]()
freshness = {"orphan_attempt_case": "fresh-00", "started_done_checkpoint_present": True,
             "selection_check_passed_despite_orphan_attempt": True}
(runtime / "records.jsonl").write_text(json.dumps({"case_id": "fresh-00"}) + "\n", encoding="utf-8")
try:
    fresh_env["prepare"]()
except AssertionError as exc:
    freshness["finalized_journal_control_rejected"] = True
    freshness["control_message"] = str(exc)
else:
    raise AssertionError("Finalized journal positive control should reject prior use")

# Base bytes are present in metadata but upstream preflight checks fix overlay
# bytes only. The synthetic base is deliberately changed after its hash capture.
source_root = OUT / "fixtures/source-integrity"
base, fixed = source_root / "base", source_root / "fix"
base.mkdir(parents=True, exist_ok=True)
fixed.mkdir(parents=True, exist_ok=True)
(base / "module.py").write_text("value = 'original base'\n", encoding="utf-8")
(fixed / "module.py").write_text("value = 'reference fixture'\n", encoding="utf-8")
wrapper = {"id": "synthetic-source", "source_dir": str(base), "upstream": {
    "base_dir": str(base), "fix_dir": str(fixed), "preflight": {
        "base_production_sha256": {"module.py": digest(base / "module.py")},
        "overlay_sha256": {"module.py": digest(fixed / "module.py")},
        "guard_fix": {"tests_ran": 1, "tests_collected": 1}}}}
runner_env = {"Path": Path, "json": json, "re": re, "hashlib": hashlib,
              "read": read, "digest": digest}
load_functions(OLD / "runner.py", {"case_digest", "preflight_case", "parse_client"}, runner_env)
before = runner_env["case_digest"](wrapper)
(base / "module.py").write_text("value = 'changed after freeze'\n", encoding="utf-8")
preflight = runner_env["preflight_case"](wrapper, source_root)
source_integrity = {"base_production_hash_now_matches": digest(base / "module.py") == wrapper["upstream"]["preflight"]["base_production_sha256"]["module.py"],
                    "case_digest_unchanged": before == runner_env["case_digest"](wrapper),
                    "upstream_preflight_accepted": preflight["source"] == "upstream_preverified"}
assert source_integrity == {"base_production_hash_now_matches": False, "case_digest_unchanged": True,
                            "upstream_preflight_accepted": True}

# Reproduce actual parser + summary behavior with no successful terminal event.
# The artificial zero exit code is explicit; this is not a claim that a real
# frozen CLI produced such a trace. It shows a missing fail-closed transition.
completion_root = OUT / "fixtures/completion"
workspace = completion_root / "workspace"
skill_rel = ".agents/skills/lean-coding/SKILL.md"
skill_path = workspace / skill_rel
skill_path.parent.mkdir(parents=True, exist_ok=True)
skill_path.write_text(skill, encoding="utf-8")
key = "synthetic--failed-terminal"
logs = completion_root / "runs/synthetic" / key
logs.mkdir(parents=True, exist_ok=True)
raw = "\n".join([json.dumps({"type": "thread.started", "thread_id": "synthetic-unique-thread"}),
                 event({"id": "synthetic-read", "type": "command_execution", "aggregated_output": skill}),
                 json.dumps({"type": "turn.failed", "error": {"message": "synthetic terminal failure"}})])
(logs / "events.jsonl").write_text(raw + "\n", encoding="utf-8")
done = {"key": key, "case_id": "synthetic", "workspace": str(workspace),
        "timeout": False, "cli_exit_code": 0, "client_seconds": 1.0}
parsed = runner_env["parse_client"](done, logs, skill_path)
export_tree = ast.parse((OLD / "export_results.py").read_text(encoding="utf-8"))
fields = ast.literal_eval(next(node.value for node in export_tree.body if isinstance(node, ast.Assign)
                              and any(isinstance(target, ast.Name) and target.id == "FIELDS" for target in node.targets)))
export_env = {"Path": Path, "json": json, "ROOT": completion_root, "HERE": completion_root / "public",
              "PROJECT": completion_root, "SKILL": skill_path, "FIELDS": fields, "observe": observe,
              "body": observer["body"], "scan": lambda *args: {"flags": []}, "public_value": lambda x: x,
              "runner": SimpleNamespace(digest=digest, SKILL_REL=skill_rel, git=lambda *args: "", write=write)}
(completion_root / "public/results").mkdir(parents=True, exist_ok=True)
load_functions(NEW / "run.py", {"export"}, export_env)
export_env["export"]("synthetic", [{**parsed, "accepted": True}], {"skill_sha256": digest(skill_path)})
summary = read(completion_root / "public/results/synthetic-summary.json")
completion = {"trace_has_turn_completed": False, "trace_has_turn_failed": True,
              "synthetic_cli_exit_code": 0, "parse_cli_completed": parsed["cli_completed"],
              "parse_usage_complete": parsed["usage_complete"], "parse_stream_errors": parsed["stream_errors"],
              "summary_cli_completed_without_timeout": summary["cli_completed_without_timeout"],
              "summary_full_body_outputs": summary["full_body_outputs"], "summary_integrity_issues": summary["integrity_issues"],
              "conditional_limit": "Requires exit code zero with missing/failed terminal event; real CLI occurrence not asserted."}
assert completion["parse_cli_completed"] is True
assert completion["summary_cli_completed_without_timeout"] == 1
assert completion["summary_integrity_issues"] == []

freeze = read(NEW / "results/freeze.json")
frozen_at = read(NEW / "results/frozen-at.json")
hashes = {}
for field, path, wanted in [
    ("protocol_sha256", NEW / "protocol.json", freeze["protocol_sha256"]),
    ("driver_sha256", NEW / "run.py", freeze["driver_sha256"]),
    ("observer_sha256", NEW / "observe.py", freeze["observer_sha256"]),
    ("runner_sha256", OLD / "runner.py", freeze["runner_sha256"]),
    ("observer_preflight_sha256", NEW / "results/observer-preflight.json", freeze["observer_preflight_sha256"]),
    ("freeze_sha256", NEW / "results/freeze.json", frozen_at["freeze_sha256"]),
]:
    actual = digest(path)
    hashes[field] = {"matches": actual == wanted, "actual": actual}
assert all(value["matches"] for value in hashes.values())

report = {
    "audit_kind": "restricted-independent-method-audit-with-synthetic-offline-probes",
    "audited_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    "probe_python": sys.version.split()[0], "frozen_experiment_python": freeze["python_version"],
    "native_model_calls": 0, "evaluation_reruns": 0, "candidate_or_new_result_reads": 0,
    "hash_checks": hashes, "observer_probes": observer_results,
    "freshness_probe": freshness, "base_integrity_probe": source_integrity,
    "terminal_completion_probe": completion,
    "findings": [
        {"id": "M1", "severity": "method-gap", "title_zh": "历史未使用检查遗漏未完成原生尝试",
         "evidence": ["run.py:68-84", "runner.py:262-280", "runner.py:327-351", "runner.py:386-388"],
         "impact_zh": "已有 started/client-done/checkpoint 且尚未写入 records.jsonl 的任务可仍被选择为 fresh。探针证实检查通过；写入最终 journal 后对照立即拒绝。此探针不证明实际选中任务曾被使用。"},
        {"id": "M2", "severity": "method-gap", "title_zh": "已有上游包装的 base 源码未重新绑定冻结字节",
         "evidence": ["run.py:101-108", "run.py:131", "runner.py:59-60", "runner.py:192-200", "runner.py:101-103", "prepare_second_real.py:30-33"],
         "impact_zh": "已有 wrapper 时跳过 restore_case；case_digest 只散列元数据。更改 base_production 文件不会改变 case_digest，upstream preflight 仍通过；运行时从 source_dir 复制该目录。此探针不证明实际 base 已改变。"},
        {"id": "M3", "severity": "conditional-method-gap", "title_zh": "正常结束汇总未要求成功终止事件",
         "evidence": ["runner.py:283-303", "run.py:177-186"],
         "impact_zh": "合成零退出码且仅有 turn.failed 的轨迹被 parse 标为 cli_completed，汇总计入 cli_completed_without_timeout，且无 integrity issue。是否存在真实 CLI 零退出失败轨迹未验证。"},
        {"id": "M4", "severity": "scope-limit", "title_zh": "冻结清单未绑定所有传递实现和实际来源目录",
         "evidence": ["run.py:13-18", "run.py:123-140", "runner.py:26-30", "runner.py:360"],
         "impact_zh": "清单绑定 driver/observer/runner，但不绑定 restore_case、scan、public_value、resource_gate、upstream grader/transport 实现；后两者按限制未读取。当前源码哈希一致不能单独证明整个评分闭包预冻结。"}
    ],
    "preserved_failure_behavior": {
        "cli_timeout_recorded_after_successful_process_cleanup": "runner.py:270-280, 344-351",
        "grading_exception_history_appended": "runner.py:374-380",
        "suite_exception_saved_and_suite_raises": "runner.py:435-446",
        "normal_failure_not_replaced_by_new_model_call": "runner.py:328-355",
        "limits_zh": "launch/cleanup 异常可能只有 started/raw/failures 而无最终记录；parse 失败保留 done/raw，但阻止 export；失败日志重写且新 exporter 不导出 stream_error_count，须读原始证据恢复。"
    },
    "not_certified_zh": [
        "所选任务确实未用于历史模型尝试或修订；禁止读取旧日志，只有冻结声明和实现可审查。",
        "candidate/UI、原始选择清单、base/fix 快照、归档、Python/CLI 本体及依赖字节的实际一致性；只验证了允许读取的文件。",
        "模型质量结果、8 份独立产物审查、轨迹人工裁决、安装包验证与最终 beta 门槛；本审查没有读取这些产物。",
        "run.py 仅执行和汇总，没有资格裁决器；不能据此宣告 beta 通过或已发生误通过。"
    ],
    "recommendation_zh": "将当前补充只读证据与冻结前检查分开报告；最终裁决补齐所有原生尝试清单、base/fix 逐字绑定、成功终止事件和完整失败记录后再判断 beta。不要追溯修改冻结文件。"
}
write(OUT / "method-audit.json", report)
print(json.dumps({"report": str(OUT / "method-audit.json"), "all_hashes_match": True,
                  "observer_probes_passed": len(observer_results), "freshness": freshness,
                  "base_integrity": source_integrity, "completion": completion}, ensure_ascii=False))

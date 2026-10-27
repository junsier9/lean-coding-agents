"""Freeze a revealed Click regression as a development-only contract."""
from pathlib import Path
import argparse
import json
import sys

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent.parent
OLD = HERE.parent / "production-2026-10-25"
sys.path.insert(0, str(OLD))
import runner
from export_results import public_value

TASK = """修复Click中显式绑定的选项值被重新解析为另一选项的问题。
同时声明is_flag=False、flag_value和default的选项，现有省略、裸选项、显式值三种用法都要保留。
例如带独立--other布尔选项时，--opt=--other和-o--other应把字符串--other传给opt，不能启用other。
显式附加的负整数和空字符串也要保留；裸选项后真正独立的后续选项仍要正常解析。
保留已有长短选项语法、类型转换、错误、回调及Context资源释放，不增加依赖或另建解析框架。
可运行已有test_public.py并补充有意义的本地检查；不要求特定内部实现或行数。"""


def prepare(reference_dir):
    destination = HERE / "cases/d10.json"
    if destination.exists():
        raise FileExistsError("Keep completed development inputs immutable")
    source = PROJECT / ".local/production-runtime/real-v4/snapshots/xb-012-click-3152/base"
    bad = PROJECT / ".local/production-blind-v4/05/A/src/click/core.py"
    reference_dir = reference_dir.resolve()
    reference_report = reference_dir / "control-report.json"
    assert reference_report.is_file(), "Finish independent control implementation first"
    files = {"click/" + path.relative_to(source / "src/click").as_posix():
             path.read_text(encoding="utf-8")
             for path in sorted((source / "src/click").rglob("*"))
             if path.is_file() and (path.suffix == ".py" or path.name == "py.typed")}
    files["click/core.py"] = bad.read_text(encoding="utf-8")
    files["docs/options.md"] = (source / "docs/options.md").read_text(encoding="utf-8")
    for path in source.iterdir():
        if path.is_file() and path.name.lower().startswith("license"):
            files[path.name] = path.read_text(encoding="utf-8")
    reference = {}
    for path in sorted((reference_dir / "click").rglob("*.py")):
        relative = "click/" + path.relative_to(reference_dir / "click").as_posix()
        content = path.read_text(encoding="utf-8")
        if content != files.get(relative):
            reference[relative] = content
    assert reference and all((reference_dir / name).is_file()
                             for name in files if name.startswith("click/"))
    expression = "option.obj._flag_needs_value\n                and isinstance(next_rarg, str)"
    parser = files["click/parser.py"]
    assert parser.count(expression) == 1
    consume_all = parser.replace(expression, "False and " + expression)
    case = {
        "id": "d10", "repo": "pallets/click",
        "family": "revealed-explicit-value-binding-regression", "task": TASK,
        "files": files, "reference_files": reference,
        "visible_tests": {"test_public.py": (HERE / "visible_d10.py").read_text(encoding="utf-8")},
        "hidden_tests": {"test_contract.py": (HERE / "contract_d10.py").read_text(encoding="utf-8")},
        "critical": True,
        "evidence_class": "known_regression_development_only_not_fresh_holdout",
        "control_variants": {
            "original_constructor": {"click/core.py": (source / "src/click/core.py").read_text(encoding="utf-8")},
            "consume_all_prefix_tokens": {"click/parser.py": consume_all},
        },
        "checks": ["显式数据与缺省分支分离", "独立后续选项仍可解析",
                   "空值与负整数", "真实类型错误不回退", "回调与资源生命周期"],
    }
    runner.write(destination, case)
    runner.write(HERE / "results/reference-control.json",
                 {"raw_report_sha256": runner.digest(reference_report),
                  "report": public_value(runner.read(reference_report)),
                  "reference_files": sorted(reference),
                  "not_a_native_skill_effectiveness_trial": True})
    origin = runner.read(HERE / "provenance.json")
    origin.update(case_sha256=runner.digest(destination),
                  reference_report_sha256=runner.digest(reference_report),
                  reference_changed_files=sorted(reference),
                  contract_sha256=runner.digest(HERE / "contract_d10.py"),
                  visible_example_sha256=runner.digest(HERE / "visible_d10.py"))
    runner.write(HERE / "provenance.json", origin)
    print(json.dumps({"prepared": "d10", "reference_files": sorted(reference),
                      "evidence_class": case["evidence_class"]}))


def preflight(iteration=1):
    root = PROJECT / f".local/production-runtime/dev-v5-iteration{iteration}"
    root.mkdir(parents=True, exist_ok=True)
    cases = [runner.read(HERE / "cases" / name) for name in ("d09.json", "d10.json")]
    results = {case["id"]: runner.preflight_case(case, root) for case in cases}
    case = cases[1]
    controls = {}
    for name, overrides in case["control_variants"].items():
        directory = root / "negative-controls" / name
        if directory.exists():
            raise FileExistsError("Do not overwrite a prior control execution")
        directory.mkdir(parents=True)
        runner.put_files(directory / "candidate", {**case["files"], **overrides})
        grade = runner.synthetic_grade(case, directory / "candidate", directory / "grade")
        assert grade["exit_code"] != 0 and not grade["timeout"]
        assert all(g["tests_ran"] == results["d10"]["expected"]["modes"][mode]["tests_ran"]
                   and g["failed"] > 0 for mode, g in grade["modes"].items())
        controls[name] = grade
    report = {"completed_at": runner.utc(), "evidence_class": "offline_development_controls_only",
              "native_model_calls": 0, "initial_fails_reference_passes": True,
              "all_negative_controls_rejected": True, "cases": results,
              "negative_controls": controls}
    runner.write(HERE / "results/preflight.json", public_value(report))
    print(json.dumps({"preflight_pass": True,
                      "methods_per_mode": {key: value["expected"]["modes"]["normal"]["tests_ran"]
                                           for key, value in results.items()},
                      "negative_controls_rejected": list(controls)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference-dir", type=Path)
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--iteration", type=int, default=1)
    arguments = parser.parse_args()
    if arguments.reference_dir:
        prepare(arguments.reference_dir)
    if arguments.preflight:
        preflight(arguments.iteration)

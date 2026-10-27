"""Freeze public regression tests and verify a real base/fix contrast.

This program runs upstream tests only. It never invokes a coding model, installs
dependencies, or changes an upstream checkout. All generated files stay in root.
"""
from __future__ import annotations

import argparse
import ast
import copy
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile
import threading
import time
import traceback
import urllib.request
import xml.etree.ElementTree as ET


CONFIG_NAMES = ("pytest.ini", ".pytest.ini", "pyproject.toml", "tox.ini", "setup.cfg")
IGNORED_DIRS = {".git", ".venv", "venv", ".tox", "__pycache__", ".pytest_cache", ".benchmark-deps"}
SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", re.M)
DOWNLOAD_LOCK = threading.Lock()
ARCHIVE_LOCKS = {}


class Exclude(Exception):
    def __init__(self, reason, detail):
        self.reason, self.detail = reason, detail
        super().__init__(f"{reason}: {detail}")


def inside(path, root):
    def normalize(value):
        # Windows resolve() can return an extended-length prefix depending on
        # whether another download thread just created a parent directory.
        text = str(Path(value).resolve())
        if text.startswith("\\\\?\\UNC\\"):
            text = "\\\\" + text[8:]
        elif text.startswith("\\\\?\\"):
            text = text[4:]
        return Path(text)
    path, root = normalize(path), normalize(root)
    if not path.is_relative_to(root) or path == root:
        raise ValueError(f"Path must be a child of runtime: {path}")
    return path


def safe_relative(value):
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not path.parts or ":" in value:
        raise ValueError(f"Unsafe repository path: {value}")
    return path.as_posix()


def reset_dir(path, root):
    path = inside(path, root)  # Check the final resolved target BEFORE deletion.
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    return path


def write_json(path, value, root):
    path = inside(path, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = inside(path.with_suffix(path.suffix + ".tmp"), root)
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def archive(repo_name, sha, destination, root):
    """Fetch a fixed GitHub SHA, retain its archive hash, and strip one root."""
    destination = reset_dir(destination, root)
    tar_path = inside(root / "archives" / (repo_name.replace("/", "--") + "--" + sha + ".tar.gz"), root)
    url = f"https://codeload.github.com/{repo_name}/tar.gz/{sha}"
    with DOWNLOAD_LOCK:
        lock = ARCHIVE_LOCKS.setdefault(str(tar_path), threading.Lock())
    with lock:
        if not tar_path.is_file():
            tar_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = inside(tar_path.with_suffix(".download"), root)
            for attempt in range(3):
                try:
                    request = urllib.request.Request(url, headers={"User-Agent": "public-regression-preflight"})
                    with urllib.request.urlopen(request, timeout=60) as response, temporary.open("wb") as output:
                        shutil.copyfileobj(response, output)
                    with tarfile.open(temporary, "r:gz") as check:
                        if not check.getmembers():
                            raise ValueError("Empty archive")
                    os.replace(temporary, tar_path)
                    break
                except Exception:
                    temporary.unlink(missing_ok=True)
                    if attempt == 2:
                        raise
                    time.sleep(attempt + 1)
        archive_hash = digest(tar_path)
        tar_path.with_suffix(tar_path.suffix + ".sha256").write_text(archive_hash + "\n", encoding="ascii")
    with tarfile.open(tar_path, "r:gz") as source:
        normalized = []
        for member in source.getmembers():
            parts = PurePosixPath(member.name).parts
            if len(parts) <= 1:
                continue
            safe_relative("/".join(parts))
            item = copy.copy(member)
            item.name = safe_relative("/".join(parts[1:]))
            if item.islnk():
                link_parts = PurePosixPath(item.linkname).parts
                item.linkname = safe_relative("/".join(link_parts[1:]))
            normalized.append(item)
        source.extractall(destination, members=normalized, filter="data")
    return {"url": url, "archive_path": str(tar_path), "sha256": archive_hash,
            "files": sum(p.is_file() for p in destination.rglob("*"))}


def test_nodes(source):
    """Return pytest node names and spans, including decorators and class methods."""
    tree = ast.parse(source)
    result = []

    def visit(nodes, classes=()):
        for node in nodes:
            if isinstance(node, ast.ClassDef):
                visit(node.body, classes + (node.name,))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
                start = min([node.lineno] + [d.lineno for d in node.decorator_list])
                result.append(("::".join((*classes, node.name)), start, node.end_lineno))
    visit(tree.body)
    return result


def regression_paths(case, base_dir, fix_dir):
    import difflib
    selected, changed_tests = [], []
    for declared in case.get("regression_test_files", []):
        relative = safe_relative(declared)
        fixed = fix_dir / relative
        if not fixed.is_file() or fixed.suffix != ".py":
            continue
        base = base_dir / relative
        old = base.read_text(encoding="utf-8-sig").splitlines(keepends=True) if base.is_file() else []
        new = fixed.read_text(encoding="utf-8-sig").splitlines(keepends=True)
        diff = "".join(difflib.unified_diff(old, new, n=0))
        ranges = [(int(m[1]), int(m[1]) + max(1, int(m[2] or "1")) - 1)
                  for m in HUNK_RE.finditer(diff)]
        if not ranges:
            continue
        changed_tests.append(relative)
        try:
            nodes = test_nodes(fixed.read_text(encoding="utf-8-sig"))
            matched = [name for name, start, end in nodes
                       if any(start <= hi and end >= lo for lo, hi in ranges)]
        except (SyntaxError, UnicodeError):
            matched, nodes = [], []
        if matched:
            selected.extend(relative + "::" + name for name in matched)
        else:
            # Helpers, module-level parametrization, or syntax unsupported by
            # AST: retain the specific changed test module, never the suite.
            selected.append(relative)
    if not changed_tests:
        raise Exclude("no_changed_regression_tests", "No declared Python test file has a base-to-fix diff.")
    return list(dict.fromkeys(selected)), changed_tests


def overlay_manifest(fix_dir, changed_tests):
    files = set(changed_tests)
    test_roots = set()
    for relative in changed_tests:
        pieces = PurePosixPath(relative).parts
        if len(pieces) > 1:
            # Tests can live inside a production package. Freeze the nearest
            # containing tests directory, retaining the historical fallback
            # for other layouts such as top-level "testing".
            end = max((i + 1 for i, part in enumerate(pieces[:-1])
                       if part == "tests"), default=1)
            test_roots.add(PurePosixPath(*pieces[:end]).as_posix())
    # Freeze complete test trees: conftest, imported helpers, data and fixtures.
    for name in test_roots:
        for path in (fix_dir / name).rglob("*"):
            if path.is_file() and not any(p in IGNORED_DIRS for p in path.relative_to(fix_dir).parts):
                files.add(path.relative_to(fix_dir).as_posix())
    for path in fix_dir.rglob("conftest.py"):
        if not any(p in IGNORED_DIRS for p in path.relative_to(fix_dir).parts):
            files.add(path.relative_to(fix_dir).as_posix())
    for name in CONFIG_NAMES:
        if (fix_dir / name).is_file():
            files.add(name)
    return sorted(files), sorted(test_roots)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def pytest_config(fix_dir):
    for name in CONFIG_NAMES:
        path = fix_dir / name
        if not path.is_file():
            continue
        content = path.read_text(encoding="utf-8-sig")
        if name in ("pytest.ini", ".pytest.ini") or (name == "pyproject.toml" and "[tool.pytest.ini_options]" in content) or (name == "tox.ini" and "[pytest]" in content) or (name == "setup.cfg" and "[tool:pytest]" in content):
            return name
    return ".preflight-pytest.ini"


def prepare_grade(case, candidate_dir, grade_dir):
    root = Path(case["preflight"]["runtime_root"]).resolve()
    candidate_dir, grade_dir = inside(candidate_dir, root), inside(grade_dir, root)
    fix_dir = inside(case["fix_dir"], root)
    if grade_dir == candidate_dir or grade_dir.is_relative_to(candidate_dir) or candidate_dir.is_relative_to(grade_dir):
        raise ValueError("Candidate and grade directories must be separate trees.")
    # Verify the reference tests/config have not changed since preflight.
    expected_hashes = case["preflight"]["overlay_sha256"]
    for relative in case["grade_overlay_files"]:
        relative = safe_relative(relative)
        if digest(fix_dir / relative) != expected_hashes[relative]:
            raise ValueError(f"Frozen reference changed: {relative}")
    reset_dir(grade_dir, root)

    def ignore_candidate_artifacts(directory, names):
        ignored = set(shutil.ignore_patterns(*IGNORED_DIRS)(directory, names))
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.name.startswith((".pytest-", ".test-tmp")) and entry.is_dir(follow_symlinks=False):
                    ignored.add(entry.name)
        return ignored

    shutil.copytree(candidate_dir, grade_dir, dirs_exist_ok=True,
                    ignore=ignore_candidate_artifacts)
    # Remove candidate-controlled test trees, including additional test plugins.
    for relative in case["preflight"]["frozen_test_roots"]:
        target = inside(grade_dir / safe_relative(relative), root)
        if target.is_dir():
            shutil.rmtree(target)
    for path in grade_dir.rglob("conftest.py"):
        inside(path, root).unlink()
    for name in CONFIG_NAMES:
        inside(grade_dir / name, root).unlink(missing_ok=True)
    for relative in case["grade_overlay_files"]:
        destination = inside(grade_dir / safe_relative(relative), root)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(fix_dir / relative, destination)
    config = case["preflight"]["pytest_config"]
    if config == ".preflight-pytest.ini":
        (grade_dir / config).write_text("[pytest]\n", encoding="utf-8")
    return grade_dir


def junit_summary(path):
    summary = dict(tests_collected=0, tests_ran=0, passed=0, failed=0, errors=0,
                   skipped=0, assertion_failures=0, failures=[])
    if not path.is_file():
        return summary
    try:
        tree = ET.parse(path)
        for item in tree.iter("testcase"):
            summary["tests_collected"] += 1
            failed, error, skipped = item.find("failure"), item.find("error"), item.find("skipped")
            if error is not None:
                summary["errors"] += 1
            elif skipped is not None:
                summary["skipped"] += 1
            else:
                summary["tests_ran"] += 1
                if failed is None:
                    summary["passed"] += 1
                else:
                    summary["failed"] += 1
            if failed is not None or error is not None:
                detail = failed if failed is not None else error
                kind = detail.get("type", "")
                message = detail.get("message", "")
                if not kind:
                    match = re.match(r"([A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception)|Failed):", message)
                    kind = match.group(1) if match else ""
                if "AssertionError" in kind or "DID NOT RAISE" in message or "Failed" == kind:
                    summary["assertion_failures"] += 1
                summary["failures"].append({"test": item.get("classname", "") + "::" + item.get("name", ""),
                                            "stage": "call" if failed is not None else "setup_or_collection",
                                            "type": kind, "message": message[:2000]})
    except ET.ParseError as exc:
        summary["report_error"] = str(exc)
    return summary


def run_tests(case, work_dir, logs_dir, label, python_exe, deps_dir, test_paths=None):
    root = Path(case["preflight"]["runtime_root"]).resolve()
    work_dir, logs_dir = inside(work_dir, root), inside(logs_dir, root)
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path, xml_path = logs_dir / (label + ".log"), logs_dir / (label + ".xml")
    xml_path.unlink(missing_ok=True)
    command = [str(Path(python_exe).resolve()), "-m", "pytest", "-q", "-o", "addopts=",
               "-p", "no:cacheprovider", "-p", "pytest_timeout", "--timeout=60",
               "--junitxml=" + str(xml_path), "--rootdir=" + str(work_dir),
               "--confcutdir=" + str(work_dir), "-c", str(work_dir / case["preflight"]["pytest_config"]),
               *(case["test_paths"] if test_paths is None else test_paths)]
    environment = os.environ.copy()
    environment.update(PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTHONDONTWRITEBYTECODE="1",
                       PYTHONPATH=os.pathsep.join([str(work_dir / "src"), str(work_dir), str(Path(deps_dir).resolve())]))
    started = time.monotonic()
    with log_path.open("w", encoding="utf-8") as output:
        output.write(json.dumps({"command": command, "cwd": str(work_dir),
                                 "PYTHONPATH": environment["PYTHONPATH"]}, ensure_ascii=False) + "\n")
        output.flush()
        try:
            completed = subprocess.run(command, cwd=work_dir, env=environment,
                                       stdout=output, stderr=subprocess.STDOUT, timeout=180)
            exit_code = completed.returncode
        except subprocess.TimeoutExpired:
            exit_code = 124
            output.write("\nPREFLIGHT: process exceeded 180-second outer limit.\n")
    result = junit_summary(xml_path)
    result.update(exit_code=exit_code, command=command, log_path=str(log_path),
                  junit_path=str(xml_path), seconds=round(time.monotonic() - started, 3))
    return result


def grade_case(case, candidate_dir, grade_dir, python_exe, deps_dir) -> dict:
    """Grade production changes against immutable upstream tests/config/fixtures.

    candidate_dir and grade_dir must be separate directories under the runtime.
    The fixed reference is hash checked and its whole test trees replace the
    candidate's test trees; candidate pytest config and conftest are discarded.
    """
    guard = case.get("preflight", {}).get("guard_fix", {})
    if not case.get("guard_paths") or not guard.get("tests_ran") or guard.get("exit_code") != 0 or guard.get("failed") or guard.get("errors"):
        raise ValueError("Case has not passed full-file guard preflight.")
    grade_dir = prepare_grade(case, candidate_dir, grade_dir)
    return run_tests(case, grade_dir, grade_dir.parent / (grade_dir.name + "-logs"),
                     "grade", python_exe, deps_dir, test_paths=case["guard_paths"])


def guard_worker(original, args):
    case = copy.deepcopy(original)
    try:
        fix_dir, base_dir = Path(case["fix_dir"]), Path(case["base_dir"])
        guard_paths = []
        for declared in case["regression_test_files"]:
            relative = safe_relative(declared)
            path = fix_dir / relative
            if path.suffix != ".py" or not path.is_file() or path.name == "conftest.py":
                continue
            try:
                actual_tests = bool(test_nodes(path.read_text(encoding="utf-8-sig")))
            except (SyntaxError, UnicodeError):
                actual_tests = False
            if actual_tests:
                guard_paths.append(relative)
        if not guard_paths:
            raise Exclude("no_full_regression_test_files", "Declared files contain no actual Python tests.")
        case["guard_paths"] = list(dict.fromkeys(guard_paths))
        prod = case["production_files"]
        if set(prod).intersection(case["grade_overlay_files"]):
            raise Exclude("test_overlay_contains_production_file", "Frozen test tree overlaps declared production code.")
        base_hashes = {p: digest(base_dir / p) if (base_dir / p).is_file() else None for p in prod}
        fix_hashes = {p: digest(fix_dir / p) if (fix_dir / p).is_file() else None for p in prod}
        base_test_dir = base_dir.parent / "base-test"
        if any((digest(base_test_dir / p) if (base_test_dir / p).is_file() else None) != h
               for p, h in base_hashes.items()):
            raise Exclude("base_test_production_mismatch", "Base-test must retain the base production file hashes.")
        case["preflight"].update(base_production_sha256=base_hashes, fix_production_sha256=fix_hashes)
        work_dir = prepare_grade(case, fix_dir, fix_dir.parent / "guard-fix")
        result = run_tests(case, work_dir, fix_dir.parent / "logs", "guard-fix",
                           args.python, args.deps, test_paths=case["guard_paths"])
        case["preflight"]["guard_fix"] = result
        if result["exit_code"] or result["failed"] or result["errors"] or not result["tests_ran"]:
            raise Exclude("fix_full_file_guard_not_passing", case)
        return True, case
    except Exclude as exc:
        if isinstance(exc.detail, dict) and "preflight" in exc.detail:
            result = exc.detail
            result["exclusion_reason"] = exc.reason
        else:
            result = dict(case, exclusion_reason=exc.reason, exclusion_detail=exc.detail)
        return False, result
    except Exception as exc:
        return False, dict(case, exclusion_reason="guard_preflight_exception", exclusion_detail=str(exc),
                           traceback=traceback.format_exc())


def validate_guards(output, args):
    output["metadata"].update(complete=False, f2p_complete=True, guard_complete=False)
    output["metadata"]["preflight_script_sha256"] = digest(__file__)
    output["metadata"].setdefault("f2p_eligible_count", len(output["cases"]))
    write_json(args.output, output, args.root)
    cases = list(output["cases"])
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        pending = {pool.submit(guard_worker, case, args): case for case in cases}
        for future in as_completed(pending):
            eligible, case = future.result()
            output["cases"] = [case if c["id"] == case["id"] else c for c in output["cases"]]
            if not eligible:
                output["cases"] = [c for c in output["cases"] if c["id"] != case["id"]]
                output["excluded"].append(case)
            write_json(args.output, output, args.root)
            print(json.dumps({"phase": "full_file_guard", "id": case["id"], "eligible": eligible,
                              "reason": case.get("exclusion_reason"),
                              "tests_ran": case.get("preflight", {}).get("guard_fix", {}).get("tests_ran"),
                              "eligible_count": len(output["cases"])}, ensure_ascii=False), flush=True)
    output["metadata"].update(complete=True, guard_complete=True)
    write_json(args.output, output, args.root)
    return output


def check_case(original, args):
    case = dict(original)
    root = args.root
    identifier = case.get("id", "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", identifier):
        raise Exclude("unsafe_case_id", identifier)
    repo_name = case.get("repo", "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo_name):
        raise Exclude("unsafe_repo_name", repo_name)
    for key in ("base_sha", "fix_sha"):
        if not SHA_RE.fullmatch(case.get(key, "")):
            raise Exclude("invalid_sha", key)
    metadata_path = inside(root / "metadata" / (case["fix_sha"] + ".json"), root)
    if not metadata_path.is_file():
        raise Exclude("trusted_commit_metadata_missing", str(metadata_path))
    metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
    if metadata.get("sha") != case["fix_sha"]:
        raise Exclude("trusted_commit_sha_mismatch", str(metadata_path))
    parents = [p["sha"] if isinstance(p, dict) else p for p in metadata.get("parents", [])]
    if not parents or parents[0] != case["base_sha"]:
        raise Exclude("not_first_parent", {"declared": case["base_sha"], "actual_parents": parents})
    declared_prod = set(map(safe_relative, case.get("production_files", [])))
    case_dir = inside(root / "snapshots" / identifier, root)
    base_dir, fix_dir = case_dir / "base", case_dir / "fix"
    base_archive = archive(repo_name, case["base_sha"], base_dir, root)
    fix_archive = archive(repo_name, case["fix_sha"], fix_dir, root)
    unchanged = [p for p in declared_prod if (base_dir / p).is_file() and (fix_dir / p).is_file()
                 and digest(base_dir / p) == digest(fix_dir / p)]
    if not declared_prod or unchanged:
        raise Exclude("production_diff_mismatch", unchanged)
    paths, changed_tests = regression_paths(case, base_dir, fix_dir)
    overlay, test_roots = overlay_manifest(fix_dir, changed_tests)
    case.update(base_dir=str(base_dir), fix_dir=str(fix_dir), grade_overlay_files=overlay,
                test_paths=paths, visible_test_paths=[p for p in changed_tests if (base_dir / p).is_file()])
    case["preflight"] = dict(runtime_root=str(root), pytest_config=pytest_config(fix_dir),
                             frozen_test_roots=test_roots,
                             overlay_sha256={p: digest(fix_dir / p) for p in overlay},
                             git_parents=parents, trusted_metadata_path=str(metadata_path),
                             base_archive=base_archive, fix_archive=fix_archive,
                             regression_test_files_changed=changed_tests)
    # Both runs use the identical frozen test/config/fixture overlay.
    base_test = prepare_grade(case, base_dir, case_dir / "base-test")
    fix_test = prepare_grade(case, fix_dir, case_dir / "fix-test")
    base_result = run_tests(case, base_test, case_dir / "logs", "base", args.python, args.deps)
    fix_result = run_tests(case, fix_test, case_dir / "logs", "fix", args.python, args.deps)
    case["preflight"].update(base=base_result, fix=fix_result)
    if fix_result["exit_code"] != 0 or fix_result["failed"] or fix_result["errors"] or not fix_result["tests_ran"]:
        raise Exclude("fix_regression_not_passing", case)
    if base_result["errors"] or base_result["exit_code"] != 1 or not base_result["failed"] or not base_result["tests_ran"]:
        raise Exclude("base_not_semantic_test_failure", case)
    if any("ImportError" in f["type"] or "ModuleNotFoundError" in f["type"] for f in base_result["failures"]):
        raise Exclude("base_import_failure", case)
    if base_result["tests_collected"] != fix_result["tests_collected"]:
        raise Exclude("base_fix_collection_mismatch", case)
    return case


def worker(case, args):
    try:
        return True, check_case(case, args)
    except Exclude as exc:
        if isinstance(exc.detail, dict) and "preflight" in exc.detail:
            result = exc.detail
            result["exclusion_reason"] = exc.reason
        else:
            result = dict(case, exclusion_reason=exc.reason, exclusion_detail=exc.detail)
        return False, result
    except Exception as exc:
        return False, dict(case, exclusion_reason="preflight_exception", exclusion_detail=str(exc),
                           traceback=traceback.format_exc())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--sources", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--deps", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4, choices=range(1, 5))
    parser.add_argument("--limit", type=int, help="Validate only the first N unique candidates for a smoke run.")
    parser.add_argument("--guards-only", action="store_true", help="Add full-file guards to an existing F2P output.")
    args = parser.parse_args()
    args.root = args.root.resolve()
    inside(args.output, args.root)
    if args.guards_only:
        output = json.loads(args.output.read_text(encoding="utf-8-sig"))
        validate_guards(output, args)
        return 0
    all_cases, unique, duplicates, seen_urls, seen_ids = [], [], [], set(), set()
    for source in args.sources:
        all_cases.extend(json.loads(source.read_text(encoding="utf-8-sig"))["cases"])
    for case in all_cases:
        url = case["issue_or_pr_url"].rstrip("/").lower()
        if url in seen_urls:
            duplicates.append(dict(case, exclusion_reason="duplicate_issue_or_pr_url"))
        elif case["id"] in seen_ids:
            duplicates.append(dict(case, exclusion_reason="duplicate_case_id"))
        else:
            seen_urls.add(url)
            seen_ids.add(case["id"])
            unique.append(case)
    selected = unique[:args.limit] if args.limit is not None else unique
    output = dict(cases=[], excluded=duplicates,
                  metadata=dict(candidate_count=len(all_cases), unique_url_count=len(unique),
                                selected_count=len(selected), partial=args.limit is not None,
                                sources=list(map(str, args.sources)), workers=args.workers,
                                test_execution_only=True))
    write_json(args.output, output, args.root)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        pending = {pool.submit(worker, case, args): case for case in selected}
        for future in as_completed(pending):
            eligible, case = future.result()
            output["cases" if eligible else "excluded"].append(case)
            # Preserve input order despite parallel execution.
            order = {case["id"]: i for i, case in enumerate(all_cases)}
            output["cases"].sort(key=lambda c: order[c["id"]])
            output["excluded"].sort(key=lambda c: order[c["id"]])
            write_json(args.output, output, args.root)
            print(json.dumps({"id": case["id"], "eligible": eligible,
                              "reason": case.get("exclusion_reason"),
                              "eligible_count": len(output["cases"]),
                              "excluded_count": len(output["excluded"])}, ensure_ascii=False), flush=True)
    validate_guards(output, args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Run frozen development controls without Codex or changing recorded results."""
from pathlib import Path, PurePosixPath
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent


def check(case_name, variant, verbose=False):
    case = json.loads((HERE / "cases" / (case_name + ".json")).read_text(encoding="utf-8"))
    if variant == "initial":
        overrides = {}
    elif variant == "reference":
        overrides = case["reference_files"]
    else:
        overrides = case["control_variants"][variant]
    scope = (HERE.parent.parent / ".local/v5-contract-checks").resolve()
    scope.mkdir(parents=True, exist_ok=True)
    results = {}
    with tempfile.TemporaryDirectory(prefix=case_name + "-", dir=scope) as temporary:
        work = Path(temporary).resolve()
        assert work.is_relative_to(scope)
        for name, text in {**case["files"], **overrides, **case["hidden_tests"]}.items():
            relative = PurePosixPath(name)
            if relative.is_absolute() or ".." in relative.parts or ":" in name or "\\" in name:
                raise ValueError("Fixture filename must stay within its temporary workspace")
            target = work / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        for mode, flags in (("normal", []), ("optimized", ["-O"])):
            command = [sys.executable, "-I", "-B", *flags, "-m", "unittest",
                       "discover", "-s", ".", "-p", "test_contract.py", "-v"]
            completed = subprocess.run(command, cwd=work, env=env, capture_output=True,
                                       text=True, encoding="utf-8", timeout=60)
            output = completed.stdout + completed.stderr
            counts = re.findall(r"^Ran (\d+) tests? in [0-9.]+s$", output, re.M)
            results[mode] = {"exit_code": completed.returncode,
                             "test_methods": int(counts[-1]) if counts else 0,
                             "pass": completed.returncode == 0 and bool(counts)}
            if verbose:
                print(mode + "\n" + output)
        assert work.resolve().is_relative_to(scope)
    print(json.dumps({"case": case_name, "variant": variant,
                      "modes": results, "native_model_calls": 0}))
    return 0 if all(value["pass"] for value in results.values()) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=("d09", "d10"), default="d10")
    parser.add_argument("--variant", choices=("initial", "reference", "original_constructor",
                                             "consume_all_prefix_tokens"), default="reference")
    parser.add_argument("--verbose", action="store_true")
    arguments = parser.parse_args()
    raise SystemExit(check(arguments.case, arguments.variant, arguments.verbose))

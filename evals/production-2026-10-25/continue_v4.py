"""Continue the already authorized gates after the first240 real calls finish."""
from pathlib import Path
import json
import shutil
import subprocess
import sys
import time
import runner

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent.parent


def execute(script, log, *arguments):
    with log.open("w", encoding="utf-8") as output:
        return subprocess.call([sys.executable, str(HERE / script), *arguments],
                               cwd=PROJECT, stdout=output, stderr=subprocess.STDOUT)


def main():
    lock = PROJECT / ".local/production-sequence.lock"
    with lock.open("x", encoding="utf-8") as stream:
        json.dump({"pid": __import__("os").getpid(), "started_at": runner.utc()}, stream)
    try:
        prior = HERE / "results/v3-python-v2-confirmation-conclusion.json"
        while not prior.exists():
            time.sleep(5)
        if runner.read(prior)["real_valid_runs"] != 240:
            raise RuntimeError("First confirmation is incomplete")
        draft = HERE / "candidates/v4-python-draft/SKILL.md"
        if runner.digest(draft) != "086AD88F21493E879D3C64F7A5AED6D7FF049F1BA74F88F02492C59DAE55A67F":
            raise RuntimeError("The queued development candidate changed")
        result = execute("run_v4_development.py", PROJECT / ".local/production-v4-development-iteration1.log")
        if result:
            return result
        summary = runner.read(HERE / "results/v4/development-iteration1.json")
        if not summary["development_gate_pass"]:
            return 2
        passed = PROJECT / ".local/production-runtime/dev-v4-iteration1/skill-input"
        target = HERE / "candidates/v4-python"
        if target.exists():
            for name in ("SKILL.md", "agents/openai.yaml"):
                if runner.digest(target / name) != runner.digest(passed / name):
                    raise RuntimeError("Existing candidate differs from development input")
        else:
            shutil.copytree(passed, target)
        return execute("controller_v4.py", PROJECT / ".local/production-v4-controller.log")
    finally:
        lock.unlink()


if __name__ == "__main__":
    raise SystemExit(main())

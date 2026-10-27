"""Replay the controller's exact phase input scope, including audit diagnostics."""
from pathlib import Path
import argparse
import json
from assess import assess


def replay(rows, phase):
    if any(row["phase"] not in {"formal", "replication"} for row in rows):
        raise ValueError("Use only the real-corpus rows, not development or routing records")
    # Formal was assessed before any replication row existed. Replication was
    # assessed with both phases in the journal; retain that diagnostic context.
    inputs = [row for row in rows if row["phase"] == "formal"] if phase == "formal" else rows
    return assess(inputs, phase)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=Path, required=True)
    parser.add_argument("--phase", choices=("formal", "replication"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    assert args.output.resolve() != args.records.resolve()
    rows = [json.loads(line) for line in args.records.read_text(encoding="utf-8").splitlines() if line.strip()]
    result = replay(rows, args.phase)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    print(json.dumps({"phase": args.phase, "output": str(args.output), "task_count": result["task_count"]}))

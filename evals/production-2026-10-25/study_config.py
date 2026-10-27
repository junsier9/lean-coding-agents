"""Separate the two preplanned studies while retaining every development iteration."""
from pathlib import Path
from typing import NamedTuple

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent.parent
BASE = PROJECT / ".local/production-runtime"
V3_COUNTS = {"dev-v3": 16, "routing-v3-python": 6,
             "routing-v3-python-v2": 9, "safety-v3": 72, "real-v3": 240}


class Study(NamedTuple):
    out: Path
    expected_counts: dict[str, int]
    real_branch: str
    archive_path: Path
    analysis_suffix: str


def study(version):
    if version == "v3":
        return Study(HERE / "results", dict(V3_COUNTS), "real-v3",
                     PROJECT / ".local/production-evidence.zip", "-v3-analysis.json")
    if version != "v4":
        raise ValueError("Only the two preplanned studies v3/v4 are supported")
    expected = {}
    for prefix, count in (("dev-v4-iteration", 6), ("routing-v4-iteration", 9), ("safety-v4-iteration", 36)):
        names = sorted(path.name for path in BASE.glob(prefix + "*") if path.is_dir())
        # Before model work starts, the unexecuted mandatory gates stay visible.
        for name in names or [prefix + "1"]:
            expected[name] = count
    expected["real-v4"] = 240
    return Study(HERE / "results/v4", expected, "real-v4",
                 PROJECT / ".local/production-v4-evidence.zip", "-analysis.json")

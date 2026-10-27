"""Retain completed raw evidence privately without copied dependency trees."""
from pathlib import Path
import hashlib
import json
import zipfile
import argparse
import runner
from export_results import BASE, DELIVERY_REPORTS, OUT, digest, rows
from study_config import study

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent.parent
LOCAL = PROJECT / ".local"


def retain(version="v3"):
    config = study(version)
    out = config.out
    index = runner.read(out / "evidence-index.json")
    assert index["actual_native_run_counts"] == config.expected_counts, "Finish and export all planned calls first"
    files = {}

    def add(path, name=None):
        path = path.resolve()
        assert path.is_file()
        assert path.is_relative_to(PROJECT.resolve())
        name = name or path.relative_to(LOCAL).as_posix()
        assert name not in files or files[name] == path
        files[name] = path

    for branch in config.expected_counts:
        root = BASE / branch
        for path in root.glob("*.json*"):
            add(path)
        for row in rows(out / ("runs-" + branch + ".jsonl")):
            log = root / "runs" / row["key"] if branch.startswith("routing") else root / "runs" / row["phase"] / row["key"]
            for path in log.iterdir():
                if path.is_file():
                    add(path)
            source = runner.read(log / "record.json")
            for entry in row["untracked_files"]:
                path = (Path(source["workspace"]) / entry["path"]).resolve()
                assert path.is_relative_to(Path(source["workspace"]).resolve())
                assert digest(path) == entry["sha256"]
                add(path)
        for path in (root / "grades").rglob("*"):
            if path.is_file() and path.suffix in {".log", ".xml"}:
                add(path)
        for path in (root / "recovery").rglob("*"):
            if path.is_file():
                add(path)
        for path in (root / "skill-input").rglob("*"):
            if path.is_file():
                add(path)
    for phase in ("formal", "replication"):
        for path in (BASE / config.real_branch / (phase + "-cases")).glob("*.json"):
            add(path)
            case = runner.read(path)["upstream"]
            for stage in ("base", "fix"):
                archive = case["preflight"][stage + "_archive"]
                source = Path(archive["archive_path"])
                assert digest(source) == archive["sha256"].upper()
                add(source)
    extra = (("production-blind", "production-oracle", "production-reserve-review", "production-runtime/dev-v3-oracle-v2")
             if version == "v3" else ("production-blind-v4", "production-runtime/development-v4-inputs"))
    for relative in extra:
        for path in (LOCAL / relative).rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts:
                add(path)
    for pattern in ("production-*.json", "production-*.log"):
        for path in LOCAL.glob(pattern):
            # The next study may run while the completed first study is archived.
            # Its changing controller files are not first-study evidence.
            if version == "v3" and ("-v4" in path.name or path.name.startswith("production-sequence")):
                continue
            add(path)
    for directory in (HERE, HERE.parent / "full-validation"):
        for path in directory.glob("*.py"):
            add(path, "frozen-project/" + path.relative_to(PROJECT).as_posix())
    for path in [HERE / "protocol.json", HERE / "protocol-v4.json", *sorted((HERE / "candidates").rglob("*"))]:
        if path.is_file():
            add(path, "frozen-project/" + path.relative_to(PROJECT).as_posix())
    for name in ("dev-cases", "dev-cases-v2", "development-next", "holdout-sealed", "holdout-sealed-v2", "routing-holdout-v2"):
        for path in (HERE / name).glob("*.json"):
            add(path, "frozen-project/" + path.relative_to(PROJECT).as_posix())
    for path in out.rglob("*"):
        if version == "v3" and path.is_relative_to(OUT / "v4"):
            continue
        if path.is_file() and path.name not in DELIVERY_REPORTS | {"evidence-retention.json"}:
            add(path, "public-results/" + path.relative_to(out).as_posix())
    target = config.archive_path
    temporary = target.with_suffix(".zip.tmp")
    hashes = {name: digest(path) for name, path in files.items()}
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, path in sorted(files.items()):
            archive.write(path, name)
    with zipfile.ZipFile(temporary) as archive:
        assert archive.testzip() is None
        assert set(archive.namelist()) == set(files)
        for name, expected in hashes.items():
            assert hashlib.sha256(archive.read(name)).hexdigest().upper() == expected
    temporary.replace(target)
    report = {"retained_at": runner.utc(), "study_version": version, "private_archive": target.relative_to(PROJECT).as_posix(),
              "archive_sha256": digest(target), "archive_bytes": target.stat().st_size,
              "files": len(files), "all_archive_entries_crc_and_sha256_verified": True,
              "dependency_trees_and_global_credentials_included": False,
              "other_study_active_controller_files_excluded": version == "v3",
              "raw_prompts_and_events_kept_private": True}
    runner.write(out / "evidence-retention.json", report)
    print(json.dumps(report))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", choices=("v3", "v4"), default="v3")
    retain(parser.parse_args().version)

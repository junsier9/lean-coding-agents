"""Build a two-file skill artifact and verify install/rollback bytes locally."""
from pathlib import Path
import argparse
import hashlib
import json
import shutil
import uuid
import zipfile

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent.parent
FILES = ("SKILL.md", "agents/openai.yaml")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def build(candidate, rc_number=2):
    candidate = candidate.resolve()
    qualification_path = HERE / "results/release-qualification.json"
    qualification = json.loads(qualification_path.read_text(encoding="utf-8")) if qualification_path.exists() else {}
    qualified = qualification.get("production_assistant_qualified") is True
    if qualification:
        assert qualification["skill_sha256"] == digest(candidate / "SKILL.md")
    version = "0.3.0" if qualified else f"0.3.0-rc.{rc_number}"
    expected = {name: digest(candidate / name) for name in FILES}
    assert {path.relative_to(candidate).as_posix() for path in candidate.rglob("*") if path.is_file()} == set(FILES)
    output = PROJECT / "dist"
    output.mkdir(exist_ok=True)
    target = output / ("lean-coding-" + version + ".zip")
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in FILES:
            info = zipfile.ZipInfo("lean-coding/" + name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, (candidate / name).read_bytes())
    smoke = PROJECT / ".local/production-runtime" / ("package-check-" + uuid.uuid4().hex)
    with zipfile.ZipFile(target) as archive:
        assert set(archive.namelist()) == {"lean-coding/" + name for name in FILES}
        assert archive.testzip() is None
        archive.extractall(smoke)
    installed = smoke / "lean-coding"
    assert {name: digest(installed / name) for name in FILES} == expected
    previous = HERE / "candidates/v2"
    previous_hashes = {name: digest(previous / name) for name in FILES}
    for source in (previous, candidate):
        for name in FILES:
            shutil.copyfile(source / name, installed / name)
        assert {name: digest(installed / name) for name in FILES} == (previous_hashes if source == previous else expected)
    manifest = {"version": version, "production_assistant_qualified": qualified,
                "supported_scope": "Codex + existing Python code maintenance involving redundant defenses",
                "skill_sha256": expected["SKILL.md"], "files_sha256": expected,
                "archive": target.name, "archive_sha256": digest(target),
                "package_entries_verified": 2, "install_and_rollback_byte_checks_passed": True,
                "known_good_v2_sha256": previous_hashes["SKILL.md"],
                "global_skill_directory_modified": False,
                "stable_at_least10percent_benefit": qualification.get("stable_at_least10percent_benefit"),
                "qualification_evidence": "evals/production-2026-10-25/results/release-qualification.json" if qualified else "Pending; release candidate only",
                "claude_tested": False}
    (output / ("lean-coding-" + version + ".json")).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest))
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, default=HERE / "candidates/v3-python-v2")
    parser.add_argument("--rc-number", type=int, default=2)
    args = parser.parse_args()
    build(args.candidate, args.rc_number)

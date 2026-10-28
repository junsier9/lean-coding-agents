"""Verify the frozen v7.1 release files and build a reproducible preview ZIP."""

import hashlib
import json
from pathlib import Path
import zipfile


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
CANDIDATE = ROOT / "evals/development-2026-10-28-v7-1/candidates/v7-1-python"
EXPECTED = {
    "SKILL.md": "B6DABCBC73C16EA12E95EB5F5D3E578848E000897B6164FCA4E86841DB73BEDE",
    "agents/openai.yaml": "E786C393174A36E6CAD32DD46325844B3036FC65619117984EBA926A93274E1A",
}
SUMMARY_SHA = "AD222CF297F29CCB4C6F3AED113AB4CAA1EB1914ABAED3502903B2B3E4F3E688"


def sha(data):
    return hashlib.sha256(data).hexdigest().upper()


def build():
    manifest = json.loads((HERE / "manifest.json").read_text(encoding="utf-8"))
    source = HERE / "lean-coding"
    files = {p.relative_to(source).as_posix(): p.read_bytes()
             for p in source.rglob("*") if p.is_file()}
    if set(files) != {*EXPECTED, "LICENSE"}:
        raise ValueError("Unexpected release file inventory")
    for name, expected in EXPECTED.items():
        if sha(files[name]) != expected or files[name] != (CANDIDATE / name).read_bytes():
            raise ValueError(f"Frozen candidate bytes changed: {name}")
    for name, data in files.items():
        data.decode("utf-8")
        if sha(data) != manifest["files_sha256"][name]:
            raise ValueError(f"Release manifest mismatch: {name}")
    summary_path = ROOT / "evals/development-2026-10-28-v7-1/results/summary.json"
    if sha(summary_path.read_bytes()) != SUMMARY_SHA:
        raise ValueError("Frozen development summary changed")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if summary["development_gate_passed"] is not True or summary["attempts"] != 6:
        raise ValueError("Unexpected known development evidence")
    for key in ("beta_quality_gate_passed", "production_assistant_qualified",
                "stable_efficiency_benefit_proven"):
        if summary[key] is not False or manifest[key] is not False:
            raise ValueError(f"Unqualified preview status changed: {key}")
    output = ROOT / "dist" / manifest["archive"]
    output.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(files):
            entry = zipfile.ZipInfo("lean-coding/" + name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.create_system = 3
            entry.external_attr = 0o100644 << 16
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, files[name])
    with zipfile.ZipFile(output) as archive:
        expected_names = {"lean-coding/" + name for name in files}
        if set(archive.namelist()) != expected_names or archive.testzip() is not None:
            raise ValueError("ZIP integrity failure")
        for name, data in files.items():
            if archive.read("lean-coding/" + name) != data:
                raise ValueError(f"ZIP byte mismatch: {name}")
    if sha(output.read_bytes()) != manifest["archive_sha256"]:
        raise ValueError("Reproducible ZIP digest mismatch")
    print(json.dumps({"archive": output.name, "sha256": manifest["archive_sha256"],
                      "files": len(files), "package_integrity_passed": True,
                      "production_assistant_qualified": False}, indent=2))


if __name__ == "__main__":
    build()

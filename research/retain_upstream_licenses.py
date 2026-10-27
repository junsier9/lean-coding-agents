"""Retain original license texts for the pinned upstream evaluation material."""
from pathlib import Path
import hashlib
import json
import shutil

PROJECT = Path(__file__).resolve().parent.parent
OUT = PROJECT / "research/third-party-licenses"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def retain():
    old = Path(read(PROJECT / ".local/state.json")["runtime_root"])
    corpus = read(old / "eligible.json")
    ids = {json.loads(line)["case_id"] for line in (old / "records.jsonl").read_text(encoding="utf-8").splitlines()}
    for name in ("real-selection.json", "second-corpus-selection.json"):
        selection = read(PROJECT / "evals/production-2026-10-25/results" / name)
        ids.update(selection["formal"] + selection["replication"])
    variants = {}
    OUT.mkdir(parents=True, exist_ok=True)
    for case in corpus["cases"]:
        if case["id"] not in ids:
            continue
        for side in ("base", "fix"):
            source = Path(case[side + "_dir"])
            licenses = [path for path in source.iterdir() if path.is_file() and
                        path.name.upper().startswith(("LICENSE", "LICENCE", "COPYING", "COPYRIGHT"))]
            assert licenses, "License provenance missing: " + case["id"]
            for path in licenses:
                digest = hashlib.sha256(path.read_bytes()).hexdigest().upper()
                key = (case["repo"], digest)
                filename = case["repo"].replace("/", "--") + "-" + digest[:12] + ".txt"
                destination = OUT / filename
                shutil.copyfile(path, destination)
                assert hashlib.sha256(destination.read_bytes()).hexdigest().upper() == digest
                value = variants.setdefault(key, {"repo": case["repo"], "sha256": digest,
                                                 "license_file": filename, "sources": []})
                provenance = {"commit": case[side + "_sha"], "filename": path.name,
                              "url": "https://github.com/" + case["repo"] + "/blob/" + case[side + "_sha"] + "/" + path.name}
                if provenance not in value["sources"]:
                    value["sources"].append(provenance)
    values = sorted(variants.values(), key=lambda row: (row["repo"], row["sha256"]))
    for row in values:
        row["sources"].sort(key=lambda item: (item["commit"], item["filename"]))
    index = {"scope": "Pinned base/fix license texts for evaluated or contingently selected upstream cases",
             "case_count": len(ids), "repository_count": len({row["repo"] for row in values}),
             "original_license_bytes_preserved": True, "licenses": values}
    (OUT / "index.json").write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = ["# 上游评测材料许可原文", "",
             "评测补丁及测试上下文涉及以下上游项目。原文从已固定的 base/fix 提交归档保留，按内容哈希去重；对应提交与文件来源见[索引](research/third-party-licenses/index.json)。", "",
             "这些原文对应评测材料，各文件保留其原有声明。skill 发行包的文件清单另见 `dist/` 中的发行清单。", "",
             "| 上游项目 | 许可原文 | SHA256 前缀 |", "|---|---|---|"]
    lines += [f"| {row['repo']} | [原文](research/third-party-licenses/{row['license_file']}) | `{row['sha256'][:12]}` |" for row in values]
    (PROJECT / "THIRD_PARTY_NOTICES.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"cases": len(ids), "repositories": index["repository_count"], "license_variants": len(values)}))


if __name__ == "__main__":
    retain()

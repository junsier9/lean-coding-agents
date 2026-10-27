# 历史提交复核包

当前 GitHub 工作树包含完整公共源码快照。由于本机 Git 推送连接失败，快照通过 GitHub API 提交。

[source-history-2026-10-27.bundle](source-history-2026-10-27.bundle) 保留 API 发布前本机历史至 `c5dd09d0fe5ef6e01e272aa385f5bd5282414414`，包括报告中引用的旧冻结提交。它只包含 Git 已跟踪的公共文件，不包含 `.local/` 私有证据。SHA256 与字节数见[清单](source-history-2026-10-27.json)。

下载该文件后，可用 `git bundle verify source-history-2026-10-27.bundle` 检查，再用 `git clone source-history-2026-10-27.bundle audit-checkout` 读取历史。工作树快照与历史包是不同证据入口；不要把 API 新提交 SHA 当作原本冻结的本机提交 SHA。

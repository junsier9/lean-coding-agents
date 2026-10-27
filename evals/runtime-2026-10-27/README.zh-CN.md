# 原生 Codex 连接与本地工具诊断

2026-10-27 的本机对照支持以下结论：Windows 已有无凭据回环 HTTPS 代理，但原生评测 CLI 环境没有 HTTP(S)/ALL_PROXY。相同 API 地址显式直连在 8.079 秒超时，经现有代理在 0.672 秒返回 HTTP 401（仅验证连接，不发送认证）。保持原 CLI、模型、认证、提示和 HTTP 传输参数，仅向子进程传入该代理后，原生请求在 24.610 秒正常结束，输出 `CONNECTIVITY_OK`。代理继承缺失是该次超时最有力的本地解释，短请求不证明长期连接稳定。

随后出现了独立故障：legacy elevated Windows 沙箱打开 `.sandbox-bin` 时报告 `helper_sandbox_lock_failed` / `NtCreateFile` 拒绝访问，部分备用 Node 工具也退出。仅修代理窗口已停止：计划 12，实际启动 5，保存 2 份失败记录、3 次中断，7 次未开始。该窗口保持失败，不以其他模型调用补位。旧 elevated 沙箱曾自动尝试自己的宿主配置流程；控制器没有手工修改 ACL、防火墙或保存的设置。

依 [OpenAI 官方 Windows sandbox 文档](https://learn.chatgpt.com/docs/windows/windows-sandbox)，在当前支持的设备上改为单次命令显式选择 MXC，保持 `workspace-write` 与 `never`。不调用模型的实际探针通过：能读写工作目录，目录外写入被拒绝。相同原生模型端点随后在 27.968 秒内使用 shell 读写文件并运行 Python 断言，CLI 退出 0，终止事件为 `turn.completed`，无 shell 错误。

新 `mxc_proxy_transport.py` 仅供评测控制器使用，既有冻结 `runner.py` 不变。它固定全部大小写 HTTP(S)/ALL_PROXY 与 NO_PROXY，记录完整环境和命令；不更换模型、提供方或认证，也不关闭 TLS 校验。完整命令会由同一纯函数重建并逐字核对。旧 `proxy_transport.py` 按原字节保留，其 NO_PROXY 未绑定缺口由独立合成反例记录；不用于后续新题。

这些诊断不是 skill 有效性或效率样本。须先在新的独立已知契约窗口完成 12 次验证，再冻结并调用保留的 13 道新题。旧 v4/v5/v6 失败继续保留。

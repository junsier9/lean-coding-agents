# GitHub 与 Agensi 发布草稿

状态：v4的303次专项尝试已完成，未通过生产验收，见[完整结果](../evals/production-2026-10-25/results/v4/summary.zh-CN.md)。本文仍为本地发布草稿，未上线；候选包保留 `rc` 版本号，不据此发布正式生产版。

**名称**：Lean Coding — Python Maintenance

**短描述**：Simplify redundant Python defenses while preserving real contracts and failure handling.

**介绍**：Use this skill for requested maintenance edits to existing Python code. Check the actual producer and caller before removing validation or copies. Preserve public errors, authorization, transaction atomicity, cancellation and cleanup. Reuse existing helpers and require a current reason for wrappers, fallbacks or retries. Run required and relevant checks, then finish the task.

**适用场景**：清理重复校验、纠正静默 fallback、核对复制的所有权用途、调整有界重试、修复资源与缓存错误语义。

**支持范围**：原生 Codex CLI 的已记录 Windows/Python 配置。只读讲解或审查、仅文档编辑、非 Python 请求不属于自动选择范围。Claude 尚未原生验证。

**效果证据**：最终发布时链接本仓库的逐次结果、冻结协议、独立盲审与资格结论。只有达到预设置信界的指标才写入收益宣传；当前不承诺提速或节省比例。代码冗余是否减少单独根据实际修改审查，不能用 token 下降替代代码证据。

**发行内容**：`lean-coding/SKILL.md` 与 `lean-coding/agents/openai.yaml`，无安装脚本、外部服务或运行依赖。ZIP 与逐文件 SHA256 随发行清单提供；评测工具和研究资料留在仓库，不装入 skill。

截至 2026-10-25，Agensi 的提交说明要求 ZIP 内含有效的 `SKILL.md` 及所需 frontmatter，并在上传后执行平台扫描。正式上传时仍须核对当日页面与实际扫描结果；本地格式检查不代表平台已接受。[Agensi 提交说明](https://www.agensi.io/terms)

发布前填入实际版本、资格状态与证据链接，并依据用户选择确定仓库可见性、许可与定价；不要把候选包描述成已获工业认证的产品。

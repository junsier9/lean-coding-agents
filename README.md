# Lean Coding — research preview

用 4 条简短决策规则指导 Codex 维护既有 Python 代码：减少无当前依据的重复防御和抽象，保留真实输入边界、错误传播、调用兼容、取消、事务与资源清理。

**当前尚无通过完整验收的可用测试版，也未证明稳定提速或 token 节省。Claude 尚未验证。** 原创内容按 [MIT](LICENSE) 许可；上游评测源码和摘录保留[各自许可](THIRD_PARTY_NOTICES.md)。

## 当前候选与状态

当前为 [v7.1 候选](evals/development-2026-10-28-v7-1/candidates/v7-1-python/SKILL.md)，仍为 **4 条规则、456 词**（含 frontmatter）。修订明确沿现有解析状态区分值与控制语法、检查 API 组合引入的嵌套形状，并覆盖 override/bypass 生产路径，避免观测元数据成为既有调用的新前置要求。

[v7 初稿开发窗口](evals/development-2026-10-28/README.zh-CN.md)完成 10 次，契约与端到端均 10/10，但产物审查确认 1 个自定义 parser 兼容回归，开发门槛保持 false。随后单独登记的 [v7.1 已知开发窗口](evals/development-2026-10-28-v7-1/README.zh-CN.md)完成 **6/6**，普通与 `-O`、正常结束、完整规则输出和 usage 全部通过，两个新契约产物无确认缺陷；开发门槛 true。两窗口分别保留，不合并重算或替换失败。**这不是新题泛化、因果效果或工业级生产资格，Agensi 未提交。**

历史 [v6 候选](evals/development-2026-10-27/candidates/v6-python/SKILL.md)为 4 条规则、403 词，针对整数协议与可选签名检查回归修订；其后续失败证据继续保留。

[已知开发验证](evals/development-2026-10-27/README.zh-CN.md)共 13 次：原冻结批次 11/12 契约通过，另行注册的中断产物恢复任务 1/1 契约通过；两次会话超时，因此正常结束及完整 usage 为 11/13。原开发门槛和恢复门槛都保持 false，未将恢复成绩替换原失败。缺失计量保留 null。

[原生连接与工具诊断](evals/runtime-2026-10-27/README.zh-CN.md)支持修复 CLI 代理继承缺失，并用 MXC 恢复工作目录内的工具执行。随后独立的 [12 次已知契约复核](evals/transport-revalidation-mxc-2026-10-27/README.zh-CN.md)契约通过 11/12、正常结束 11/12；其中 1 次出现明确模型容量错误，连接稳定性仍未证明，门槛保持 false。原 12+1 次结果及中途停止的仅代理窗口分别保留。

后续[原生长会话诊断](evals/long-session-2026-10-27-v1/README.zh-CN.md)完成 6/6：每次约 6.4–7.0 分钟、12 轮，共 72 次模型/shell 工具往返，无模型请求重连、容量错误或失败工具。元数据、MCP 和插件启动仍有警告；该结果不证明长期 SLA 或 skill 有效性。[下一轮 12 次已知契约复核](evals/environment-revalidation-2026-10-27-v2/README.zh-CN.md)现已完成，契约接受 12/12、正常结束 12/12、完整规则输出 12/12，按原门槛判定通过；24 份 normal/`-O` 日志和原始证据已核对。前一 11/12 窗口继续保持失败。该窗口属于已知契约复核，不作新题或效率证据。

[v6 新题泛化](evals/generalization-2026-10-27/README.zh-CN.md)已按冻结方案完成全部 **51 次**：13 道此前未参与修订的任务各 3 次，新题端到端成功 **38/39**、每题至少 2/3；另 12 次已知关键回归 **12/12**。51 次会话均正常结束，完整规则输出与 usage 为 51/51，无超时或推理连接错误。8 份预选 r0 独立审查确认 **2 个 P2 问题**：多值选项补全新增兼容回归，以及嵌套枚举默认值的原需求修复遗漏。因此零确认缺陷门槛未通过，**测试版发布资格仍为 false，Agensi 未提交**。原契约测试失败未改判或补跑，旧失败继续保留。

## 阅读与复核

- [当前 v7.1 候选 SKILL.md](evals/development-2026-10-28-v7-1/candidates/v7-1-python/SKILL.md)
- [v7 与 v7.1 的已知开发验证、产物审查及留存](evals/development-2026-10-28-v7-1/README.zh-CN.md)
- [v6 开发契约、冻结输入与实际结果](evals/development-2026-10-27/README.zh-CN.md)
- [v5 未参与修订任务的泛化结果与独立审查](evals/generalization-2026-10-26/README.zh-CN.md)
- [完整生产维护验证入口](evals/production-2026-10-25/README.zh-CN.md)
- [论坛、GitHub 案例与研究来源](research/2026-10-24-findings.zh-CN.md)

`skills/lean-coding/` 保留历史 prototype-2，供复跑旧研究；`dist/` 中旧 rc 包也不代表已获资格。当前 v7.1 位于上方候选目录，尚未替换历史入口或安装到全局 skill 目录。

## 保留的历史证据

| 批次 | 范围与结论 |
|---|---|
| [早期开发任务](evals/README.md) | 正反行为契约和验证方法；不是生产效率证明 |
| [prototype-2 完整验证](evals/full-validation/README.zh-CN.md) | 50 题、300 次；未达到预设稳定至少 10% 收益门槛 |
| [v4 专项验证](evals/production-2026-10-25/results/v4/summary.zh-CN.md) | 303 次；生产资格 false，效率收益未稳定证明，审查确认显式参数值回归 |
| [v5 新题泛化](evals/generalization-2026-10-26/results/conclusion.zh-CN.md) | 新题契约 59/60、已知关键边界 12/12；两次会话未正常结束，8 份预选审查发现两项兼容回归；完整测试版资格 false |
| [v6 已知开发](evals/development-2026-10-27/README.zh-CN.md) | 契约接受 12/13，正常结束 11/13；仅为已知开发题 |
| [v6 新题泛化](evals/generalization-2026-10-27/README.zh-CN.md) | 全部 51 次完成；新题端到端 38/39、已知关键回归 12/12；8 份预选审查确认 2 个 P2，测试版资格 false |
| [v7 初稿开发](evals/development-2026-10-28/README.zh-CN.md) | 10/10 契约与端到端；产物审查确认 1 个 P2，开发门槛 false |
| [v7.1 已知开发](evals/development-2026-10-28-v7-1/README.zh-CN.md) | 6/6 契约与端到端；两个新契约产物无确认缺陷，开发门槛 true；尚无新题或效率证据 |

不剔除失败、不把缺失 usage 填零、不按新门槛重评旧失败。已揭露回归只用作开发题；后续新题另行固定。没有无 skill 对照的批次不能估计 skill 因果收益。现有任务来自有限 Python 维护项目，不证明新仓库泛化或训练数据无污染。

公共仓库保留规则、研究来源、测试源码、规范化结果和补丁。原始私有日志归档及本机工作区位于忽略的 `.local/`，不随 GitHub 上传；保留清单包含 SHA256 与覆盖边界。完整原生复跑需要自己的 Codex 登录、模型访问与相应运行环境。

旧冻结提交可通过[可复核历史包](research/README-history.zh-CN.md)读取；GitHub API 快照的新提交 SHA 与原本机冻结提交 SHA 分别保留。

# Lean Coding — research preview

用 4 条简短决策规则指导 Codex 维护既有 Python 代码：减少无当前依据的重复防御和抽象，保留真实输入边界、错误传播、调用兼容、取消、事务与资源清理。

**当前尚无通过完整验收的可用测试版，也未证明稳定提速或 token 节省。Claude 尚未验证。** 原创内容按 [MIT](LICENSE) 许可；上游评测源码和摘录保留[各自许可](THIRD_PARTY_NOTICES.md)。

## 当前候选与状态

[v6 候选入口](evals/development-2026-10-27/candidates/v6-python/SKILL.md)仍为 4 条规则，入口文件按空白分词为 403 词（含 frontmatter）。它针对已确认的整数协议及可选签名检查兼容回归做了小幅修订。

[已知开发验证](evals/development-2026-10-27/README.zh-CN.md)共 13 次：原冻结批次 11/12 契约通过，另行注册的中断产物恢复任务 1/1 契约通过；两次会话超时，因此正常结束及完整 usage 为 11/13。原开发门槛和恢复门槛都保持 false，未将恢复成绩替换原失败。缺失计量保留 null。

[原生连接与工具诊断](evals/runtime-2026-10-27/README.zh-CN.md)支持修复 CLI 代理继承缺失，并用 MXC 恢复工作目录内的工具执行。随后独立的 [12 次已知契约复核](evals/transport-revalidation-mxc-2026-10-27/README.zh-CN.md)契约通过 11/12、正常结束 11/12；其中 1 次出现明确模型容量错误，连接稳定性仍未证明，门槛保持 false。原 12+1 次结果及中途停止的仅代理窗口分别保留。

后续[原生长会话诊断](evals/long-session-2026-10-27-v1/README.zh-CN.md)完成 6/6：每次约 6.4–7.0 分钟、12 轮，共 72 次模型/shell 工具往返，无模型请求重连、容量错误或失败工具。元数据、MCP 和插件启动仍有警告；该结果不证明长期 SLA 或 skill 有效性。[下一轮 12 次已知契约复核](evals/environment-revalidation-2026-10-27-v2/README.zh-CN.md)现已完成，契约接受 12/12、正常结束 12/12、完整规则输出 12/12，按原门槛判定通过；24 份 normal/`-O` 日志和原始证据已核对。前一 11/12 窗口继续保持失败。这是已知契约复核，尚无新题或效率结论。

剩余 13 道未参与修订的新题尚未调用。[泛化方案与输入](evals/generalization-2026-10-27/README.zh-CN.md)已绑定本轮 12/12 合格环境、完成独立方法审查并冻结：39 次新题加 12 次关键回归，合计 51 次；当前原生调用为 0，冻结方案不能作测试结果或发布资格。当前公开的是研究预览；Agensi 可用测试版须待完整质量验收后提交，不能宣传工业级生产就绪。

## 阅读与复核

- [当前候选 SKILL.md](evals/development-2026-10-27/candidates/v6-python/SKILL.md)
- [v6 开发契约、冻结输入与实际结果](evals/development-2026-10-27/README.zh-CN.md)
- [v5 未参与修订任务的泛化结果与独立审查](evals/generalization-2026-10-26/README.zh-CN.md)
- [完整生产维护验证入口](evals/production-2026-10-25/README.zh-CN.md)
- [论坛、GitHub 案例与研究来源](research/2026-10-24-findings.zh-CN.md)

`skills/lean-coding/` 保留历史 prototype-2，供复跑旧研究；`dist/` 中旧 rc 包也不代表已获资格。当前 v6 位于上方候选目录，尚未替换历史入口或安装到全局 skill 目录。

## 保留的历史证据

| 批次 | 范围与结论 |
|---|---|
| [早期开发任务](evals/README.md) | 正反行为契约和验证方法；不是生产效率证明 |
| [prototype-2 完整验证](evals/full-validation/README.zh-CN.md) | 50 题、300 次；未达到预设稳定至少 10% 收益门槛 |
| [v4 专项验证](evals/production-2026-10-25/results/v4/summary.zh-CN.md) | 303 次；生产资格 false，效率收益未稳定证明，审查确认显式参数值回归 |
| [v5 新题泛化](evals/generalization-2026-10-26/results/conclusion.zh-CN.md) | 新题契约 59/60、已知关键边界 12/12；两次会话未正常结束，8 份预选审查发现两项兼容回归；完整测试版资格 false |
| [v6 已知开发](evals/development-2026-10-27/README.zh-CN.md) | 契约接受 12/13，正常结束 11/13；尚无新题泛化结论 |

不剔除失败、不把缺失 usage 填零、不按新门槛重评旧失败。已揭露回归只用作开发题；后续新题另行固定。没有无 skill 对照的批次不能估计 skill 因果收益。现有任务来自有限 Python 维护项目，不证明新仓库泛化或训练数据无污染。

公共仓库保留规则、研究来源、测试源码、规范化结果和补丁。原始私有日志归档及本机工作区位于忽略的 `.local/`，不随 GitHub 上传；保留清单包含 SHA256 与覆盖边界。完整原生复跑需要自己的 Codex 登录、模型访问与相应运行环境。

旧冻结提交可通过[可复核历史包](research/README-history.zh-CN.md)读取；GitHub API 快照的新提交 SHA 与原本机冻结提交 SHA 分别保留。

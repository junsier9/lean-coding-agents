# v6 新题泛化：51 次完成，发布门槛未通过

本轮按冻结方案完成全部 **51 次原生 Codex 调用**，没有补跑或替换失败。13 道此前未参与本候选修订的任务各做 3 次，共 39 次；另做 12 次已知关键契约。候选、模型、环境、门槛和审查选择在执行中保持冻结。

| 检查 | 实际结果 | 原门槛 |
|---|---|---|
| 新题端到端成功 | **38/39**；唯一契约失败保留 | 至少 38/39，通过 |
| 每题重复结果 | 每题至少 **2/3** | 至少 2/3，通过 |
| 已知关键契约 | **12/12**，分别核对 normal 与 `-O` | 12/12，通过 |
| 会话、规则和计量 | 正常结束、完整规则输出及完整 usage 均 **51/51**；无超时或推理连接错误 | 同时进入端到端判断 |
| 固定 r0 独立审查 | 8 份齐全：4 pass、2 pass_with_scope_note、2 needs_changes；**2 个 P2** | 零确认缺陷，**失败** |

**v6 的 beta 发布门槛未通过。** 源码、原始事件、评分日志、固定审查输入和留存归档由未修改的[冻结核验器](verify.py)复核，完整判断见 [beta-qualification.json](results/beta-qualification.json)。不向 Agensi 提交该候选，不宣传工业级生产资格、稳定提速或 token 节省。本批次没有无 skill 对照，不能估计因果收益。

两项独立确认问题分别是：

- [case-08：新增补全兼容回归](results/confirmed-regressions/case-08/README.zh-CN.md)。`--pair` 尚缺第二个值时，已接受的合法值 `-1`、`-` 或 `--flag` 被误认为另一个选项，补全错误切换到位置参数。原版 6 个诊断用例通过，候选 6 个失败；父代理最小复现确认。
- [case-05：嵌套枚举默认值遗漏](results/requirement-omissions/case-05/README.zh-CN.md)。合法的 `multiple=True, nargs=2` 默认值仍显示枚举内部表示；四类枚举和两种大小写共 8 例重现。原版也存在，属于修复不完整，未记为新增回归；父代理另行确认。

诊断测试是在审查产物后增加的证据，不进入原冻结评分器或本轮规则修订。两项发现只说明本轮产物存在问题，不足以证明由 skill 引起。

唯一原始契约失败是 `b-07-click-2333/r0` 的入口名称断言。独立初审与明确标注的初审后定向补查未确认真实入口缺陷；其覆盖也未包括实际构建 Shiv/PEX。该审查结论**没有覆盖或改判契约失败**，失败仍在 39 次分母中。见[差异记录](results/case-04-grade-review-adjudication.json)、[初审](results/independent-review/case-04-initial-review-a.json)及[定向补查](results/independent-review/case-04-post-blind-supplement.json)。

case-03 的比较 `TypeError` 传播差异与 case-07 的既有 CliRunner EOF 模拟限制，按有依据的契约判断保留为范围说明，未计确认缺陷；真实入口、最小复现与审查限制见[原始独立报告](results/independent-review/source-report-index.json)。

复核入口：[新题记录](results/generalization-runs.jsonl)、[新题汇总](results/generalization-summary.json)、[关键回归记录](results/known-critical-runs.jsonl)、[关键回归汇总](results/known-critical-summary.json)、[原字节审查报告与输入哈希](results/independent-review/source-report-index.json)、[原始证据留存](results/evidence-retention.json)、[完整工作区补充留存](results/workspace-retention.json)、[轨迹人工判断](results/trace-adjudication.json)、[私有安装包检查](results/package-validation.json)。原始归档位于忽略的 `.local/`，不随 GitHub 上传；工作区补充归档包括未跟踪文件及非 Python 资源。安装包仅私有暂存，未获发布资格。

本轮已揭露的任务只能作为后续开发材料；后续候选若再使用这些任务，不再称为未见新题。下一轮泛化须另行封存新任务。

## 冻结方案与准备记录

本方案绑定 `environment-revalidation-2026-10-27-v2` 的原 12/12 合格结果，七份报告、核验代码、原始证据归档及历史失败摘要均按 SHA256 固定。登记与冻结时原生模型调用为 **0**；对应准备记录按当时状态保留，没有改写为执行结果。上述 51 次是后续按冻结方案实际完成的独立执行窗口。

固定取原 `remaining_adequate` 顺序中位置 `20:` 的剩余 13 道任务，每题 3 次，共 39 次；另做 12 项已知关键契约，各 1 次，总计 **51 次**，最多 3 个原生客户端并发。13 道任务按历史完成记录与未完成启动元数据排重，并核对仓库/fix SHA 和规范化来源 URL。已知契约只用于回归保护，不增加新题样本数。

原质量门槛保持不变：新题端到端成功至少 **38/39**，每题至少 **2/3**，关键契约 **12/12**；8 个事先选定的 r0 产物须完成独立审查且无确认缺陷。端到端成功同时要求契约通过、CLI 正常成功结束和完整冻结规则输出；接口错误、超时、未完成或未调用的位置保留在完整分母中，不补跑或替换失败，不填补缺失 usage。

候选保持 v6 原字节，Codex 模型仍为 `gpt-6.1-sol/xhigh`，使用同一 CLI、Python、认证、固定系统代理及显式 MXC 的 `workspace-write`/`never`。规则、UI、源码、许可、案例来源与归档、base/fix 输入、依赖、历史排重索引、51 个调度位置和审查选择均已冻结。准备回放得到相同 freeze SHA。

本轮独立方法审查的 26 个 readiness 正反探针和调度检查通过；审查只使用方法源码与合成数据，未读取新题解法、候选正文或 solver 产物。之前 B1–B3 与代理绕过问题的原报告和修复复核继续保留，不能据新结果重标旧失败。

复核入口：[协议](protocol.json)、[冻结清单](results/freeze.json)、[冻结时间与摘要](results/frozen-at.json)、[准备核验](results/preparation-verification.json)、[独立方法审查](results/readiness-binding-method-review.json)、[私有输入归档清单](results/prepared-input-retention.json)。

该批次只有 with-skill 一个实验臂，不估计速度、token 或因果收益，不重开旧效率确认性检验。任务来自已有仓库且经过源码契约筛查，不能证明新仓库泛化或训练数据无污染。即使后续达到本方案的 beta 质量门槛，也不构成工业级生产资格；Claude 尚未验证。

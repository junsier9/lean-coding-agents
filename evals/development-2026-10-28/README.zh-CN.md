# v7 已知失败驱动的开发修订

本窗口已完成固定 **10 次**原生调用：契约通过、端到端成功、正常结束、完整规则输出和 usage 均为 **10/10**。6 个新契约产物逐份审查后，确认 d13/r1 存在 **1 个 P2**：新 parser 私有字段成为普通命令调用的前置要求，破坏了既有 `make_parser` 覆写。原始契约分数没有重评，完整[开发门槛](results/summary.json)为 **false**；原始日志、最终工作区与实际评分源码已绑定并[留存](results/evidence-retention.json)。

问题见[最小复现](results/confirmed-findings/custom-parser/probe.py)、[普通及优化模式结果](results/confirmed-findings/custom-parser/results.json)和[产物审查](results/artifact-review.json)。它是在审查后发现的开发问题，不属于原冻结隐藏评分器。随后的 [v7.1 修订窗口](../development-2026-10-28-v7-1/README.zh-CN.md)单独登记，不能用后续结果覆盖本窗口失败。

首次预启动还因冻结清单包含回退评分耗时而被一致性检查拦截，当时原生调用为 0。[原冻结及登记](results/prelaunch-v1/record.json)、初始方法审查、修正复核和预启动错误均保留；修正后连续两次准备重放一致，再另行登记并完成本次 10 个位置。

[v7 候选](candidates/v7-python/SKILL.md)仍为 4 条规则，按空白分词为 443 词（含 frontmatter，v6 为 403 词）。没有安装到全局目录，也没有替换历史候选或更改旧结果。

修订针对 v6 固定产物审查确认的两类问题：沿既有 API 组合产生的数据形状检查消费者；按解析或绑定状态区分值与控制语法；相关验证覆盖会改变形状或状态的已支持组合。保留原有错误传播、取消、授权、事务、资源清理、接收者关键字、整数索引协议及可选检查规则。规则中没有加入 Click 的具体题目答案或要求穷举所有组合。

本轮仅做**已揭露开发验证**。d13/d14 从 v6 的失败产物抽取完整 Click 源码包及原许可；可见材料、独立构造的参考修复、隐藏行为契约分别保存。测试先证明初始版本失败、参考通过、直接回退旧实现仍不满足维护需求，普通及 `-O` 模式均有非零测试。隐藏测试不进入原生客户端的工作区。

[协议](protocol.json)固定最多 10 次原生 Codex：d13/d14 各 3 次，旧 d09–d12 各 1 次，最多 3 个并发。复用上一轮的 CLI、Python、模型和既有代理/MXC 路由，不更换模型或认证。失败不补跑替换，usage 缺失保留 null；原始证据及最终工作区私有留存，公共结果保留摘要与补丁。

候选修订、fixture 构造和独立前向检查均已知道问题，不能作为盲测、新题泛化或 skill 因果效果。即使 10 次全部通过，也只通过本轮开发门槛；v6 原发布失败保持不变，仍无稳定提速、token 节省或工业级生产资格，Claude 尚未验证，不据此向 Agensi 提交。下一次泛化需另行封存未参与修订的新任务。

执行入口为 `run.py prepare`、`run.py run`、`run.py close`。本窗口已经关闭，禁止改写冻结输入或以新模型尝试替换失败。方法与结果见[原方法审查](results/method-review.json)、[第一轮修正复核](results/method-fixes-review.json)、[预启动修正复核](results/prelaunch-fixes-review.json)、[最终登记](results/registration.json)、[固定输入](results/freeze.json)和[真实结果](results/summary.json)。私有归档已保留，收尾不能覆盖重写原归档。

# Codex 完整验证

已完成 prototype-2 的 50 个任务、300 次有效运行，另有 3 次原生加载 smoke。Claude 按用户要求跳过。

**结论：有正向效率信号，但尚未达到预设的“稳定至少节省 10%”门槛。** 正式耗时下降 8.6%，复验耗时下降 7.7%、token 下降 13.7%；置信界仍不足以保证 10%。正式 token 因一次基线超时缺失而只作保守推断，原记录保留 null。

- [完整中文报告：结果、局限及重算命令](results/summary.zh-CN.md)
- [冻结协议](preregistered-protocol.json)、[冻结规则](skill-frozen.md)、[确认分组及原始 pilot 规划](results/partition-confirmatory.json)
- [正式分析](results/formal-analysis.json)、[正式 token 保守界](results/formal-token-bound.json)、[复验分析](results/replication-analysis.json)、[最终机器结论](results/conclusion.json)
- [300 次逐次数据及补丁索引](results/runs.jsonl)、[语料与归档哈希](results/corpus-manifest.json)、[证据索引](results/evidence-index.json)
- [最终记录与数值核对](results/final-integrity.json)、[全部初始输入核对](results/initial-input-integrity.json)、[工具轨迹审查](results/trace-audit.json)
- [匿名补丁审查](results/blind-audit-decoded.json)、[原生加载与错误路径检查](results/native-loading-smoke.json)
- [评测工具 36 项检查及 skill 格式检查](results/harness-validation.json)、[运行环境](results/runtime-environment.json)
- [缺失 usage 方法审查](results/missing-usage-bound-method-review.json)、[Pearson 题面复核](results/pearson-task-contract-audit.json)、[参考快照恢复账本](results/snapshot-restoration.json)

入口与冻结文件 SHA256 均为 `8C757603BBADE8FF688FD0A08CC5F8A095893A2593363B51EC983F4F70FA74BF`。本轮未根据正式或复验结果改规则、任务或门槛。

每批 20 个新任务，两组各重复 3 次；以任务为簇做 20,000 次配对 bootstrap。两批均需通过观测质量门槛，且对应成本比的单侧 97.5% 上界均 <0.90，才宣称稳定至少 10% 收益。失败成本保留，cached input 不重复计数，缺失 token 不填零。

主要脚本为 [预筛与评分](preflight.py)、[模型运行与检查点恢复](run_experiment.py)、[统计分析](analyze.py)、[保守界](bound_analysis.py)、[完整编排](validate_all.py)、[原生 smoke](native_loading_smoke.py)。本机原始事件、提示词与检查点保存在被 Git 忽略的 `.local/` 证据备份中；跨平台的一键模型复跑尚未验证。

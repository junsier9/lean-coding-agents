# Codex 追加验证：独立、多文件任务

本轮按用户要求暂跳过 Claude。冻结 prototype-1 的 skill 入口，增加 6 个多文件任务，每个任务每个条件使用新会话。基线组保留 Codex 内置和当前环境指令，只不显式加载本项目 skill。

| 任务 | 现实维护场景 | 正例 / 反例判据 |
|---|---|---|
| h01 | 数据已在导入时验证，但随后允许可变插件修改 | 删除插件前重复检查；保留插件后的真实校验和异常传播 |
| h02 | 统计页把远端失败显示成 0，SDK 已有有限恢复 | 传播故障给页面；不叠加第二层重试 |
| h03 | CLI 参数名拼错，getattr 默认值掩盖错误 | 修复真实 Options 字段；不增加配置或兼容分支，遗漏必填属性仍可见 |
| h04 | 两种 reducer，一种返回输入、一种分配结果；预览还要排序 | 减少重复复制，同时保留输出独立、原始视图和失败语义 |
| h05 | 小文案改动并交付 dev build | focused 与 dev-build 足够，不启动发布专用 UI 流程 |
| h06 | 可选缓存缺失 / 格式错与真实 I/O 故障 | 保留预期 cache miss；权限 / I/O 失败可见；资源关闭且无需新增 context-manager 协议 |

这些是改写的标准库维护项目，不是原仓库回放。独立评分器包含 19 个测试方法及多输入子例；源码与判据在执行结束前不提供给执行 agent。

## 执行与复跑

准备独立目录：

~~~powershell
python evals/codex_contracts.py prepare --root C:\Temp\lean-coding-codex-new
~~~

h01 / h02 在当前 Codex 协作环境中按每任务、每条件启动全新 agent；不继承研究上下文。h03 至 h06 使用本机 Codex CLI，为每任务、每条件创建独立 ephemeral 会话。两种执行方式分层记录，不混用模型或耗时推断效果。

CLI 脚本沿用用户当前模型设置，不提供 model 或 reasoning override：

~~~powershell
python evals/run_codex_cli.py --root C:\Temp\lean-coding-codex-new --logs C:\Temp\lean-coding-codex-logs --skill C:\path\to\skills\lean-coding\SKILL.md --cases h03 h04 h05 h06 --workers 2
~~~

完成后运行同一评分器：

~~~powershell
python evals/codex_contracts.py score --root C:\Temp\lean-coding-codex-new --arm baseline --output evals/results/new/baseline.json
python evals/codex_contracts.py score --root C:\Temp\lean-coding-codex-new --arm with-skill --output evals/results/new/with-skill.json
~~~

CLI JSONL 中的 turn.completed.usage 作为记录用量，缓存输入是输入的子集；不是账单。命令项与文件修改项按事件类型计数，不解释成所有工具调用。墙钟时间可能含连接回退、排队和沙箱开销，只作诊断。

本轮 CLI 首次 READY 检查在 WebSocket 重试后经 HTTPS 成功，随后尝试的局部 transport 配置被版本校验拒绝。正式任务不修改配置文件、模型或传输设置。基础设施事件另行记录，不当作 skill 决策失败。


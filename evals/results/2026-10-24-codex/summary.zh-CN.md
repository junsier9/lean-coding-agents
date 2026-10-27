# Codex 追加验证与规则修订

日期：2026-10-24（Asia/Singapore）  
范围：按用户要求继续测试 Codex；Claude 环境暂跳过。

## 结论

追加 6 组多文件任务对照，两组均完成 6/6 个任务并通过 19/19 项契约检查。测试支持安全简化与必要错误处理可以同时保留；**仍未证明这个 skill 相比当前 Codex 基线有稳定的增量收益。**

本轮还发现并修正了一处漏删：旧版 skill 组在不运行插件时仍做重复全量校验。将规则改为按执行路径判断后，新上下文定向复测删除了这次扫描，并保留插件修改后的校验。

当前入口已更新为 prototype-2。完整六组对照使用 prototype-1；prototype-2 只对该修订做了定向复测，不能把旧版全套结果当作新版重新跑过的证明。

## 执行方式

| 场景 | 执行环境 | 每条件会话 |
|---|---|---:|
| h01 可变插件、h02 SDK 重试与页面错误 | 当前 Codex 协作代理；同一继承模型，无覆盖 | 每任务各 1 个新上下文 |
| h03 CLI 参数、h04 复制隔离、h05 dev 构建、h06 缓存错误 | 原生 Codex CLI 0.164.0-alpha.11.1；用户配置 gpt-6.1-sol / xhigh | 每任务各 1 个 ephemeral 会话 |

两种执行方式分层记录。只比较同一任务、同一环境下的条件；不把模型、服务排队或工具开销混合解释成 skill 效果。CLI 没有提供上游 resolved model ID，表中型号是已核对的用户配置。

执行器只收到任务、项目文件及实验组 skill，不获得评分器、预期改法或研究结论。评分依据父 agent 的实际运行与 diff 审查；执行器自述测试不替代父 agent 结果。

## 实际行为

| 场景 | baseline | prototype-1 skill |
|---|---|---|
| h01 校验与插件 | 删除解析后的重复类型判断；只在插件后重新校验 | 删除重复类型判断；插件后校验正确，但无插件时仍重新扫描 |
| h02 远端失败 | 取消失败返回零；由页面显示 unavailable；SDK 重试不变 | 相同，未叠加第二层重试 |
| h03 必填属性 | 直接读取正确的 max_rows，遗漏属性仍抛错 | 相同 |
| h04 复制与排序 | 只在 reducer 返回输入本身时复制 | 相同的所有权判断，另一种清晰写法 |
| h05 小改动交付 | focused + dev-build，未执行 release-ui | 相同 |
| h06 可选缓存 | 保留缺失 / 坏 JSON 的 None，I/O 故障可见，closing 管理 read/close 对象 | 保留同样语义，以较小的 try/except/finally 改动减少嵌套 |

两组都通过非法输入、插件破坏数据、重试次数、异常实例、输出别名、读取阶段 FileNotFoundError、权限失败及资源释放等检查。基线 h06 另写了一个有意义的行为测试文件，实验组用内联检查；没有仅凭测试文件存在就判定其过度工程。

原始记录：[baseline 结果](baseline.json)、[skill 结果](with-skill.json)、[baseline diff](baseline.diff)、[skill diff](with-skill.diff)、[实验协议](protocol.json)。

## 减少复制是否带来实际收益

在同一 Python 3.13.0 中，用 100,000 个整数、3 次测量的 tracemalloc 峰值中位数检查 h04。输入列表在开始追踪前分配。

| reducer | 原始代码 | baseline 简化后 | skill 简化后 |
|---|---:|---:|---:|
| IdentityReducer | 1,600,432 B | 800,360 B | 800,320 B |
| DoubleReducer | 4,797,312 B | 3,997,232 B | 3,997,200 B |

相对原始代码，skill 组分别减少约 50% 和 16.7% 的被追踪峰值，输入保持不变、输出仍独立。基线取得几乎相同的改善，几十字节的差异没有实质比较意义。这是特定函数的 Python 分配测量，不是进程 RSS 或整个 agent 的内存、速度结论。[测量参数与全部样本](h04-memory.json)

## CLI 记录用量

只汇总 h03–h06 的四组原生 CLI 对照：

| 记录指标 | baseline | skill |
|---|---:|---:|
| 输入 tokens（含缓存） | 890,148 | 964,015 |
| 其中缓存输入 tokens | 790,528 | 882,688 |
| 非缓存输入 tokens | 99,620 | 81,327 |
| 输出 tokens | 14,891 | 14,775 |
| 完成的命令执行项 | 38 | 39 |

缓存输入属于输入的子集，不能再相加。以上是 turn.completed 记录，不是账单；也没有据此换算金额。输入总量更高、非缓存输入更低、输出相近，样本不足以证明稳定节省。

所有正式 CLI 会话都经历 WebSocket 重连后 HTTPS 回退，部分命令还涉及 Windows Python 解释器发现；其中一次 Git 状态检查发生在无 Git 元数据的合成项目中。墙钟时间包含这些开销，不作提速宣传。连接失败与配置校验失败另行记录，没有视作 skill 的代码判断失败。[逐任务用量与事件计数](cli-usage.json)

## 为什么修订第三条规则

prototype-1 在 h01 的无插件路径仍调用 validate_current，尽管 parse_readings 已保证刚构造的数据有效。基线省去了这次扫描，说明“功能测试全绿”仍可能漏掉冗余工作。

将第三条中较笼统的不变量要求替换为：

> A guard is redundant only when an invariant covers every path that reaches it. If only some paths can break that invariant, keep or move the guard onto those paths.

没有增加固定行数、重试次数、额外审批或强制 review。其余规则保持不变。

prototype-2 在同样原始 h01 输入上，以新上下文显式加载候选；没有告知执行器漏删位置或期望改法。父 agent 定向复测：

- 4/4 项契约检查通过，包括坏输入、坏插件修改与异常传播。
- 无插件时校验调用为 0；运行插件后为 1。
- 实际 diff 将校验放入插件执行分支，随后提升为当前入口。

这次结果支持窄范围修订纠正了已观察到的漏删，不足以证明新版整体优于基线。没有继续扩张全套测试或按一次结果添加通用限制。[定向结果](targeted-v2.json)、[定向 diff](targeted-v2.diff)

## 版本与局限

- prototype-1 完整对照入口 SHA-256：9D6CD83C1EDC9B2CCF6B85242BF7C3CB09CC1EFB325A797A881031FAF35C033E。
- prototype-2 当前入口 SHA-256：8C757603BBADE8FF688FD0A08CC5F8A095893A2593363B51EC983F4F70FA74BF。
- 每个任务每个条件仅一次观察；合成项目不是生产回放。
- 当前 Codex 基线已有内置及用户指令，测试的是叠加本 skill 的增量。
- 此轮验证显式加载；自动触发、Claude 和真实生产提速均未验证。
- 格式验证已通过；格式正确不等于效果已证明。

下一目标：整理 GitHub 发布说明，保留这些效果与验证范围的限制。


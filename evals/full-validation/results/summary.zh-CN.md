# Lean Coding prototype-2：Codex 完整验证结果

2026-10-25（Asia/Singapore）。已完成 50 个真实维护任务、300 次有效对照运行，以及另外 3 次原生加载 smoke。结论：出现正向效率信号，观测验收通过率没有下降；**尚未证明达到预设的“稳定至少节省 10% token 或耗时”门槛**。该结论不等于证明没有任何收益。

| 批次 | 任务 / 有效运行 | 基线验收 | skill 验收 | token 节省 | 耗时节省 |
|---|---:|---:|---:|---|---|
| pilot，仅用于规划 | 10 / 60 | 30/30 | 30/30 | 不作确认性结论 | 不作确认性结论 |
| 正式 | 20 / 120 | 57/60（95.0%） | 57/60（95.0%） | 实际比值未识别；保守单侧 97.5% 节省下界 3.8% | 8.6%，95% 区间 [2.5%, 14.5%] |
| 独立复验 | 20 / 120 | 58/60（96.7%） | 59/60（98.3%） | 13.7%，95% 区间 [4.0%, 23.7%] | 7.7%，95% 区间 [0.7%, 15.1%] |

百分比使用“所有尝试成本 / 验收成功数”的比值，包含失败成本。两批观测质量门槛均通过，但相同指标必须在两批都满足成本比置信上界 **<0.90**，才能达到本轮预设目标。

| 成本比的单侧 97.5% 上界 | 正式 | 独立复验 | 预设要求 |
|---|---:|---:|---:|
| token | 0.9618，保守界 | 0.9600，完整 usage | 两批均 <0.90 |
| 耗时 | 0.9753 | 0.9930 | 两批均 <0.90 |

正式批次已知基线 token 为 26,330,005，缺少一次超时运行的最终 usage；skill 为 23,232,411。由此得到的 **0.8824 是观测成本比上界，不能写成实际 token 比值点估计**。复验计量完整：基线 25,869,944、skill 22,702,447 tokens；每次验收成功成本分别为约 446,034 和 384,787 tokens。正式每成功耗时约 260.6 秒与 238.2 秒，复验约 212.0 秒与 195.7 秒。

## 原生加载与正反路径

在三个独立仓库中使用 `.agents/skills/lean-coding/`，分别检验显式 `$lean-coding`、未点名 skill 的相关重构、README 拼写修正。仓库级发现方式同时经本地 CLI 目录探针与 [OpenAI 官方 skill 文档](https://learn.chatgpt.com/docs/build-skills)核对。

- 显式与隐式相关任务均实际读取规则正文，只修改 `logic.py`；内部两个重复条件和一轮重复类型检查被删除。外部 list 与严格 int 验证、PayloadError、独立 tuple 所有权快照保持；两次各通过 7 个冻结契约测试，涵盖空输入、非法输入、bool/float/string、调用者非变异、快照不受后续变异影响、原 list 仅迭代一次。
- README 负例仅将 `helo` 改为 `hello`；源码、测试和 skill 均未变，轨迹中未观察到规则正文或读取命令。由于 JSON 轨迹不暴露完整模型输入，这不能证明绝对未激活，也不能估计自动选择可靠率。
- 这些任务没有另设基线，不能把清理重复校验的结果单独归因为 skill。它们只支持加载路径与功能行为可用，不能增加正式效率结论的样本量。3 次 smoke 计量合计 351,664 tokens，单独保留，不计入主 300 次。

可查看 [显式补丁](native-smoke-explicit.patch)、[隐式补丁](native-smoke-implicit.patch)、[README 补丁](native-smoke-negative.patch)及 [原生记录](native-loading-smoke.json)。



## 本轮检验了什么

冻结 prototype-2 的 6 条规则；pilot 后按预定公式确定并冻结每批 N=20，未据正式或复验结果修改 skill、任务分组、门槛或样本量。配置为 Codex CLI `0.164.0-alpha.11.1`、`gpt-6.1-sol`、`xhigh`，Python 3.12.14；这是本机配置的模型别名，不能证明服务端模型修订始终相同。HTTP provider 只在评测进程内设置，复用已有认证，未修改全局配置。

设计为试验 10 题，正式 20 题，独立复验 20 题，每题两组各运行 3 次，计划共 300 次有效评测。每次使用新上下文和独立 Git 工作目录，成对随机且平衡先后顺序，最多并行 3 个模型进程。正式批次为 Click 6 题、boltons 7 题、more-itertools 7 题；复验分别为 7、7、6 题。试验还包含 itsdangerous 与 pluggy。

来源共 250 条记录，去重和运行预筛后 177 个案例准入；实际模型评测仍是初始固定顺序中的 50 个案例，另外 127 个为备用。来源预筛中的 33,599 项修复版本 guard 测试通过，不能算成额外独立模型样本。初始协议和样本量文件中的 80 条来源、61 个准入数是当时的初始语料状态，扩充并未改变实际确认分组。

独立评分恢复冻结的上游完整相关测试文件、配置及 fixtures，候选修改测试或配置不能替代评分。要求测试收集数匹配且非零。评分通过表示这些检查覆盖的行为正确，不能保证全部生产行为无回归。

## 如何判断节省是否可信

主要成本为所有有效尝试的 `input_tokens + output_tokens` 总和除以验收成功数；失败尝试的成本保留。缓存 input 是 input 的子集，不再次相加；这里检验的是报告 token 总量，不能直接折算为账单或订阅额度节省。

时间成本为模型客户端执行时间与独立评分时间之和除以成功数，包含规则读取、探索、工具调用、局部重试、CLI 内服务等待和测试，排除控制器队列等待、工作目录准备、研究、控制器调试及评测外审查。成功评分按测试进程启动至日志与摘要解析的阶段耗时计量，评分前复制不计；评分故障保留完整失败耗时。每次模型执行上限 600 秒。到时终止后对留存代码评分通过仍可计为验收通过，但不能据此称模型会话自然完成。

每批以完整任务为簇做 20,000 次配对 bootstrap，同题的两组及全部 3 次重复始终在一起；固定种子 20261024，分位数采用 inverted CDF。预设门槛是 skill/baseline 成本比的单侧 97.5% 上界小于 0.90，同时该批 skill 的观测通过率不低于基线。正式与独立复验都要通过同一指标，才宣称稳定至少 10% 收益；两项收益指标各用 alpha .025。小样本 bootstrap 的覆盖率是近似的。

试验的 jackknife 方差规划得到 token 13 题、时间 11 题，按预定下限取每批 20 题。规划假设真实比值 0.80、检验边界 0.90；80% 功效是单批近似值，两批各 80% 的独立联合通过概率约 64%，实际还受质量门槛影响。它不能证明试验具有检测任意微小收益的能力。

## 缺失 usage 与基础设施故障

正式批次的一次基线运行 `mi-1248 / r1` 到达 600 秒上限，代码验收通过，但未返回最终 usage。原 token 记录保留为 null；正式批次的实际 token 比值、点估计和双侧区间均未识别。

预注册允许在完整保守界成立时推断。在正式批次中，skill 组 usage 完整且只有基线缺失，已知基线成本是其实际成本下界；对同样的非负任务重采样权重，计算出的成本比是实际成本比上界。这里仅保证相对于同 draw、同 percentile 完整数据方法的单调保守性，总体覆盖仍为近似。独立数学审查与 12 项合成测试覆盖了该方法；接入发生在正式汇总分析之前。没有把缺失 token 填成零，没有将保守比值上界写成实际点估计，也没有用它重估样本量。若 skill 组也缺失，该方法不能证明节省。

Windows 沙箱依赖读取、嵌套测试目录识别、pytest 临时目录复制故障均有单独记录。初始环境预检 2 次会话、一次评分故障涉及的原始 5 次试验会话完整留存，合计已知 2,903,879 tokens，单列为额外校准开销，不混入 300 次有效比较。另有小型环境 smoke 与研究编排开销；本报告不声称已计量整个研究的全部资源。

后续评分故障使用模型执行结束、评分开始前的检查点恢复，只重做独立评分，保留原模型 token、客户端耗时及失败评分耗时。没有为了改善质量分数重跑失败任务。

复验在 118 条评分记录时，又因临时快照缺失文件而被评分前哈希检查阻断。全量核对发现 4,086 项冻结 overlay 中缺失 150 个原始空文件，其余哈希一致；更广的原始快照也有文件缺失，原因尚未确定。校验 306 份已保存归档后恢复 26,777 个缺失文件，全部冻结验收哈希恢复一致，未覆盖任何现存不同字节。已完成的基线会话仅恢复评分，之后运行唯一尚未启动的 skill 会话。

对此额外核对全部 300 个初始 Git 快照与固定父版本归档：27,054 项逐字节一致，192 项在 Git 的 CRLF 规范化后相同，未发现未解决差异。最后一对的 39 个初始上游文件均逐字节一致。候选工作目录和原始事件未修改；此次恢复没有新增重跑模型成本。临时文件缺失的触发进程仍不明，相关证据保留。

## 冗余与必要错误处理的证据

正式批次后按固定种子随机抽取 8 对补丁，隐藏组名后由独立 Codex 代理审查，揭盲为 skill 较优 1 对、相当 7 对，基线较优 0 对。这是描述性审查，不能证明普遍减少冗余，也不能用行数少直接代表代码更好；探针也不能证明成本变化由减少冗余导致。

较优案例 `mi-1125 / r0` 中，skill 使用 tee 保留惰性消费；基线提前物化输入。评分之后的定向探针确认：`gray_product(..., repeat=0)` 中 skill 与原实现不消费输入，基线会消费；另一个迭代器先产生有效值、随后抛异常时，skill 保留先返回有效前缀再传播异常的时机，基线在首个值之前抛出。这些差异未用于修改冻结评分或成本。

其余案例中，双方通常都保留了有依据的复制、边界检查和局部错误处理。例如 `value_chain` 只围绕取得迭代器捕获标量输入的 TypeError，迭代过程的异常继续传播。现有证据支持“删除防御前检查契约与所有路径”，尚不足以声称 skill 相比当前 Codex 系统性更好。

正式批次的 6 次失败来自同一 Pearson 分类题，两组各 3 次。独立题面复核发现，零分母时应归 Type I / 1 的具体要求未明确写入题面；原分数和成本全部保留，不能简单把该题失败归因为 skill，也不按结果删除它。

复验的 3 次失败来自 Click 段落格式测试，基线 2 次、skill 1 次，差异在空行缩进是否匹配上游精确字符串要求。两批均没有 skill 通过次数更低的任务，也没有同题同重复中基线通过而 skill 失败的配对；这仍只反映当前测试与运行。

## 适用边界

本轮是 Windows 上、显式读取冻结 skill 的 Python 工具库与 CLI 维护对照。任务来自公开 GitHub 历史，无法排除模型预训练接触；它不是随机生产样本，也没有覆盖前端、数据库、分布式重试、鉴权等全部场景。不同任务来自相同项目，外推应保持克制。两批在同一台机器、同一次连续实验中执行；“独立复验”指新任务和新上下文，不是独立平台或跨日期复验，不能证明长期服务负载下的稳定收益。

相同观测通过率和局部错误路径测试不能构成普遍无回归或非劣效证明。额外原生加载 smoke 只检验少量功能路径，不能估计自动触发可靠率或原生工作流提速。Claude 依用户要求跳过；未安装到全局目录，未上传 GitHub 或 agensi。

下一目标：围绕更明确的重复校验、静默 fallback 等场景缩小适用范围，再用全新留出任务检验修改后的增量收益。


## 可审查证据

- [冻结协议](../preregistered-protocol.json)、[冻结 skill](../skill-frozen.md)、[正式分组及其内嵌的原始 pilot 规划](partition-confirmatory.json)。本机时间记录中，分组写入为 2026-10-24 13:30:57.860646 UTC，首个正式客户端开始为 13:30:58.561479 UTC；内嵌规划只有 60 条 pilot 记录。后来控制器恢复会重写外层 sample-plan 的输入计数，计算仍只使用 pilot；不能用后来文件的修改时间替代原分组证据。
- [正式分析](formal-analysis.json)、[正式 token 保守界](formal-token-bound.json)、[复验分析](replication-analysis.json)、[复验 token 状态](replication-token-bound.json)、[机器结论](conclusion.json)。
- [逐次数据及补丁索引](runs.jsonl)、[来源/基线/修复哈希与预筛结果](corpus-manifest.json)、[250 条来源去重账本](source-dedup.json)、[扩充账本](final-extension-manifest.json)。
- [300 次最终一致性核对](final-integrity.json)、[轨迹审查](trace-audit.json)、[标记逐条核对](trace-audit-adjudication.json)、[文件证据索引](evidence-index.json)、[本地原始证据存档清单](evidence-retention.json)。原始事件、提示词、检查点、94 份实际使用的源归档及校准会话另存于本机被 Git 忽略的 `.local/full-validation-evidence.zip`，公开逐次记录保存其哈希。存档含私有全局提示目录，仅供本地留存。
- [运行环境](runtime-environment.json)、[36 项工具检查与 skill 格式检查](harness-validation.json)、[独立报告方法复核](report-method-review.json)。这些是评测工具检查，不是额外模型有效性样本。
- [缺失计量独立审查](missing-usage-bound-method-review.json)、[保守界接入记录](bound-exception-integration.json)、[Pearson 题面复核](pearson-task-contract-audit.json)。
- [匿名审查原结果](blind-audit-findings.json)、[揭盲结果](blind-audit-decoded.json)、[运行探针](blind-audit-runtime-diagnostic.json)、[原生加载 smoke](native-loading-smoke.json)。
- [初始环境修正](infrastructure-amendment.json)、[pilot 整任务重跑说明](pilot-grading-recovery-amendment.json)、[正式评分恢复](formal-grading-checkpoint-recovery.json)、[临时目录复制验证](pytest-temporary-copy-validation.json)。
- [复验评分中断原记录](replication-grading-failure-initial.json)、[恢复前参考核对](reference-integrity-before-repair.json)、[固定归档恢复账本](snapshot-restoration.json)、[300 次初始输入核对](initial-input-integrity.json)、[最后一对的逐文件核对](last-pair-input-integrity.json)。

## 重算数值

下列 `python` 指装有 NumPy 2.3.5 的解释器。在项目根目录执行；这些命令只读公开记录，不调用模型，也不覆盖原分析：

```powershell
python -B evals/full-validation/analyze.py --records evals/full-validation/results/runs.jsonl --phase formal --output .local/replay/formal.json
python -B evals/full-validation/analyze.py --records evals/full-validation/results/runs.jsonl --phase replication --output .local/replay/replication.json
```

保守界可调用 `bound_analysis.analyze_token_bound(records, phase)`；最终一致性核对已从全部 300 条记录重算两批原分析和保守界，并对比原指标。输入总记录数和其他阶段计数可能随汇总时点变化，所选阶段的指标和分组必须一致。

重新执行模型可使用 `preflight.py`、`run_experiment.py`、`validate_all.py`，但它们是本机 Windows 评测工具，解释器路径、独立依赖目录、来源归档和已有认证需要对应环境准备；尚未验证跨平台的一键执行。模型是随机服务，重新运行不保证得到相同代码或精确数值。


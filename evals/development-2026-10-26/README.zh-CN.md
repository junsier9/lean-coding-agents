# 显式参数值回归：开发测试与v5规则

本目录是已暴露失败的开发回归，不是新封存验收，不追加v4确认轮，也不证明提速或生产合格。已结束的v4成绩、数据与发布判定保持原样。

本次已完成：新任务与旧任务各3次，**6/6功能回归通过**，全部CLI正常结束、用量完整。完整正文读取的原自动计数为5/6；独立审查从MCP返回值补证了第6次，原始计数和自动门槛结果不改写，见下方观测裁定。

## 固化的行为

[d10](cases/d10.json)从v4盲审pair05实际产生回归的Click代码开始。运行包来自固定提交，`core.py`使用当时的实际输出；[来源记录](provenance.json)与fixture内的上游许可证保留来源。只把运行包及相关文档作为可见项目，不声称覆盖完整上游pytest套件。

| 情形 | 必须得到的行为 |
|---|---|
| `--opt=--other`、`-o--other` | 显式字符串传入opt，不能激活other |
| 明确附加的负整数或空字符串 | 保留原值及正常类型转换 |
| 裸选项后跟独立的`--other` | opt使用声明的flag_value，other正常启用 |
| 非法整数、缺少必需值、独立未知选项 | 保留原有真实错误，不能回退或悄悄接受 |
| 回调、重复选项、多参数选项、资源上下文 | 保持原有调用、值绑定、异常传播及释放行为 |

[16项行为契约](contract_d10.py)检查公开调用结果、回调和资源行为，不按代码行数、内部实现或字符串模式判定修复。[可见复现](visible_d10.py)提供正常三形式与一个实际反例。另保留[d09接收者兼容任务](cases/d09.json)，防止规则修订后丢失原接收者关键字或每实例缓存契约。

独立功能控制实现未读取候选规则或本目录的契约测试，仅修改`parser.py`；随后由本目录测试检验。离线结果见[预检](results/preflight.json)和[独立控制记录](results/reference-control.json)：

- 正确参考实现：d10普通及`-O`均16项通过；d09均15项通过。
- 实际回归版本被拒绝。
- 恢复旧构造逻辑、全局吞并前缀token这两种错误处理也被拒绝。

unittest的一个方法可含多个subTest，因此失败事件数可能超过方法数。不能用失败事件数直接推算失败方法数或独立案例数。

## 规则修订

[v5候选](candidates/v5-python/SKILL.md)仍为四条规则，377词，运行入口仍只有两文件，描述及UI元数据不变。

1. 修改共享状态时，检查生产者、调用者及消费该状态的代码。
2. 显式输入与省略必须分开；fallback不能把已经绑定的数据重新解释为控制语法，同时保留公开错误、正常可选结果及原接收者调用资格。
3. 在受影响的调用形式里检查“看起来像控制语法的显式数据”；保留原绑定/类调用检查，不增加通用检查轮次。

这里是对现有第一、第二、第四条的窄范围修改，未新增第五条规则、安装依赖或验证框架到skill中。

## 不调用模型的复跑入口

在仓库根目录，用Python3.12运行：

```powershell
python evals/development-2026-10-26/check_contract.py --case d10 --variant reference
python evals/development-2026-10-26/check_contract.py --case d10 --variant initial
python evals/development-2026-10-26/check_contract.py --case d10 --variant original_constructor
python evals/development-2026-10-26/check_contract.py --case d10 --variant consume_all_prefix_tokens
python evals/development-2026-10-26/check_contract.py --case d09 --variant reference
```

参考实现应返回0，三个d10错误版本应返回1；需要完整输出时加`--verbose`。每次使用新的临时工作区，不覆盖已记录的结果，不调用Codex。入口的[实际复测记录](results/offline-checker-validation.json)包含普通和`-O`结果。

## 原生开发回归

计划固定为两个任务各3次，共6次，继续使用已记录的Codex模型/CLI配置与Python3.12。通过要求为全部契约通过、实际读取完整skill正文、CLI正常结束且不超时。原始事件、失败成本和输出保留。

[iteration2冻结记录](results/candidate-freeze-iteration2.json)生成于模型调用前。iteration1只做准备，模型调用为0；之后在首次执行前恢复了公开错误与可选结果的显式措辞，原草稿快照保留，见[修订记录](results/pre-native-editorial-correction.json)。这不是一次失败模型运行的重试。

执行结果见[原始汇总](results/development-iteration2.json)与[逐次记录](results/runs-iteration2.jsonl)：d10为3/3、d09为3/3，普通及`-O`契约全部通过；六次CLI正常结束、完整用量6/6。

原正文检测器只收集命令执行输出，因此自动汇总保留5/6、自动门槛false。[独立轨迹审查](results/trace-independent-review.json)确认d09 r2的MCP `item_4`实际输出完整且逐字节相同的skill，补齐证据后[观测裁定](results/body-observation-adjudication.json)为6/6，并满足原开发门槛。没有重跑模型或改动评分、测试和规则。16次Node调用均经审阅；Git被拒绝的外部全局ignore查询如实记录，不声称完整主机访问隔离。原始工具计数也不能用于跨工具类型的效率比较。

[六份源码修改审阅](results/source-review.json)确认：三个d10输出只改既有解析器；三个d09输出只改既有缓存封装。显式来源参数、参数规范化和缓存类型区分有当前契约依据；未按行数或测试数量选优。

这些结果只说明已知开发任务通过，不能称为相对v4的因果改进、独立泛化证据、稳定收益或生产资格。

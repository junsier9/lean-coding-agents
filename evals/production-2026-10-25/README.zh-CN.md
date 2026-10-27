# Python 生产维护版优化

范围经用户选择固定为 Codex + Python。v3已结束且未合格；v4的303次专项尝试也已完成，未达到生产验收。正式批候选通过率96.7%、复验88.3%，稳定收益及盲审门槛均未通过；详见[v4完整结果](results/v4/summary.zh-CN.md)。旧版及300次结果原样保留。

v4[正式批次120次](results/v4/formal-analysis.json)已完成：候选58/60（96.7%）、基线57/60，无任务成功次数下降，本批质量门槛通过。耗时成本比为1.002，单侧98.75%上界1.149，尚无可信正向收益；一次已接受补丁的客户端运行超时，原始事件没有完整用量，故精确token比较不可用。[复验120次](results/v4/replication-analysis.json)两组均53/60（88.3%），未达到95%；耗时点估计改善7.8%，但不能单选本批证明稳定收益。[最终用量限制](results/v4/usage-limitations.json)保留正式1次与复验14次缺失，不补零、不删除尝试、不重复模型调用。归档核验与最终门槛状态分别见[证据核验](results/v4/evidence-integrity.json)及[发布判定](results/v4/release-qualification.json)。

预选复验盲审 [pair05](results/v4/blind-reviews/pair05.json)发现 A/B 两侧共有的确认回归：明确附加的 `--opt=--other` 被重新解释为另一个选项，回调由原来的 `('--other', False)` 变为 `('flag', True)`；明确附加的负整数也会报 NoSuchOption。父审阅者以两个字符串写法[独立复现](results/v4/blind-reviews/pair05-parent-probe.json)，并再次核对436个输入文件字节未变。[裁定](results/v4/blind-pair05-adjudication.json)使该配对不满足生产盲审门槛；不改变冻结评分，不声称安全漏洞，也不将有歧义的分离写法另算缺陷。该裁定形成时其余审查尚未完成；全部8组报告定稿后才读取分组标签。

[8组独立审查已全部完成并揭盲汇总](results/v4/blind-review-summary.json)：原始报告与2030个输入文件哈希再次核对一致，[完成控制记录](results/v4/blind-review-completion-control.json)先于读取分组生成。7组未发现确认缺陷，pair05在基线与候选各有同一项确认回归，无一侧偏好。第3组双重无效输入的异常优先级、第6组Decimal NaN边界政策及第8组空环境值政策范围均单独记录，未凭未明示的要求另造缺陷。盲审门槛未通过；原定真实复验亦已结束，评分与样本均未因此调整。

v3的343次专项调用已全部完成并核对2744个原始记录文件，两批统计从公开数据完整复算一致。[完整结果](results/summary.zh-CN.md)与[发布判定](results/v3/release-qualification.json)均为不合格：真实通过率90.0%和88.3%低于95%。

v4三轮开发回归依次为3/6、5/6、[6/6](results/v4/development-iteration3.json)。[359词候选](candidates/v4-python/SKILL.md)明确区分内部代理与被包装方法接收者，并检查原参数名的关键字类调用；[36次已知关键回归](results/v4/safety-iteration3.json)和[9次加载检查](results/v4/routing-iteration3.json)全部通过后，[候选已冻结](results/v4/candidate-freeze.json)并进入新40题。[修订依据](results/v4/development-revision3.json)保留旧失败与用量，不改原任务、评分或门槛。[rc.3候选包清单](results/v4/package-candidate.json)记录了实际两文件解压、回滚与重新安装校验，仍未获得生产资格。

发布判定实现另有一次中性修复：原事前固定代码在[43个隔离合成场景](results/v4/qualification-review-original.json)中，错误接受了12种矛盾或不完整证据组合。修复后直接核对当前原始行、冻结统计重算、实际归档和ZIP条目，并绑定盲态、候选与阶段。[独立复测60场景](results/v4/qualification-fix-review.json)为3个有效正例通过、57个反例拒绝。该代码修复发生在真实对照开始后，原实现、原事前控制及真实时间均保留于[修复账本](results/v4/qualification-code-amendment.json)；skill、模型、任务、评分、统计与门槛全部未变，未倒填事前时间。接口复测的原生证据组件使用可观察替身，最终发布判断将实际调用原始证据核验；这些合成场景不计入真实skill效果样本。

首轮v3候选为306词，触发范围限定到明确的Python编辑请求。正文优先判断校验是否覆盖所有到达路径、变异后是否仍可信、错误是否被伪装为成功、以及新增层是否有当前依据。运行入口仍只有规则与UI元数据，不把评测框架装进skill。

v4的一次Windows临时目录复制失败已从完成的客户端检查点[恢复评分](results/v4/grading-recovery.json)，未重复模型调用，原失败耗时保留。续跑另暴露了汇总时间戳导致冻结哈希不一致的问题；[修复记录](results/v4/resume-metadata-amendment.json)保留失败日志与新旧汇总，进程内包装器在重新计算门槛后，仅当除完成时间外全部字段一致时保留原汇总字节。[独立11场景检查](results/v4/resume-metadata-review.json)通过，冻结控制器、候选、评分、统计和门槛均未修改。

[协议](protocol.json)在新模型运行前固定：开发案例与封存验收分离；关键契约必须全部通过；另从127个未执行真实案例中分出两批新题。真实对照每批20题、各3次重复，保留失败成本、使用原生仓库skill加载。为防止反复改版直到碰巧显著，最多两轮确认的两项指标共同分配alpha预算，每项单侧置信度为98.75%，稳定至少10%的声明仍需两批都通过。

生产辅助可用性与至少10%的效率声明分别验收。前者要求关键错误边界、真实维护质量、加载、独立审查、可复核的版本安装，以及两批在同一成本指标上有可信正向收益（原98.75%上界<1.00）；至少10%的声明仍要求两批上界<0.90。[追加发布要求](results/release-requirements.json)在新真实任务执行数为0时冻结，不改变样本、评分、统计方法或10%门槛。两个嵌套判断使用同一置信上界。两者都只覆盖已验证配置与场景，不能由少量测试推导任意生产代码永不出错。

方法参考[OpenAI官方skill评测说明](https://developers.openai.com/blog/eval-skills)：分别观察任务结果、执行过程和效率，而不是只看一次最终通过。公开说明的[skill构建建议](https://learn.chatgpt.com/docs/build-skills)也强调聚焦一项任务并测试触发边界。

- [306 词候选与四条规则](candidates/v3-python-v2/SKILL.md)、[决策与正反任务矩阵](contract-matrix.zh-CN.md)
- [候选冻结记录](results/candidate-v3-python-v2-freeze.json)、[元数据修订账本](results/candidate-lineage-v2.json)、[实际加载的两文件核对](results/bundle-alignment.json)
- [关键错误处理：72 次原生运行全部通过](results/safety-v3-summary.json)、[最终自动选择检查：9/9](results/routing-v3-python-v2.json)
- [开发评分器纠偏：保留原 15/16 与无模型重跑的 16/16](results/development-oracle-adjudication.json)
- [预先选择的真实任务](results/real-selection.json)、[预先选择的盲审配对](results/blind-review-plan.json)
- [8组独立盲审与揭盲汇总](results/blind-review-summary.json)：7组等效，1组skill更贴合已有输入范围；2013个审阅文件哈希再次核对一致
- [40 题独立契约复核](results/real-task-contract-review.json)、[备用题的结果盲态预审规划](results/reserve-screening-plan.json)
- [安装、使用范围与回滚](../../docs/production-use.zh-CN.md)

独立审查者未读取模型成绩或候选，对 40 题给出 32 个 adequate、7 个 underspecified 和 1 个 inconsistent。后两类包括题面没有给出的新异常文案或内部表示要求。主分析保留全部原题、原评分与成本；该审查只解释限制，不重新计分。备用题的预审也不改变当前实验，任何第二轮确认仍占用原定的剩余额度。

统计重算使用本目录冻结的 `assess.py` 和公开逐次数据，依赖 NumPy，不调用模型。`replay_analysis.py` 保留原控制器的输入范围：正式分析当时只有正式行；复验分析当时含两批行。这样也能原样核对输入审计计数。两批完成后分别运行：

```powershell
python evals/production-2026-10-25/replay_analysis.py --records evals/production-2026-10-25/results/runs-real-v3.jsonl --phase formal --output formal-recomputed.json
python evals/production-2026-10-25/replay_analysis.py --records evals/production-2026-10-25/results/runs-real-v3.jsonl --phase replication --output replication-recomputed.json
```

原生运行复用已冻结的本机 CLI 与解释器配置，需要原始案例准备步骤；跨平台的一键模型复跑尚未验证。单独重算统计不等于重新运行模型。公开记录中的机器目录会换成具名占位符，补丁保留实际字节；完整提示词、事件和检查点保存在本机被 Git 忽略的证据备份中。

第一轮正式批基线通过53/60、候选通过54/60（90%），未达到预先要求的至少95%。token每成功任务成本点估计减少16.6%，但单侧98.75%上界对应的收益下界为7.3%，仍未证明至少10%；耗时区间也未证明正向收益。第二批完整执行后，候选通过53/60（88.3%）；原分数、样本与门槛均未改动。详见[两批完整结果](results/summary.zh-CN.md)。

第二轮仍属于原定的两轮确认上限。[40道新题](results/second-corpus-selection.json)在候选改写与执行前，按既定备用题顺序选取独立契约预审为充分的题目。v4草稿仅针对已证实的兼容性缺陷加强公开签名、调用方式、对象协议和元数据的保留；先运行开发回归、已知关键契约和加载检查，才冻结候选并接触新40题的模型结果。已知回归不能称为新封存测试，开发成绩也不混入真实效率样本。

两轮、两项指标的误报预算用于“同一指标在两批共同达标”的复合声明。两批取交集，不需要再次分摊该复合检验的预算；每批的各个区间不因此获得同时95%覆盖保证。按任务整簇重采样仍为近似推断，20题的区间与公开项目题集不能证明所有工业项目均有收益。独立[方法审查](results/method-review.json)保留了这些限制及评分器恢复过程的审计问题。

v4加入[资源停止控制](results/resource-stop-control.json)：只有观察到实际服务限额才停止新的原生尝试，已完成的客户端检查点仍可评分。不购置额度、不重置额度、不更换模型。此控制在新一轮模型调用前记录，原冻结评分与统计代码保持原样。

# 行为验证

本文记录首轮成组试验。追加的独立 Codex 会话验证见 [codex-tests.zh-CN.md](codex-tests.zh-CN.md)，实际结果与 prototype-2 修订见 [追加报告](results/2026-10-24-codex/summary.zh-CN.md)。

## 设计

- 12 个合成任务，组成冗余删除与必要防御的对应场景；23 个测试方法，包含多组输入子例。
- 固定同一组初始文件，生成 baseline 与 with-skill 两个隔离目录。
- 两个独立执行 agent 使用相同的继承模型；不给执行 agent 预期改法、评分器或研究结论。
- baseline 不加载本项目 skill；with-skill 显式读取冻结的 SKILL.md。两组都可自写本地检查。
- 两组按 t01 到 t12 的相同顺序完成；每组任务共享一次会话。因此本轮是成组探索性对照，不是每任务独立随机试验。
- 执行结束后，父 agent 运行未提供给执行 agent 的契约检查，并审查 diff、测试范围与新增文件。
- 检查器先在未修改的种子上运行，确认能检出预置缺陷和未完成需求。
- 此轮执行隔离、停用网络与再次委派是实验控制，两组相同，不是安装 skill 后的通用限制。

独立执行依据 skill-creator 的 Independent Forward-Testing 工作流：用现实请求和最少原始材料验证新 skill 对边界判断的影响。这里只在临时目录中处理标准库合成项目。

## 重跑

在项目根目录运行，选择一个尚不存在的输出目录：

~~~powershell
python evals/benchmark.py prepare --root C:\Temp\lean-coding-run-new
~~~

分别给执行 agent 对应目录的 tasks.json 与如下请求；只有实验组多一句显式加载本项目 skill：

> 逐个完成 tasks.json 中的独立开发请求，读取各项目 TASK.md 和源文件，直接修改并运行有意义的本地检查。编辑范围限于所分配的目录。不要查看另一组、研究材料或外部评分器。完成后在 session.md 记录改动、实际检查与未完成项。

两组完成后再运行：

~~~powershell
python evals/benchmark.py score --root C:\Temp\lean-coding-run-new --arm baseline --output evals/results/new/baseline.json
python evals/benchmark.py score --root C:\Temp\lean-coding-run-new --arm with-skill --output evals/results/new/with-skill.json
~~~

score 同时输出 JSON 与可审查的 app.py diff。exit 1 表示至少一个契约失败；测试通过不代表复杂度已经最优。其他文件的增加或改动还需人工审查，不能只看 app.py 行数。

完整任务与正反判据见 [tasks.zh-CN.md](tasks.zh-CN.md)。它和评分器在执行完成前不提供给执行 agent。

## 报告边界

本轮没有 Claude CLI 原生执行，不能声称已经验证 Claude 的行为或自动触发。原始 token 与完整工具调用轨迹未由协作接口回传，不以 agent 自报估算效率提升。两组一次成组执行也不足以支持显著性或生产提速结论。

后续只在真实失败、未覆盖风险或明确的对照问题需要时补测，避免为这个反过度工程 skill 制造无限验证流程。


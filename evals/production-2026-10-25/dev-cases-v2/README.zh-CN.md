# 开发案例 oracle fidelity 修正

本目录派生自冻结的 `../dev-cases/`，旧目录未写入。仅修复 `d06.json` 的隐藏 `Tracked` 代理缺失 SQLite 上下文协议的问题，8 题的任务、初始源码、参考解和检查要求均不变；另外 7 题逐字节复制。原有 12 个 d06 测试方法及所有断言完整保留，原评分与任何模型产物均未修改。

`__enter__` 检查真实连接并返回代理，不自动开始事务。`__exit__` 仅在连接存在活动事务时提交或回滚，提交经过原 `commit`，因此保留 `fail_commit` 故障注入。提交失败后回滚并重新抛出原异常；包括 KeyboardInterrupt 在内的异常退出会回滚并传播。上下文不会关闭连接，应用仍需释放资源。

`isolation_level=None` 是 autocommit。仅使用 `with connection` 不会创建事务，也不能使块内多条 SQL 自动原子化；探针明确验证这一点，修复未向候选代码提供额外的事务行为。

捆绑 Python 3.12.14 上实际验证：

- 普通与 `-O` 模式各运行原 8 题：初始版本仍全部拒绝，参考版本各 95 项测试全通过。
- `fidelity_probe.py` 从新隐藏测试 AST 加载实际 Tracked，分别比较原生 SQLite 与代理的成功、无事务、autocommit 写入后异常、RuntimeError、KeyboardInterrupt、延迟外键导致的提交失败、authorizer 拒绝提交、关闭后进入、块内关闭等行为。
- 额外核对 `fail_commit=True` 在显式 commit 与上下文退出两个路径均有效，并检查原提交异常身份、回滚及关闭计数。普通与 `-O` 模式各 12 项 fidelity 测试全通过。

复现全部修复与检查：

```powershell
python -B evals/production-2026-10-25/dev-cases-v2/repair_oracle.py
```

结果在 `preflight.json`、`fidelity.json`、`fidelity-optimized.json`；旧/新 SHA、变化范围与校验信息在 `oracle-repair.json`。本次只执行本地标准库检查，未运行模型、读取其他模型结果或接触封存留出集。此修正本身不是 skill 有效性证据。

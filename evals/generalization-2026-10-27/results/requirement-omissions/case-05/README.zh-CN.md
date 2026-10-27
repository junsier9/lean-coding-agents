# case-05：合法嵌套枚举默认值的需求遗漏

这是固定 r0 产物独立审查后增加的诊断用例，未用于本轮候选修订、原始评分或补跑。它确认的是**原需求未修完整**，不是候选新增的回归。

原需求要求 Enum、IntEnum、Flag 等枚举构成的 Choice 默认值，按用户可识别的选择名称展示。既有 API 明确支持 `multiple=True`、`nargs>1` 时的嵌套默认值；但候选仅转换第一层元素中的枚举。

最小例子为 `multiple=True, nargs=2, default=((Color.Red, Color.Blue),)`。默认解析与显式 `--color Red Blue` 在两版中都成功；帮助中的可选值为 `[Red|Blue]`，默认值却仍为 `(<Color.Red: 1>, <Color.Blue: 2>)`。父代理在原审查输入上另行复现相同结果。

- `test_nested_enum_default.py`：四类枚举 × 两种大小写设置，共 8 例；审查中原版和候选均失败。断言只要求使用选择名称，不限定括号或分组格式。
- `parent_probe.py`：父代理最小复现，依次接收待检查版本的 `src` 和冻结 `testdeps` 目录。
- `parent-confirmation.json`：父代理实际结果与探针 SHA256。

运行八例测试时，将选定版本的 `src` 与冻结 `testdeps` 放入该进程的 `PYTHONPATH`，禁用额外 pytest 插件自动加载，再执行 `python -B -m pytest -p pytest_timeout --timeout=30 -q test_nested_enum_default.py`。原版、候选完整源码和审查执行日志保留在本轮私有归档。

独立审查将此项记为 P2。它计入零确认缺陷门槛；没有无 skill 对照，不能据此推断 skill 的因果作用。

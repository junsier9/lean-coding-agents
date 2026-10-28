# lean-coding

[![Status: Experimental beta](https://img.shields.io/badge/Status-Experimental_beta-orange)](releases/0.7.1-beta.1/README.zh-CN.md)
[![Scope: Codex + Python](https://img.shields.io/badge/Scope-Codex_%2B_Python-blue)](#what-it-does)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

用于既有 Python 代码维护的 Codex skill。用 4 条决策规则减少没有当前依据的校验、包装、复制、重试与兜底，同时保留真实输入边界、错误传播、调用兼容、取消、事务与资源清理。

当前发布包为 **0.7.1-beta.1**，对应 v7.1 规则。它是免费实验测试版：已通过已知回归开发验证，尚未通过新题泛化与完整质量验收，未证明稳定提速或 token 节省，Claude 尚未验证。

**Agensi：已提交审核，尚未上线。** 可先从本仓库[下载 ZIP](dist/lean-coding-0.7.1-beta.1.zip)或安装发布目录；后续完善以新版本和更新日志提供。

> **适用人群**：使用 Codex 修改既有 Python 项目，希望减少多余代码，又需要保留原有行为与错误处理的开发者。

## Coding Stance

`$lean-coding` 将修改约束在当前需求和实际契约内：

- 沿生产者、调用者、消费者及现有覆写路径确认保证。
- 保留公开行为、合法输入协议和失败语义。
- 每层包装、检查、复制或重试都要有现实依据。
- 完成必要检查后结束；失败后调整方法。

规则主体保持简短。项目专属测试命令和环境约束仍由项目自己的说明与工具提供。

## What It Changes

下表来自已经揭露并参与规则修订的开发问题，**没有无 skill 对照，不能用于计算 skill 的因果收益**。

| 已观察到的问题 | 当前规则关注的决策 | 已有证据 |
| --- | --- | --- |
| 合法参数值长得像 `--flag`，被误当成新选项 | 使用既有解析状态区分数据与控制语法 | 多值补全及兼容契约通过 |
| 分别测试通过，组合后的嵌套枚举默认值显示仍出错 | 跟踪已有 API 组合改变的值形状 | 43 项契约在普通与 `-O` 模式通过 |
| 强制读取新增元数据，让原本可用的自定义 parser 失败 | 可选观测字段保持可选，覆盖现有覆写路径 | 原始兼容探针失败可复现，v7.1 产物恢复正常 |

最新开发窗口共 **6/6 次原生 Codex 端到端通过**；两个重点产物经过知晓参考答案的开发审查，无确认缺陷。每题仅运行一次，不能据此估计真实项目成功率。[完整报告](evals/development-2026-10-28-v7-1/README.zh-CN.md)

## What It Does

在请求修改既有 Python 代码，且涉及以下内容时使用：

- 重复校验、异常处理和兜底值。
- 防御性复制、重试与多余包装。
- 简化代码时需要保留调用方式、对象协议和错误契约。

只读解释、只读审查、纯文档任务和非 Python 工作不在当前触发范围内。需要读写项目文件，并使用项目已有的 Python 与测试工具；没有额外 MCP 服务、付费 API 或第三方依赖。

## Package Contents

当前可安装的完整目录：

```text
releases/0.7.1-beta.1/lean-coding/
  SKILL.md
  agents/openai.yaml
  LICENSE
```

包内不含评测源码、原始会话日志或本机路径。规则与 UI 元数据逐字节对应已测试的 v7.1 候选。[发布清单与 ZIP 摘要](releases/0.7.1-beta.1/manifest.json)

`skills/lean-coding/` 保留早期 prototype-2，供历史复跑。安装当前版本时，请使用上面的发布目录。

## Install

### Codex installer

在 Codex 中发送：

```text
使用 $skill-installer 从 https://github.com/junsier9/lean-coding-agents 安装 releases/0.7.1-beta.1/lean-coding 目录。
```

### Manual installation

克隆或下载本仓库，将完整的 `releases/0.7.1-beta.1/lean-coding/` 文件夹复制到一个目标位置：

| 范围 | 目标目录 |
| --- | --- |
| 当前项目 | `<项目根目录>/.agents/skills/lean-coding/` |
| 个人本地项目 | `~/.agents/skills/lean-coding/` |

保留 `agents/openai.yaml` 和 `LICENSE`。Windows 中 `~` 表示用户主目录；WSL 使用其 Linux 用户目录。已有同名安装时，先备份到技能发现目录之外，再替换，避免目录嵌套和旧文件残留。

在目标项目中用 `/skills` 查找 `lean-coding`，再显式调用 `$lean-coding`。若未出现，重启 Codex 后检查。文件复制成功不代表当前会话已经加载。路径与发现方式见 [Codex 官方说明](https://learn.chatgpt.com/docs/build-skills)。

直接从 GitHub 安装采用 MIT 许可，无需 Agensi 账号。Agensi 提交状态见[发布记录](releases/0.7.1-beta.1/README.zh-CN.md)。本仓库不会自动修改你的全局 skill 目录。

## Use It

```text
使用 $lean-coding 修复这个 Python 问题。保留现有公开接口和异常行为，复用已有保证，完成项目要求的检查。
```

```text
使用 $lean-coding 简化这段重复校验。检查所有到达路径和现有覆写，保留仍然需要的边界处理。
```

```text
使用 $lean-coding 修改这个重试逻辑。根据当前失败与操作是否可安全重复决定处理方式，沿用已有重试边界。
```

skill 提供决策指导；运行权限、项目约束与代码验收继续由实际环境、项目说明和测试负责。

## Validate

在仓库根目录运行，使用已有 Python 3.10+：

```powershell
python releases/0.7.1-beta.1/build.py
```

macOS / Linux 可使用 `python3`。命令核对候选字节与已登记的开发证据、检查发布目录文件集，并在 `dist/` 生成可复现 ZIP，检查 CRC、清单和解包后字节。它不启动模型评测，也不安装 skill。

构建成功只证明发布包完整性。行为验证、保留的失败与限制分别见[开发报告](evals/development-2026-10-28-v7-1/README.zh-CN.md)和[历史研究](RESEARCH_HISTORY.zh-CN.md)。

## Updates

后续版本保留版本目录、发布清单和变更说明。更新本地副本时，先获取仓库新版本，再备份并替换已安装的完整目录；只更新源码仓库不会更新已经复制的安装。

Agensi 支持上传新 ZIP 和更新日志；更新审核期间旧版本继续提供服务，获批后加载最新版本。[Agensi 更新说明](https://www.agensi.io/docs/updates)

## Limits And Known Gaps

- v7.1 目前只有 6 道已知开发任务，每题 1 次；尚无未参与修订的新题结果。
- 两个重点产物的开发审查知晓参考答案，不能称为盲审。
- 没有 v7.1 无 skill 对照，未证明稳定提速、token 节省或减少人工返工。
- Python 项目、平台与依赖版本覆盖有限；Claude 尚未验证。
- 原 v7 开发审查确认 1 个兼容回归，原窗口保持失败；此前 v6 泛化审查确认 2 个 P2，质量门槛也保持失败。
- 本次实验测试版发布不改变上述评分，不代表通过原定测试版质量门槛或取得工业级生产资格。

## Research And License

- [论坛与 GitHub 案例来源](research/2026-10-24-findings.zh-CN.md)
- [历史验证、失败门槛与复核入口](RESEARCH_HISTORY.zh-CN.md)
- [旧冻结提交的可复核历史包](research/README-history.zh-CN.md)
- 原创 skill 与发布包采用 [MIT](LICENSE)；仓库中的上游评测材料保留[各自许可](THIRD_PARTY_NOTICES.md)。

欢迎在 [GitHub Issues](https://github.com/junsier9/lean-coding-agents/issues) 提交可复现问题：任务、最小代码、预期与实际行为、Codex/模型版本及使用的 skill 版本。提交前移除密钥和私有项目数据。

中文搜索词：减少过度防御性编程、Python 代码维护、Codex skill、错误处理、冗余代码。

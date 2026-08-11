# JARVIS Repository Instructions

## 版本封档

当用户明确表示某个版本人工验收通过或允许封版时，Codex 必须在同一任务中：

1. 更新 `docs/milestones.md`，记录实现功能、实际问题、根因、最终改进、验证结果、已知限制及 `Manual Acceptance: PASS · SEALED`。
2. 更新 `CHANGELOG.md` 的对应版本摘要。
3. 保留已经封版的架构语义；后续行为变化使用新的版本条目，不静默重写历史。
4. 封档前检查 Git diff，并确认没有 `.env`、API Key、`memory.db`、私人 Memory 或未脱敏日志进入版本记录。
5. 在自动检查和人工验收均通过后，将该版本的代码与封档文档提交到当前 `main`，推送至已配置的 `origin`，并核对远程分支提交与本地一致。
6. 如果 GitHub 登录、提交或推送失败，必须明确报告实际状态，不得声称已经同步。未经用户明确要求，不自动创建 Tag、GitHub Release 或改变仓库可见性。

## 风险分级验证

从 v0.5D 之后的变更开始，验证强度必须与风险成比例：

- 低风险：聚焦测试（如适用）、lint/format、diff 与敏感信息检查；默认不使用多轮独立对抗审查。
- 中风险：定向 TDD、受影响回归和一次独立审查。
- 高风险（认证/Secret、持久化/schema、副作用、权限、文件/系统执行、Provider/协议、并发/取消或关键跨边界数据流）：完整相关测试矩阵、跨栈回归、专项安全/规格审查和必要的真实人工验收。

新证据可以提升风险等级；明确的用户计划、CI、本文件和 sealed ADR 的硬性要求不得降低。禁止让低风险功能默认复制 v0.5D 的最高强度多轮审查流程。

任何副作用成功声明都必须遵守：

```text
Side-effect success claims MUST originate
from verified executor results.
```

# JARVIS Repository Instructions

## 版本封档

当用户明确表示某个版本人工验收通过或允许封版时，Codex 必须在同一任务中：

1. 更新 `docs/milestones.md`，记录实现功能、实际问题、根因、最终改进、验证结果、已知限制及 `Manual Acceptance: PASS · SEALED`。
2. 更新 `CHANGELOG.md` 的对应版本摘要。
3. 保留已经封版的架构语义；后续行为变化使用新的版本条目，不静默重写历史。
4. 封档前检查 Git diff，并确认没有 `.env`、API Key、`memory.db`、私人 Memory 或未脱敏日志进入版本记录。
5. 在自动检查和人工验收均通过后，将该版本的代码与封档文档提交到当前 `main`，推送至已配置的 `origin`，并核对远程分支提交与本地一致。
6. 如果 GitHub 登录、提交或推送失败，必须明确报告实际状态，不得声称已经同步。未经用户明确要求，不自动创建 Tag、GitHub Release 或改变仓库可见性。

任何副作用成功声明都必须遵守：

```text
Side-effect success claims MUST originate
from verified executor results.
```

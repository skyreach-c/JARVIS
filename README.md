# JARVIS

JARVIS 是一个由 Tauri/React 桌面端和 Python Core 组成的本机个人 AI 助手。当前已封版基线为 v0.4C.2，具备 DeepSeek 流式聊天、Personality、进程内 Session Context、SQLite Pinned Long-term Memory，以及具有副作用安全边界的 Natural Memory Router。

## 开发命令

```powershell
.\scripts\bootstrap.ps1
.\scripts\dev.ps1
```

`dev.ps1` 是开发阶段的唯一启动入口。它只启动 Tauri；Python Core 由 Tauri 自动启动和停止。

首次使用前，在项目根目录 `.env` 的 `DEEPSEEK_API_KEY=` 后填写本地 Key。不要把 `.env` 提交到 Git。

运行全部自动检查：

```powershell
.\scripts\check.ps1
```

文档入口：

- 版本、问题、改进与验收总账：[`docs/milestones.md`](docs/milestones.md)
- 用户可见变化：[`CHANGELOG.md`](CHANGELOG.md)
- 架构决策：[`docs/decisions.md`](docs/decisions.md)
- 初始协议与架构：[`docs/architecture.md`](docs/architecture.md)

## 版本记录

每个里程碑在人工验收后都会更新版本总账和 Changelog。已封版版本的详细人工验收场景、已知限制和安全边界以 `docs/milestones.md` 为准。

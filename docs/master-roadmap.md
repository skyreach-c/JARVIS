# JARVIS Master Roadmap

## Mission

JARVIS 的长期目标是构建一个 single-user personal AI runtime：它具有统一的 Identity / Personality、可靠的 Memory、可替换的 Cognitive Providers、Agent Runtime、Task Runtime、Tools / Skills，以及明确的 Permission / Approval / Verification 边界。

在此基础上，JARVIS 将逐步获得观察电脑现实、调用 Codex 等 Specialist Agent、控制 Windows、Voice、Vision / Computer Use 和 bounded proactivity 的能力，并最终通过 ROS2 接入 Camera、Mechanical Arm 与 Robot。

长期架构不绑定某一家模型或 Provider。核心关系固定为：

```text
Model    = intelligence
Core     = governance
Tool     = capability
Executor = reality
Memory   = continuity
```

永久安全不变量：

```text
Side-effect success claims MUST originate
from verified executor results.
```

## Long-term Architecture

```text
User
  ↓
Request Router (minimal routing context only)
  ├─ DIRECT ─────→ Identity / Personality + relevant context
  ├─ CONTROLLED ─→ Core-owned bounded capability flow
  └─ AGENT
       ↓
    Identity / Personality + Task Context
       ↓
    Agent Brain
       ↓
    Relevant Skills
       ↓
    Relevant Tools
       ↓
 Permission / Approval
       ↓
     Executor
       ↓
 Verified Observation
       ↓
  Final Response
```

- `DIRECT`：确定性本地路径或无需 Tool 的普通响应，不额外调用 Agent Brain。
- `CONTROLLED`：未来由 Core 拥有流程和边界的有界单能力路径；当前尚未实现该三路 Router。
- `AGENT`：在 Task、预算和权限约束下由模型作出有限决策。
- 当前 Memory terminal routing 继续优先于通用 Router；未来 Router 只接收完成路由所需的最小上下文，不默认读取完整 Memory、Task 或 Personality。
- 任何需要 Tool 的路径都不能绕过 ToolRegistry 和 Executor；未来副作用路径还必须经过 Permission 与 verified receipt。既有 deterministic Memory command 继续使用其已封版的 MemoryStore/Core 验证边界，不被强行改写为 Tool。

外部能力通过 Adapter 接入，可能包括 Codex、MCP、Windows、Browser、Voice、Vision、ROS2 与 Robot。Core 内部协议保持 provider-neutral 和 tool-neutral。

## Permanent Engineering Principles

1. Reality > Model。
2. 每个重要状态只有一个 authoritative owner。
3. 能 deterministic 完成的工作不交给 Agent。
4. Observe → Think → Act → Verify。
5. Tool 是原子能力，Skill 是组合方法；二者都不能绕过 Core governance。
6. 新增现实世界副作用能力必须经过 Permission、Executor 与可验证 Receipt；尚未引入 PermissionPolicy 的 sealed 只读或 Memory 路径保持各自现有边界。
7. Memory 不追求把所有内容无限永久保存。
8. Local-first，但不是 Local-only；发送到云端的数据边界必须明确。
9. 内部契约保持稳定，外部尽量兼容 MCP、ACP、Codex、ROS2 等标准。
10. 不为展示能力而增加不必要的复杂度。

明确禁止：

- 所有请求都永久经过 Agent Brain。
- 万能 Shell Tool 或无边界磁盘访问。
- 自动 merge Codex 改动。
- 无限自动长期 Memory。
- LLM 直接控制机器人底层执行器。
- 为了多 Agent 而多 Agent。
- 自动修改 production code 实现所谓“自我进化”。

## Sealed Foundations

- **v0.4C family — Reliable Memory + Safety · SEALED**
- **v0.5A — Multi-Model Provider Foundation · SEALED**
- **v0.5B — Agent Runtime Foundation · SEALED**
- **v0.5C — Capability Runtime Foundation · SEALED**

封版版本的当前行为以 `docs/decisions.md` 和 `docs/milestones.md` 为准。本 Roadmap 描述未来方向，不反向改写 sealed semantics。

## Next Milestone

### v0.5D — Workspace Knowledge

让 JARVIS 从“知道项目中的文件存在”升级为“在明确边界内安全理解单个项目文本文件”。

重点：

- 在现有单次 AgentRuntime 中注册只读 `filesystem.read_text`
- project-root only、text-only
- size / encoding / context budget
- secret protection
- prompt-injection boundary
- Tool observation telemetry

v0.5D 仍保持一次 Brain 决策、最多一次 Tool Call。它不引入三路 Router、Task Runtime 或通用 PermissionPolicy，也不实现搜索、RAG、项目索引、多文件读取、写入、删除、命令执行或任意磁盘访问。Relevant retrieval 和 Tool relevance filtering 只保留为后续演进点。

## Later Milestones

### v0.5E — Task Runtime & Governance

- `task_id` 与 `request_id` 分离
- Task Context、progress、cancellation、timeout 和 Tool budget
- PermissionPolicy、approval、grant 和 execution receipt

Task Runtime 和 Permission Manager 各自拥有唯一状态；副作用能力仍注册在同一 ToolRegistry，不创建平行 CapabilityRegistry。

### v0.6 — Codex Specialist

JARVIS 将软件工程任务委托给 Codex：

```text
JARVIS → Task Runtime → isolated git worktree → Codex
       → tests → captured evidence → diff → summary → user approval
```

禁止自动 merge。Smart Request Router 可作为后续延迟和成本优化，不是 Codex 安全前置。

### v0.6A — Smart Request Router

实现 DIRECT / CONTROLLED / AGENT 三路，让普通聊天不再无条件额外调用 Agent Brain。

### v0.7 — Windows Assistant

先观察 processes、windows 和 active window，再评估 `launch_app`、`focus_window`、`open_path` 等低风险 Action。禁止万能 `run_command`。

### v0.8 — Voice Runtime

Wake Word、ASR、TTS、interruption、echo suppression、follow-up window 与 background-task notification。

### v0.9 — Screen Vision / Computer Use

Screen capture 与 understanding；优先 structured tools，GUI computer-use 只作为 fallback。

### v1.0 — Personal Workflow JARVIS

Calendar、Mail、Notes、Browser、Tasks、Notifications 与 bounded proactivity。

### Post-v1.0

Skills / MCP ecosystem → Camera → ROS2 Observe → Robot / Mechanical Arm。

机器人安全边界：LLM 只负责高层任务理解；ROS2、planner 与 controller 负责运动规划和控制；LLM 不得直接输出底层 actuator command。

## Change Discipline

- Milestone 可以细化实现，但不得静默改变本 Roadmap 的主架构。
- 架构方向变化必须显式修改本文件，并通过新的 ADR 记录原因和边界。
- 当前实现事实与人工验收结果只写入 milestones、CHANGELOG 和 ADR，不把临时调试历史堆入 Roadmap。

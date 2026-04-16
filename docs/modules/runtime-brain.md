# Runtime 内核（Brain）

`GazerBrain` 是系统运行时编排中心，负责把 Agent、Memory、Tools、Channels、Devices、Scheduler 组装成一个可持续运行的闭环。

## 关键代码

- 启动入口：`main.py`
- Runtime 核心：`src/runtime/brain.py`
- 配置系统：`src/runtime/config_manager.py`
- 安全 IPC：`src/runtime/ipc_secure.py`

## 启动流程

1. `main.py` 创建 3 条 IPC 队列（UI、chat input、chat output）并用 `wrap_queue` 签名包装。
2. 按配置决定是否拉起 UI 子进程。
3. 拉起 Admin API 子进程（`run_admin_api`）。
4. 构造 `GazerBrain` 并执行 `await brain.start()`。

## Brain 初始化职责

- OpenViking 预检：`ensure_openviking_ready()`
- MemoryManager 初始化
- Agent 初始化（含 MessageBus / ToolRegistry / LLM Provider）
- Orchestrator 初始化（多 Agent + SLA + 资源锁）
- Capture/Device/Channel 初始化
- PluginLoader / SkillLoader 装配
- CronScheduler / HeartbeatRunner 启停

## 运行中循环

`GazerBrain.start()` 在后台并发运行：

- Agent 主循环
- 命令队列执行器
- Cron 与 Heartbeat
- 各 Channel 监听
- 感知采集（屏幕/摄像头/卫星帧）
- 可选唤醒词处理

主循环持续检查配置热更新与状态动作（如呼吸姿态）。

## 关闭流程

`stop()` 会按顺序停止：

- 传感/硬件
- Capture Manager
- CommandQueue / Orchestrator
- Cron / Heartbeat / Gmail Push
- Agent 与后台任务

确保子任务取消后再退出进程，避免悬挂状态。

## 设计约束

- Channel 不应直接调用 Agent，必须经 `MessageBus`。
- Runtime 只负责编排，不应在此堆叠业务逻辑。
- 安全策略集中在 `src/security/*`，Runtime 仅注入与调用。

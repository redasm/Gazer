# Runtime 内核

Runtime 是系统装配层，核心入口在 `src/runtime/brain.py`。

## 主要职责

- 初始化配置、日志、总线、LLM、工具、通道与设备。
- 维护主循环和生命周期（start/stop）。
- 管理心跳任务、cron 任务与运行状态。

## 关键组件

- `GazerBrain`：系统总编排器。
- `ConfigManager`：配置读取、热更新、敏感字段掩码。
- 各种 `_init_*` 方法：将能力模块绑定到统一运行时。

## 设计约束

- Runtime 负责“连线”，业务逻辑下沉到模块内部。
- 不应在 Runtime 中实现可复用策略（应进入 `src/security`、`src/tools`、`src/agent`）。
- 所有外部输入通道应通过总线汇聚，避免旁路调用。

## 推荐扩展点

- 在 `_setup_tools` 增加新工具注册流程。
- 在 `_init_channels` 增加新通道适配器。
- 在 `ConfigManager` 添加可审计配置项。


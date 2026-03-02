# 设备节点（Device Node）

设备层负责把 Agent 的工具调用映射为可执行动作，统一通过 `DeviceRegistry` 管理。

## 模块职责

- 抽象节点协议：`DeviceNode`（`src/devices/registry.py`）。
- 注册/路由节点：`DeviceRegistry`（按 `target` 或默认节点调度）。
- 统一动作结果：`NodeActionResult`（`src/devices/models.py`）。
- 提供本地、卫星、机体硬件三类节点实现。

## 关键代码

- 节点抽象与注册中心：`src/devices/registry.py`
- 数据模型：`src/devices/models.py`
- 本地桌面节点：`src/devices/adapters/local_desktop.py`
- 远程卫星节点：`src/devices/adapters/remote_satellite.py`
- 机体硬件节点：`src/devices/adapters/body_hardware.py`
- 启动装配：`src/runtime/brain.py`（`_init_devices`）

## 运行流程

1. `GazerBrain._init_devices()` 读取配置并构建设备拓扑。
2. `DeviceRegistry.register()` 注册可用节点。
3. 工具调用进入设备层时，`DeviceRegistry.invoke()` 负责：
   - 目标解析（显式 `target` / 默认节点 / 单节点自动推断）
   - 能力校验（动作是否在节点 capability 列表中）
   - 调用转发与异常收敛（统一错误码）
4. 节点返回 `NodeActionResult`，上层据此决定下一步。

## 当前节点模式

- 本地模式：注册 `LocalDesktopNode`，可执行本机截图、输入等动作。
- 卫星模式：存在 `perception.satellite_ids` 时注册多个 `RemoteSatelliteNode`。
- 机体模式：`devices.body_node.enabled=true` 时注册 `BodyHardwareNode`。

## 配置关注项

- `devices.default_target`
- `devices.local.backend`
- `devices.satellite.nodes.*`
- `devices.satellite.invoke_timeout_seconds`
- `devices.body_node.*`

配置文件：`config/settings.yaml`

## 故障排查

- `DEVICE_TARGET_REQUIRED`：未指定目标且无默认节点。
- `DEVICE_TARGET_NOT_FOUND`：目标节点不存在或未注册。
- `DEVICE_ACTION_UNSUPPORTED`：动作不在节点 capability。
- `DEVICE_INVOKE_EXCEPTION`：节点执行内部异常。

建议先看 Brain 启动日志中的 `Registered device node` 与节点列表接口输出，再定位动作层错误。

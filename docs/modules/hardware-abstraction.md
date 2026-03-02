# 硬件抽象层（Hardware Abstraction Layer）

Gazer 支持具身硬件控制：机械臂、球形/头显屏幕、LED、麦克风与 TTS 等。硬件抽象层（HAL）将具体硬件与 Agent/工具/设备节点解耦，便于扩展不同机体形态（机械臂、轮式、四足等）。

## 分层关系

```
Agent / 工具 / Device 工具
        │
        ▼
  DeviceRegistry + BodyHardwareNode（设备节点，暴露 action）
        │
        ▼
  BodyDriver（硬件抽象接口）
        │
        ├── SerialArmDriver   → 机械臂（串口/USB）
        ├── NullDriver       → 无硬件（开发/无头）
        └── 其他 Driver 扩展  → 未来机体
```

- **BodyDriver**：统一机体接口，与具体硬件形态无关。
- **BodyHardwareNode**：将 BodyDriver 包装为设备节点，通过 `DeviceRegistry.invoke()` 暴露 `hardware.*` 动作。
- **工具层**：`HardwareControlTool`、`VisionTool` 等直接使用 BodyDriver 或通过设备节点间接调用。

## 关键代码

- 抽象接口与空实现：`hardware/drivers/base.py`
- 机械臂驱动：`hardware/drivers/serial_arm.py`
- 驱动工厂：`hardware/drivers/factory.py`
- 设备节点封装：`src/devices/adapters/body_hardware.py`
- 工具与插件：`src/tools/hardware.py`、`src/extensions/hardware/plugin.py`
- Brain 装配：`src/runtime/brain.py`（`_init_devices` 内注册 BodyHardwareNode）

## BodyDriver 接口

所有机体驱动必须实现：

| 方法/属性 | 说明 |
|-----------|------|
| `connect()` / `disconnect()` | 建立/断开与硬件的连接 |
| `is_connected` | 当前是否已连接 |
| `gesture(name, **kwargs)` | 执行命名动作（如 nod、shake_head、breathe） |
| `set_actuator(name, value)` | 底层执行器控制（如舵机角度） |
| `set_leds(rgb)` | LED 颜色 [R, G, B]，0–255 |
| `capabilities` | 本驱动支持的 gesture 名称列表 |

不同机体对同一接口做不同实现：机械臂映射到舵机与串口指令，无硬件则用 `NullDriver` 空实现。

## 当前驱动

### SerialArmDriver（机械臂）

- 通过串口（USB）与 Gazer 机械臂通信。
- 支持动作：`nod`、`shake_head`、`greet`、`shy`、`breathe`、`tracking_look`、`reset`。
- 配置：`body.type=serial_arm`，`body.port`（如 `auto` 或 `COM3`），`body.baudrate`（默认 115200）。

### NullDriver

- 无实体硬件时使用，所有调用为 no-op。
- 配置：`body.type=none`（默认）。

## 设备节点暴露的能力（BodyHardwareNode）

当 `devices.body_node.enabled=true` 时，BodyHardwareNode 向 DeviceRegistry 注册，工具/Agent 可通过设备 invoke 或直接工具调用使用下列能力：

| 动作 | 说明 | 安全等级 |
|------|------|----------|
| `hardware.status` | 连接状态与支持的 gesture | safe |
| `hardware.move_head` | 头舵机 yaw/pitch 角度（0–180） | privileged |
| `hardware.set_led` | LED RGB | privileged |
| `hardware.gesture` | 执行命名动作 | privileged |
| `hardware.vision.distance` | 从空间感知获取用户距离 | safe |
| `hardware.audio.transcribe` | 麦克风录音转文字 | privileged |
| `hardware.audio.speak` | TTS 播放 | privileged |
| `hardware.display.message` | 向连接中的 UI/显示通道发送状态（如球形显示、头显） | safe |
| `hardware.connect` / `hardware.disconnect` | 连接/断开机体（可配置关闭） | privileged |

球形显示或头显屏幕作为“显示输出”通过 `hardware.display.message` 与 UI 队列对接，由前端或头显客户端消费。

## 工具与插件

- **HardwareControlTool**：提供 `move_head`、`set_led`，供 Agent 直接调用，内部使用 BodyDriver。
- **VisionTool**：查询视觉/空间信息（如距离），依赖可选的 SpatialPerceiver。
- 插件 **extensions/hardware**：在具备 `body` 服务时注册上述工具。

## 配置

```yaml
# config/settings.yaml

body:
  type: none          # none | serial_arm
  port: auto          # 串口，如 COM3 或 auto 自动检测
  baudrate: 115200

devices:
  body_node:
    enabled: false    # 为 true 时注册 BodyHardwareNode
```

## 扩展新机体

1. 在 `hardware/drivers/` 下实现 `BodyDriver` 子类（连接、gesture、set_actuator、set_leds）。
2. 在 `hardware/drivers/factory.py` 的 `create_body_driver()` 中根据配置实例化新驱动。
3. 若需新动作，在 `BodyHardwareNode.invoke()` 中增加对应 `hardware.*` 分支，并在 `info().capabilities` 中声明。

## 与设备节点的关系

设备节点文档描述的是 **DeviceRegistry + DeviceNode** 的通用模型（本地桌面、卫星、机体三类节点）。机体节点（BodyHardwareNode）是其中一类，其底层由本硬件抽象层（BodyDriver）具体执行。详见 [设备节点（Device Node）](./device-node.md)。

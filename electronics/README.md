# Gazer 硬件设计

> 桌面级 AI 伴侣机器人的电子与机械硬件

## 目录结构

```
hardware/
├── head_unit/          # 头部单元 PCB (KiCAD)
├── arm_controller/     # 机械臂控制板 PCB (KiCAD)
├── mechanical/         # 机械结构设计 (3D模型/工程图)
├── bridge.py           # 硬件通信桥接 (Python)
├── gestures.py         # 手势/动作控制
└── __init__.py
```

## 模块说明

### 1. 头部单元 (`head_unit/`)
75mm 圆形 4 层 PCB，集成头部所有电子元件：
- **USB2514B** 4 端口 USB Hub (上行至手臂连接器)
- **AMS1117-3.3** LDO 稳压器 (5V→3.3V)
- **MAX98357A** I2S 音频功放 + 扬声器
- **XMOS XVF3000** DSP (AEC/降噪/DOA)
- **MSM261S4030H** × 4 PDM 麦克风阵列 (90° 间隔)
- **TC358762** HDMI-MIPI 桥 → 圆形 LCD
- **USB-A** 深度相机接口 (RealSense D405)

详细规格见 `doc/head_pcb_spec.md`

### 2. 机械臂控制板 (`arm_controller/`)
主控底座 PCB，包含：
- **ESP32-S3** 主控 (USB CDC + WiFi/BLE)
- **12V→5V** Buck 降压 (TPS5430, 3A)
- **STS3215** 串行舵机接口 (半双工 UART + 缓冲)
- **USB Hub** (GL850G/GL3520) 汇聚所有外设
- **PCM2706** USB 音频 DAC
- **MPU6050** IMU (头部姿态)
- 电容触摸输入

详细规格见 `doc/hardware_design.md`

### 3. 机械结构 (`mechanical/`)
5-DOF 情感表达机械臂：
- J1: 基座旋转 (±180°, 配滑环)
- J2: 肩部俯仰 (0°~120°)
- J3: 肘部俯仰 (0°~135°)
- J4: 头部偏航 (±70°)
- J5: 头部俯仰 (±60°)

执行器: Feetech STS3215 串行总线舵机 × 5

详细规格见 `doc/mechanical_design.md`

## 电源架构

```
12V/3A DC → [Dock] ─── 12V ──→ 舵机 (STS3215 × 5)
                   └── Buck ──→ 5V ──┬── USB Hub
                                     ├── ESP32 (via LDO→3.3V)
                                     └── 复合线缆 ──→ [头部]
                                          ├── USB2514B Hub
                                          ├── MAX98357A 功放
                                          ├── 深度相机
                                          └── AMS1117 ──→ 3.3V
                                               ├── XMOS DSP
                                               ├── PDM 麦克风 × 4
                                               └── HDMI-MIPI 桥
```

## 数据架构

```
Mac Mini / PC ── USB-C ──→ [Dock USB Hub]
                              ├── Port 1: ESP32-S3 (舵机控制)
                              ├── Port 2: PCM2706 (音频)
                              └── Port 3: 头部 Hub (via 手臂线缆)
                                   ├── Port 1: 深度相机
                                   ├── Port 2: XMOS 麦克风阵列
                                   ├── Port 3: 预留
                                   └── Port 4: 预留 (LED 环)
```

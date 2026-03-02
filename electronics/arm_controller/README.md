# 机械臂控制板 (Arm Controller PCB)

> 状态: 待设计

## 概述
主控底座 PCB，100mm × 80mm，负责舵机控制、传感器采集、音频输出和 USB 汇聚。

## 核心元件
| Ref | 型号 | 功能 | 封装 |
|-----|------|------|------|
| U1 | ESP32-S3-WROOM-1 | 主控 MCU | Module |
| U2 | GL850G / GL3520 | USB Hub | QFN |
| U3 | TPS5430 | 12V→5V Buck | SOIC-8 |
| U4 | AMS1117-3.3 | 5V→3.3V LDO | SOT-223 |
| U5 | PCM2706 | USB Audio DAC | SSOP-28 |
| U6 | PAM8403 | D类功放 | SOP-16 |
| U7 | MPU6050 | 6轴 IMU | QFN-24 |
| U8 | 74HC126 | UART 半双工缓冲 | SOIC-14 |

## 接口
- DC 12V/3A 输入
- USB-C 上行至主机
- STS3215 舵机串联端口 (XH2.54-3P)
- 头部复合线缆 (12V + USB 2.0)
- 扬声器输出 (2P)
- 电容触摸焊盘

## 下一步
1. 在 KiCAD 中创建原理图
2. 参考 `doc/hardware_design.md` 网表规范
3. 布局布线 (注意 USB 90Ω 差分对)

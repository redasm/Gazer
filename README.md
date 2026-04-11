# Gazer

Gazer 是一个桌面级具身 AI 伴侣系统，采用 `Runtime + Agent + Tools + Memory + Soul + Devices` 的分层架构。

当前仓库已完成多轮重构，核心能力集中在以下路径：

- 运行时编排：`main.py`、`src/runtime/brain.py`
- 智能体执行：`src/agent/*`
- 管理 API 与 Web 控制台：`src/tools/admin_api.py`、`web/*`
- 记忆与人格：`src/memory/*`、`src/soul/*`
- 安全边界：`src/security/*`
- 设备与卫星节点：`src/devices/*`

## 核心能力

- 双脑执行路径：快响应与深度推理分层（`fast_brain` / `slow_brain`）。
- 工具治理：统一 `ToolRegistry` + 分层策略管线 + 安全等级（Tier）控制。
- 长期记忆：OpenViking 语义记忆与长期提炼，数据落在 `data/openviking`。
- Soul 体系：三槽位 `WorkingContext`、预算管理、主动推断、人格演化宪法约束。
- 多通道与多节点：`MessageBus` 解耦 Channel/Agent，支持卫星节点接入。
- 管理可观测：FastAPI Admin API + React 控制台 + WebSocket 实时事件。

## 架构总览

核心主链路（简化）：

1. Channel 收到输入，调用 `ChannelAdapter.publish()`。
2. 消息进入 `MessageBus`（`src/bus/queue.py`）并执行限流。
3. `AgentLoop`（`src/agent/loop.py`）构建上下文、调用 LLM、执行工具。
4. 结果通过 `MessageBus` 出站，由各 Channel 发送。
5. Turn 记忆、策略与审计信息写入 Memory/Observability。

运行生命周期由 `GazerBrain` 统一编排：

- 初始化 OpenViking、MemoryManager、Agent、Orchestrator、DeviceRegistry、Channels。
- 启动 Admin API 子进程、Agent 主循环、Cron/Heartbeat、感知捕获与硬件层。

## 目录结构

```text
Gazer/
  assets/                  # SOUL/Prompt 等运行时资产（SOUL.md 为人格唯一源）
  config/                  # settings.yaml 与环境配置
  data/openviking/         # 记忆与配对等持久化数据
  doc/                     # 架构重构设计文档
  docs/                    # 项目文档中心
  src/
    agent/                 # Agent loop/orchestrator/adapter
    bus/                   # MessageBus 与命令队列
    channels/              # Telegram/Web/Discord 等通道适配
    devices/               # 本地节点/卫星节点/协议与会话
    llm/                   # Provider 与路由器
    memory/                # OpenViking memory backend
    runtime/               # Brain、部署、安全 IPC、日志脱敏
    security/              # owner/pairing/threat scan 等
    soul/                  # 情感、人格、认知、工作上下文
    tools/                 # 工具实现与 Admin API 路由
  tests/                   # pytest 测试
  web/                     # React + Vite 管理台
  main.py                  # 启动入口
```

## 快速开始

### 1. 环境准备

- Python 3.10+
- Node.js 18+（如需 `web/` 前端开发）

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
# source .venv/bin/activate

pip install -e .
```

可选安装额外依赖组：

```bash
pip install -e ".[dev,perception,ui,browser,satellite]"
```

### 2. 配置

- 复制 `.env.example` 为 `.env` 并填写模型密钥。
- 核对 `config/settings.yaml`。
- 人格唯一源文件：`assets/SOUL.md`。

### 3. 启动

后端（默认同时拉起 Brain 与 Admin API 子进程）：

```bash
python main.py
```

CLI 模式：

```bash
python main.py --cli
```

前端开发（可选）：

```bash
cd web
npm install
npm run dev
```

默认后端地址为 `http://localhost:8080`（可由 `ADMIN_API_PORT` 或配置覆盖），前端默认使用 `VITE_API_BASE` 或 `http://localhost:8080`。

### 4. 基础验证

- 健康检查：`GET /health`
- 登录会话：`POST /auth/session`
- WebSocket：`/ws/status`、`/ws/chat`、`/ws/canvas`

## 测试与质量

先跑定向，再跑全量：

```bash
pytest -q tests/test_security_regressions.py
pytest -q tests/test_tools_and_skills.py
pytest -q
```

## 安全与运维要点

- 安全逻辑集中在 `src/security/*`，避免在业务模块复制鉴权逻辑。
- IPC 队列默认走 HMAC 包装（`src/runtime/ipc_secure.py`）。
- Admin Token / Session 由 `OwnerManager` 托管（`src/security/owner.py`）。
- 配对审批与 allowlist 由 `PairingManager` 维护（`src/security/pairing.py`）。
- 运行日志经 `runtime/log_sanitizer.py` 脱敏。

## 文档入口

- 文档中心：[`docs/README.md`](docs/README.md)
- 模块文档总览：[`docs/modules/index.md`](docs/modules/index.md)

## 许可证

本项目使用 Apache-2.0 License，详见 [`LICENSE`](LICENSE)。

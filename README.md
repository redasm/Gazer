# Gazer

Gazer 是一个桌面级具身 AI 伴侣系统，采用 `Runtime + Agent + Tools + Memory + Soul + Devices` 的分层架构。

## 核心能力

- **双脑执行路径** — 快响应与深度推理分层（`fast_brain` / `slow_brain`）。
- **工具治理** — 统一 `ToolRegistry` + 分层策略管线 + 安全等级（Tier）控制。
- **长期记忆** — OpenViking 语义记忆与长期提炼，数据落在 `data/openviking`。
- **Soul 体系** — 三槽位 `WorkingContext`、预算管理、主动推断、人格演化宪法约束。
- **多通道与多节点** — `MessageBus` 解耦 Channel/Agent，支持 Telegram、Discord、飞书、Slack 等通道及卫星节点接入。
- **管理可观测** — FastAPI Admin API + React 控制台 + WebSocket 实时事件。

## 架构总览

核心主链路（简化）：

1. Channel 收到输入，调用 `ChannelAdapter.publish()`。
2. 消息进入 `MessageBus`（`src/bus/queue.py`）并执行限流。
3. `AgentLoop`（`src/agent/loop.py`）构建上下文、调用 LLM、执行工具。
4. 结果通过 `MessageBus` 出站，由各 Channel 发送。
5. Turn 记忆、策略与审计信息写入 Memory/Observability。

运行生命周期由 `GazerBrain` 统一编排：

- 初始化 OpenViking、MemoryManager、Agent、Orchestrator、DeviceRegistry、Channels。
- 启动 Admin API（同进程 asyncio 任务）、Agent 主循环、Cron/Heartbeat、感知捕获与硬件层。

## 目录结构

```text
Gazer/
  main.py                    # 启动入口
  pyproject.toml             # Python 包定义与依赖
  Dockerfile                 # 多阶段 Docker 构建
  docker-compose.yml         # Compose 编排
  .env.example               # 环境变量模板
  config/
    settings.yaml            # 主配置文件
  assets/                    # SOUL/Prompt 等运行时资产（SOUL.md 为人格唯一源）
  data/                      # 运行时数据目录（记忆、配对等）
  docs/                      # 项目文档中心
  skills/                    # 用户可扩展技能
  src/
    agent/                   # Agent loop / orchestrator / adapter
    bus/                     # MessageBus 与命令队列
    channels/                # Telegram / Web / Discord / 飞书 / Slack 等通道适配
    cli/                     # Click CLI（gazer start / chat / doctor 等）
    config/                  # 配置缺省值
    devices/                 # 本地节点 / 卫星节点 / 协议与会话
    eval/                    # 评测、trainer、self-evolution
    extensions/              # 插件：desktop / email / git / browser / cron 等
    flow/                    # Flow 引擎、审批
    llm/                     # LLM 抽象、LiteLLM、路由器
    memory/                  # OpenViking memory backend
    multi_agent/             # 多智能体 worker / monitor / pool
    plugins/                 # 插件加载器与 API
    runtime/                 # Brain、config_manager、子系统、IPC、日志脱敏
    security/                # owner / pairing / threat scan 等
    soul/                    # 情感、人格、认知、工作上下文
    tools/                   # 工具实现与 Admin API 路由
  web/                       # React + Vite 管理台
  tests/                     # pytest 测试
  perception/                # 感知子系统（屏幕 / 相机 / 音频）
  hardware/                  # 硬件串口驱动抽象
  electronics/               # KiCad 硬件设计
  satellite/                 # 卫星链路示例
```

## 先决条件

| 依赖 | 版本要求 | 说明 |
|------|---------|------|
| Python | >= 3.10 | 推荐 3.11 |
| pip | 最新 | 用于安装 Python 包 |
| Node.js | >= 18 | 仅前端开发需要，推荐 20 LTS |
| npm | >= 9 | 随 Node.js 安装 |
| Git | 最新 | 克隆仓库 |

## 安装

### 1. 克隆仓库

```bash
git clone https://github.com/your-org/gazer.git
cd gazer
```

### 2. 创建 Python 虚拟环境

```bash
python -m venv .venv
```

激活虚拟环境：

```bash
# Windows (PowerShell)
.venv\Scripts\Activate.ps1

# Windows (CMD)
.venv\Scripts\activate.bat

# macOS / Linux
source .venv/bin/activate
```

### 3. 安装 Python 依赖

安装核心依赖（可编辑模式）：

```bash
pip install -e .
```

按需安装可选依赖组：

| 依赖组 | 命令 | 包含内容 |
|--------|------|---------|
| `dev` | `pip install -e ".[dev]"` | pytest、black |
| `perception` | `pip install -e ".[perception]"` | OpenCV、MediaPipe、Whisper、TTS、音频 |
| `ui` | `pip install -e ".[ui]"` | PySide6 面部 UI |
| `browser` | `pip install -e ".[browser]"` | Playwright 浏览器自动化 |
| `satellite` | `pip install -e ".[satellite]"` | WebSocket 卫星节点连接 |

一次性安装全部可选依赖：

```bash
pip install -e ".[dev,perception,ui,browser,satellite]"
```

> **注意**：`perception` 组中的 `pyaudio` 在 Windows 上可能需要预先安装系统级音频库，macOS 需要 `portaudio`（`brew install portaudio`）。

### 4. 配置环境变量

复制环境变量模板并填写必要的 API 密钥：

```bash
# Windows (PowerShell)
Copy-Item .env.example .env

# macOS / Linux
cp .env.example .env
```

编辑 `.env` 文件，至少配置一个 LLM Provider 密钥：

```dotenv
# 必填：至少配置一个 LLM Provider
OPENAI_API_KEY=sk-xxxx

# 或使用其他 Provider（取消注释并填写）
# DEEPSEEK_API_KEY=your_key
# DASHSCOPE_API_KEY=your_key
# GEMINI_API_KEY=your_key
# ANTHROPIC_API_KEY=your_key
```

Provider 密钥按命名约定自动解析：`{PROVIDER_NAME}_API_KEY`。支持的 Provider 完整列表见 `.env.example`。

### 5. 检查配置文件

核心配置在 `config/settings.yaml`，包含模型选择、人格、语音、感知、设备、安全策略等。首次使用通常无需修改。

人格唯一源文件为 `assets/SOUL.md`，可按需自定义。

### 6. 安装前端依赖（可选）

如需使用 Web 管理控制台的开发模式：

```bash
cd web
npm install
cd ..
```

### 7. 验证安装

使用内置诊断命令检查环境：

```bash
gazer doctor
```

该命令会检查 Python 版本、核心依赖、配置文件、API 密钥、端口可用性等。

## 启动

### 标准模式

启动 Brain + Admin API（同进程 asyncio 任务）：

```bash
python main.py
```

或通过安装后的 CLI：

```bash
gazer start
```

### CLI 交互模式

```bash
python main.py --cli
```

或：

```bash
gazer chat
```

### Web 管理控制台（开发模式）

在另一个终端中启动前端开发服务器：

```bash
cd web
npm run dev
```

默认后端地址为 `http://localhost:8080`（可通过 `ADMIN_API_PORT` 环境变量或 `config/settings.yaml` 覆盖）。
前端通过 `VITE_API_BASE` 环境变量指定 API 地址，默认使用 `window.location.origin`。

### Docker 部署

构建并运行：

```bash
# 仅核心
docker build -t gazer .

# 包含可选依赖
docker build -t gazer --build-arg EXTRAS=perception,browser .
```

使用 Compose：

```bash
# 首先确保 .env 文件已配置
docker compose up -d
```

Compose 会自动挂载 `config/`、`data/`、`assets/`、`skills/`、`workflows/` 目录。

## 基础验证

启动后可通过以下方式验证系统运行状态：

```bash
# 健康检查
curl http://localhost:8080/health

# WebSocket 端点
# /ws/status  — 系统状态推送
# /ws/chat    — 对话流
# /ws/canvas  — 画布协作
```

## CLI 命令参考

```text
gazer                    显示帮助
gazer start              启动 Brain + Admin API
gazer chat               交互式 REPL
gazer onboard            首次配置向导
gazer doctor             系统诊断
gazer config show        查看当前配置（密钥脱敏）
gazer channel status     检查通道 Token 状态
gazer pairing list       查看待审批配对请求
gazer pairing approve    审批配对
gazer plugin list        列出已发现的插件
gazer plugin init        脚手架创建新插件
```

## 测试

先跑定向，再跑全量：

```bash
# 安全回归测试
pytest -q tests/test_security_regressions.py

# 工具与技能测试
pytest -q tests/test_tools_and_skills.py

# 全量测试
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

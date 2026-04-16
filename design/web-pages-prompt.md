# Gazer Web 页面 Prompt

## 产品描述

Gazer 是一款面向桌面场景的 embodied AI companion 管理控制台，服务于一个具备对话、感知、记忆、工具调用、多智能体协作与自我优化能力的 AI 伴侣系统。它不只是普通聊天机器人的后台，而是一个集运行监控、策略治理、模型路由、技能管理、训练闭环、人格调优和系统调试于一体的综合操作台。用户可以在这里配置大模型与语音能力，管理外部渠道接入，查看记忆图谱和任务轨迹，监控日志、观测指标与风险闸门，并对 AI 的行为边界、工具权限和人格演化进行精细控制。整体产品强调“实时感、状态感、可控性和可演化性”，适用于开发者、研究者或高级操作者持续调试和训练一个长期运行的 AI 个体。Web 端既承担日常运维角色，也承担实验、诊断、回放和治理角色，是 Gazer 作为 AI 主体持续成长和稳定运行的重要可视化中枢。

基于当前项目实际代码整理，重点参考 `web/src/App.jsx`、`web/src/components/Layout.jsx` 与 `web/src/pages/*`。这个文档只描述当前后台管理端有哪些页面、每个页面承担什么职责、页面内有哪些主要功能与操作，适合继续做 UI 设计、页面重绘、信息架构整理或设计稿生成。

## 全局页面框架

- 产品定位：`GAZER ADMIN CONSOLE`，是一个面向运维、调试、训练、策略治理的桌面 AI 伴侣后台控制台。
- 整体结构：左侧固定分组导航，右侧顶部状态栏，主区域为单页路由内容。
- 全局能力：
  - 中英文切换。
  - 顶部/底部展示系统在线状态与健康状态。
  - 首次访问可能需要输入 admin token。
  - 多数页面通过 API 实时读取数据，部分页面通过 WebSocket 实时推送。
  - 部分操作保存后会触发 toast 通知。

## 导航分组

### 1. System Config

- `Settings`
- `Model Providers`

### 2. Chat

- `Chat`
- `Multi-Agent`

### 3. Control

- `Dashboard`
- `Skills`
- `Memory`
- `Cron`
- `Canvas`
- `Workflow`

### 4. Policy

- `Security`
- `Tool Policy`
- `LLM Router`
- `Release Gate`
- `Optimization Tasks`
- `Trainer Jobs`
- `Observability`
- `Policy Audit`

### 5. Soul

- `Personality`

### 6. System Tools

- `Debug`
- `Logs`

## 页面清单与功能

### Dashboard

- 作用：系统总览首页，集中展示运行状态、资源使用、模型调用与趋势。
- 主要内容：
  - 系统信息卡片，如 CPU、内存、磁盘、运行时长等。
  - 模型调用与 token 使用统计。
  - 今日请求量、成本、平均延迟等摘要指标。
  - 按模型或按类别分布的统计可视化。
  - token 趋势图、调用趋势图。
- 主要功能：
  - 自动轮询刷新。
  - 切换时间范围/粒度查看趋势。

### Settings

- 作用：总配置中心，管理系统运行参数、模型、语音、渠道、搜索、感知、人格与执行后端。
- 主要内容：
  - Brain Profiles：主脑/副脑/嵌入模型配置，含 provider、model、embedding 开关与高级模型参数。
  - Wake Word：唤醒词开关、关键词、灵敏度。
  - Voice TTS：语音合成 provider、voice id、速率、音量、是否严格要求云端。
  - ASR：语音识别 provider、route mode、model size、输入设备、base URL、API key。
  - Channels：消息渠道配置，至少包含 Telegram、Feishu 等渠道的 token、app id、allowed ids、模拟输入/媒体分析等能力。
  - Web Search：搜索主 provider、fallback 顺序、启用的 provider、场景路由、相关性门槛、搜索 API key。
  - Memory Tool Persistence：哪些工具结果写入记忆、哪些只留在轨迹中。
  - Release Gate Health Thresholds：warning / critical 阈值配置。
  - Coding Execution Backend：exec 工具运行在哪，支持 local、docker sandbox、remote ssh，以及 fallback/identity/workspace 配置。
  - Perception：摄像头、动作识别、视觉 provider、卫星/节点、body node 暴露。
  - Personality：角色名、信任等级等人格基础参数。
  - Visual：眼睛颜色、眨眼频率、呼吸速度等外观表现参数。
- 主要功能：
  - 表单编辑配置。
  - Tag 输入。
  - Toggle 开关。
  - 保存配置、刷新配置。

### Model Providers

- 作用：独立管理模型服务提供商注册表，而不是在 Settings 中直接硬编码。
- 主要内容：
  - 左侧 provider 列表。
  - 右侧 provider 详情表单。
  - provider 基础字段：base URL、API key、default model、API 模式、鉴权方式、是否走 auth header。
  - 每模型 JSON 配置，支持 id/name/reasoning/input/cost/contextWindow/maxTokens。
- 主要功能：
  - 新增 provider。
  - 选择 provider 查看/编辑。
  - 保存 provider。
  - 删除 provider。
  - 刷新 provider 列表。

### Chat

- 作用：Web 端聊天主界面，用于直接和 Gazer 对话，并实时查看工具执行过程。
- 主要内容：
  - 左侧会话列表，可折叠。
  - 支持多个 session，本地持久化会话与消息。
  - 右侧消息区，区分 user / assistant / system / tool event。
  - 工具调用事件流，能展示工具名、进度、摘要、结果状态。
  - Markdown 渲染的助手消息。
  - 输入框与发送按钮。
- 主要功能：
  - 新建会话。
  - 切换会话。
  - 自动用首条消息命名会话。
  - 重命名会话。
  - 删除会话。
  - 复制消息。
  - WebSocket 实时收发消息。
  - 展示工具调用中间态、错误态、生成媒体附件提示。

### Multi-Agent

- 作用：多智能体任务看板，监控任务拆解、 worker 执行状态与操作员评论。
- 主要内容：
  - 多列 Kanban，看板列包括 queued、running、sleeping、completed、failed。
  - 顶部 mission/session 概览。
  - 任务统计：总数、运行中、睡眠中、完成、失败、tokens。
  - 任务详情侧栏：描述、依赖、当前工具、执行结果、错误、评论、日志。
  - 设置弹窗：多智能体开关、最大 worker 数量等。
- 主要功能：
  - WebSocket 实时监听 session.init、task.created、task.status 等事件。
  - 选择任务卡查看详情。
  - 发布 operator comment。
  - 调整多智能体执行配置并保存。

### Skills

- 作用：技能管理器，用于浏览、读取、编辑和创建技能。
- 主要内容：
  - 左侧技能列表，区分 builtin skills 和 extension skills。
  - 中间/右侧技能详情。
  - 文件列表，不只 `SKILL.md`，也可查看技能下其他文件。
  - Markdown 内容阅读与编辑区域。
- 主要功能：
  - 获取技能列表。
  - 选择技能查看说明。
  - 切换技能文件。
  - 编辑并保存技能文件。
  - 新建技能。

### Memory

- 作用：记忆图谱可视化页面。
- 主要内容：
  - 3D 图谱画布。
  - 节点类型图例，如 root、entity、topic、event、daily、emotion。
  - 节点数、边数统计。
  - 搜索框。
  - 选中节点详情面板。
  - 邻居节点与关系信息。
- 主要功能：
  - 图谱缩放/旋转/聚焦。
  - 悬停高亮邻接节点与边。
  - 点击节点聚焦镜头。
  - 搜索节点并跳转到目标。

### Logs

- 作用：实时日志台。
- 主要内容：
  - 日志表格。
  - 日志级别筛选。
  - 搜索框。
  - request_id、model、tokens 等元信息徽标。
- 主要功能：
  - 自动轮询刷新。
  - 按级别过滤。
  - 按关键词搜索。
  - 下载日志。
  - 清空日志。
  - 自动滚动到底部。

### Debug

- 作用：综合调试与回放中心，是运维/评测/编码调试最复杂的页面。
- 主要标签页：
  - System：系统资源、进程、基础状态。
  - LLM History：最近模型调用历史。
  - Config：调试配置查看。
  - Trajectories：轨迹、任务运行、编码基准与回放。
- Trajectories 标签下的主要内容：
  - 轨迹列表，支持按 session_key 和 limit 过滤。
  - 轨迹详情，包括 final status、event count、最终输出。
  - 事件流过滤：event type、tool_call_id、关键词搜索。
  - Task View：阶段耗时、错误数、turn latency。
  - Replay Preview：轨迹重放预览。
  - Compare Run：对比两个 run 的 shared/missing/added 步骤。
  - Resume / Auto Resume / Replay Execute。
  - Task Runs：任务执行列表与 checkpoint。
  - Coding Quality 指标。
  - Coding Benchmark：历史、leaderboard、scheduler、observability、run-now。
- 主要功能：
  - 查看系统状态。
  - 查看 LLM 历史。
  - 查看完整运行轨迹。
  - 按轨迹恢复会话。
  - 执行 replay。
  - 手动触发 coding loop。
  - 编辑 benchmark scheduler 配置并保存。

### Security

- 作用：安全与访问控制中心。
- 主要内容：
  - Owner Channel IDs 配置。
  - Pending Pairings：待批准配对请求。
  - Approved Senders：已授权发送者，按 channel 分组展示。
  - System Doctor：系统健康诊断总览与检查项结果。
- 主要功能：
  - 保存 owner channel ids。
  - 批准 pairing。
  - 拒绝 pairing。
  - 撤销已授权 sender。
  - 刷新 pairing 数据。
  - 刷新 doctor 诊断结果。

### Cron

- 作用：定时任务管理页。
- 主要内容：
  - 定时任务列表。
  - 新建任务表单，包括 name、cron expression、message、enabled、one-shot。
- 主要功能：
  - 新建 cron job。
  - 启用/禁用任务。
  - 删除任务。
  - 刷新任务列表。

### Canvas

- 作用：Agent 生成式画布/面板展示区，用于承载 Markdown、JSON、表格和 A2UI 结构化 UI。
- 主要内容：
  - 画布版本号与连接状态。
  - Panel 列表。
  - 单 panel 可显示：
    - markdown
    - json
    - table
    - a2ui
    - 纯文本
- 主要功能：
  - WebSocket 实时接收 `canvas_update`。
  - 清空全部 panel。
  - 对 A2UI 组件转发 userAction 给后端。

### Tool Policy

- 作用：工具暴露与执行策略治理页。
- 主要内容：
  - tool allowlist。
  - tool denylist。
  - tool groups JSON。
  - effective policy 查看。
  - explain policy 查询。
  - policy simulate 结果列表。
  - 按 reason/status/tool 搜索与筛选。
- 主要功能：
  - 编辑并保存策略配置。
  - 查看某个 agent 的有效策略。
  - 解释某个工具为什么允许/禁止。
  - 模拟整个工具集的准入结果。
  - 导出 simulation CSV。

### LLM Router

- 作用：大模型路由状态与预算控制页。
- 主要内容：
  - 当前路由策略。
  - 路由是否启用。
  - provider 健康统计：calls、success_rate、latency、当前 model。
  - 预算策略：enabled、window_seconds、max_calls、max_cost_usd、estimated_input_tokens_per_char。
  - provider_cost_per_1k_tokens JSON。
- 主要功能：
  - 读取当前 router status。
  - 切换 routing strategy。
  - 编辑预算策略并保存。

### Release Gate

- 作用：发布闸门/高风险动作闸门页面。
- 主要内容：
  - Gate 当前状态：blocked、reason、source、updated_at。
  - Gate 与 workflow health 联动状态说明。
  - 当前阈值明细。
  - Workflow observability 卡片。
  - Manual Override 表单：blocked、reason、source、metadata JSON。
- 主要功能：
  - 查看闸门是否阻塞。
  - 查看 workflow success rate、failures、p95 latency 等信号。
  - 手动 override gate。

### Optimization Tasks

- 作用：优化闭环任务管理页，跟踪 benchmark/gate 失败后产生的待优化项。
- 主要内容：
  - 任务列表。
  - 按 status、dataset_id 过滤。
  - 每条任务展示 task_id、dataset_id、fail_streak、priority、created_at、note。
- 主要功能：
  - 更新任务状态为 open / resolved / dismissed。
  - 填写 note。
  - 刷新任务列表。

### Trainer Jobs

- 作用：训练任务与实验管理页。
- 主要内容：
  - Training jobs 列表。
  - Experiments 列表与下拉选择。
  - 过滤项：dataset_id、status。
  - 任务详情抽屉/面板。
  - 输出 JSON 详情展示。
- 主要功能：
  - 创建 training job。
  - 手动运行 training job。
  - 创建 experiment。
  - 运行 experiment。
  - 查看 job detail。
  - 刷新 jobs / experiments。

### Observability

- 作用：综合观测页面，强调指标、趋势、告警、记忆质量、失败归因。
- 主要内容：
  - 总体统计卡片。
  - observability metrics。
  - trends。
  - efficiency baseline。
  - alerts。
  - trajectories 简表与 run detail。
  - memory quality report。
  - memory turn health。
  - tool failure / tool error 排行与排序。
- 主要功能：
  - limit、排序项写入 URL 状态。
  - 导出 memory quality report。
  - 查看并重试某条 trajectory。
  - 查看告警与观测基线。

### Policy Audit

- 作用：策略变更审计页。
- 主要内容：
  - 审计记录列表。
  - action 过滤器。
  - timestamp、action、details 展示。
- 主要功能：
  - 刷新审计记录。
  - 导出 CSV。
  - 清空审计记录。

### Personality

- 作用：人格状态、反馈与自优化页面。
- 主要内容：
  - Personality State：当前 affect、mental_state、OCEAN 五维人格向量。
  - System Prompt 编辑区。
  - Evolution Stats。
  - Feedback 提交区：positive / negative / correction。
  - History：近期人格演化历史。
- 主要功能：
  - 调整并保存 OCEAN 向量。
  - 编辑并保存 system prompt。
  - 提交用户反馈。
  - 触发优化，让系统根据反馈改写 prompt。
  - 查看 evolution stats 与 history。

## 当前应当纳入设计稿的共享组件

- 左侧分组导航。
- 顶部健康状态条。
- 通用玻璃态卡片/面板。
- 表格列表页模板。
- 筛选栏模板。
- 空状态模板。
- Toast / Notice 提示。
- Confirm Modal。
- ToggleSwitch。
- TagInput。

## 当前代码中已发现的页面缺口

- 路由和导航里仍然存在 `Workflow` 页面入口。
- 但当前工作区中 `web/src/pages/WorkflowStudio.jsx` 已删除。
- 同时 `web/src/components/WorkflowObservabilityCard.jsx` 也已删除，而 `ReleaseGate`、`OptimizationTasks`、`TrainerJobs` 等页面仍有引用痕迹。
- 因此，如果基于“当前实际可实现页面”生成设计稿，建议先以本文件列出的“现存页面代码”为准，并把 `Workflow` 视为待补全/已下线页面。

## 可直接用于页面生成/设计工具的简版提示词

为 Gazer 设计一套后台管理控制台页面。产品是一个桌面 embodied AI companion 的 admin console，整体为深色、科技感、控制台风格。页面结构为左侧分组导航 + 顶部健康状态栏 + 右侧主内容区。需要覆盖这些页面及功能：Dashboard 系统总览；Settings 全量系统配置；Model Providers 提供商注册表；Chat 多会话聊天与工具事件流；Multi-Agent 多智能体看板；Skills 技能管理与编辑；Memory 3D 记忆图谱；Logs 日志台；Debug 调试/轨迹/回放/benchmark；Security 配对审批与系统体检；Cron 定时任务；Canvas Agent 生成式画布；Tool Policy 工具准入策略；LLM Router 路由与预算；Release Gate 发布闸门；Optimization Tasks 优化闭环任务；Trainer Jobs 训练任务与实验；Observability 指标、趋势、告警、记忆质量；Policy Audit 策略审计；Personality 人格状态、反馈与自优化。要求突出信息密度、状态感、运行中反馈、列表与详情联动、实时性与专业运维气质。

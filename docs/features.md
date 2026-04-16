# 项目特色与优势

这页用于回答一个核心问题：**Gazer 与通用聊天机器人相比，优势在哪里？**

## 1) 双脑架构（快脑 + 慢脑 + BrainHint 路由）

Gazer 在 Agent 执行链路中区分不同思考路径：

- 快脑：低延迟响应，处理轻量问题与常规交互。
- 慢脑：复杂推理与高风险决策，支持更稳健的工具编排。
- BrainHint 声明式路由：调用方通过 `BrainHint(latency_critical, quality_critical, reasoning_depth)` 声明意图，由 `DualBrainRouter` 统一决策，无需硬编码快/慢脑选择。

实现线索：

- `src/agent/loop.py`：快速路径 `_try_fast_brain(...)` 与主循环协同。
- `src/multi_agent/brain_router.py`：三维决策矩阵路由。
- `src/multi_agent/dual_brain.py`：适配层，兼容新旧两种调用方式。

**价值**：在"响应速度"和"任务质量"之间取得工程可控的平衡，且路由逻辑集中可审计。

## 2) 多 Agent 协同系统（Multi-Agent Collaboration）

面对复杂任务时，Gazer 可自动拆解为多 Worker 并行执行：

- 任务复杂度前置评估：快脑四维打分（可并行性/信息广度/高价值/低依赖），<200ms 决定分流。
- Planner-Worker 架构：Planner 慢脑规划 DAG，Worker 快脑并行执行。
- Work Stealing + 自适应恢复：Worker 原子领取任务，出错自动切慢脑重试，超出能力升级 Planner。
- Blackboard 共享状态：避免上下文窗口爆炸，结果直写共享存储。

实现位置：`src/multi_agent/*`、`src/agent/adapter.py`（`process_auto`）

**价值**：复杂任务的执行时间可缩短 2-4 倍，同时对简单任务零额外开销。

## 3) 长期记忆系统（Memory）

Gazer 不只保留短上下文，而是具备长期记忆能力：

- 对话与事件持久化
- 语义检索与历史召回
- 记忆与当前任务上下文融合

实现位置：`src/memory/*`

**价值**：让助手在跨天、跨会话场景下保持连续性与个体化理解。

## 4) 人格引擎 Soul

Soul 负责"怎么想、怎么说、怎么保持一致"：

- 人格边界与表达风格约束
- 工作记忆与认知步骤编排
- 人格一致性与安全边界协同

实现位置：`src/soul/*`

**价值**：输出风格稳定，不因任务切换而出现强烈人格漂移。

## 5) 自我进化闭环（Evolution / Training）

Gazer 已具备"评测—优化—再评测"的工程骨架：

- 评测基准与 gate 机制
- 优化任务与训练任务接口
- 发布门禁联动（高风险动作可硬阻断）

实现线索：`src/tools/admin/training_routes.py` 中的 `debug/eval-benchmarks`、`debug/optimization-tasks`、`debug/training-jobs` 相关接口。

**价值**：系统能力可持续提升，而非纯手工调 prompt。

## 5.1) 进化质量增强（GEPA + Skill Evolution + Auto Dataset）

在基础进化骨架之上，新增三个子系统进一步提升进化质量：

### GEPA-Lite 遗传进化引擎
- `LightningLiteTrainer.generate_patch()` 在规则引擎 seed patch 基础上运行微种群遗传搜索。
- 变异算子：add\_rule / remove\_rule / mutate\_router\_strategy；交叉算子：规则集两点合并。
- 适应度函数：eval\_pass\_improvement(50%) + rule\_parsimony(20%) + tool\_coverage(20%) + router\_alignment(10%)。
- 安全保证：GEPA 输出分数 < seed 时自动回退，不劣化原始 patch。
- 配置开关：`trainer.gepa.enabled`（默认 false）。

实现线索：`src/eval/gepa_optimizer.py`、`src/eval/trainer.py`。

### 技能/工具描述进化（Skill Evolver）
- 从轨迹数据中提取 per-tool 失败画像（失败次数、错误码分布、坏输入样本）。
- 通过 LLM meta-prompt 或启发式模板生成描述改进提案（Proposal）。
- 双重安全门：字符数 ≤ 500，Jaccard 语义保留率 ≥ 0.75（均可配置）。
- 审核流：pending → approved → applied；apply 只写 `skill_overrides` 配置，不改源码。
- API：`POST /debug/skill-evolution/proposals/generate` → `POST .../approve` → `POST .../apply`。

实现线索：`src/eval/skill_evolver.py`、`src/tools/admin/training_routes.py`。

### 评测基准自动生成（Dataset Auto Builder）
- 三种策略自动从 bridge export 构建 eval 数据集：正例（高成功率轨迹）、负例（失败轨迹）、工具合约（(tool, expected\_status) 模式）。
- 无需手动标注，gate 连续失败后系统可自动扩充测试集覆盖率。
- recall query set 生成：从本地 SKILL.md 文件自动生成与 memory recall regression 兼容的查询集。
- API：`POST /debug/eval-benchmarks/auto-build`、`POST /debug/eval-benchmarks/build-recall-query-set`。

实现线索：`src/eval/dataset_auto_builder.py`、`src/eval/benchmark.py`（`build_dataset_auto`）。

## 6) 具身与硬件支持

Gazer 面向桌面具身场景，支持设备接入与机体控制：

- **硬件抽象层（HAL）**：BodyDriver 统一接口，支持机械臂（串口）、球形/头显显示、LED、麦克风与 TTS；SerialArmDriver / NullDriver 可配置切换。
- **设备节点**：DeviceRegistry 调度本地桌面、卫星与机体节点；BodyHardwareNode 将 HAL 暴露为 `hardware.*` 动作供工具与 Agent 调用。
- 硬件状态回传与心跳、高风险物理动作的策略门禁。

实现线索：`hardware/drivers/*`（BodyDriver、SerialArmDriver）、`src/devices/*`、`src/runtime/brain.py`（设备初始化与编排）。详见 [硬件抽象层](./modules/hardware-abstraction.md)。

**价值**：从"纯软件助手"扩展到"可感知、可执行"的实体交互体验（机械臂、球形显示等）。

## 7) 生产级治理能力

- 工具策略分级（allow/deny/group/tier/provider）
- 管理端鉴权与审计
- LLM 路由、预算与可观测指标
- MCP JSON-RPC 统一工具发现与调用入口（`/mcp`）
- Web 可视化 Workflow Builder（节点配置、保存、运行闭环）

实现位置：`src/tools/registry.py`、`src/tools/admin_api.py`、`src/runtime/config_manager.py`

**价值**：适合长期运行，不是一次性 Demo。

---

## 9) 已落地的 P1~P4 强化能力（最新）

> 开发阶段按"主路径优先，不做兼容兜底"实现。

### P1 渠道与插件生态

- 新增 `Discord` 渠道适配器（统一通过 `MessageBus`）：`src/channels/discord.py`
- Runtime 渠道初始化加入 Discord 开关：`src/runtime/brain.py`
- 插件市场最小闭环（管理 API）：
  - 列表：`GET /plugins/market`
  - 详情：`GET /plugins/market/{plugin_id}`
  - 安装：`POST /plugins/market/install`
  - 启停：`POST /plugins/market/toggle`
- 插件供应链安全（Loader 层）：
  - 完整性校验（`integrity` + SHA256）
  - 签名校验（轻量 HMAC，`signature.key_id/value` + trusted_keys）

### P2 训练系统深化（Lightning-lite -> 实验闭环）

- 新增 Sample Store（trajectory/eval 样本存储）：
  - `GET /debug/training-sample-stores`
  - `GET /debug/training-sample-stores/{store_id}`
  - `POST /debug/training-sample-stores/from-benchmark`
- 新增 Experiment（参数、运行、对比）：
  - `GET /debug/training-experiments`
  - `GET /debug/training-experiments/{experiment_id}`
  - `POST /debug/training-experiments`
  - `POST /debug/training-experiments/{experiment_id}/run`
  - `GET /debug/training-experiments/{experiment_id}/compare`
- 发布策略增强：
  - 发布支持 rollout 元数据（direct/canary）
  - Canary 条件下可按 gate 自动回滚
  - 新增 release promote：`POST /debug/training-releases/{release_id}/promote`

### P3 Soul 与人格一致性深化

- MentalProcess 配置化编辑（YAML + UI）：
  - `GET /debug/persona/mental-process`
  - `POST /debug/persona/mental-process`
  - 前端页：`web/src/pages/PersonaEval.jsx`
- Persona 一致性信号接入 release gate health 评估：
  - 阈值：`warning/critical_persona_consistency_score`
  - 信号出现在 `/debug/release-gate` 响应的 `health.signals`

### P4 部署治理与可观测

- Router 增加预算降级模式：
  - `budget_degrade_active`
  - 预算紧张时优先低成本路由
- 可观测新增趋势与告警：
  - `GET /observability/trends`
  - `GET /observability/alerts`
  - `DELETE /observability/alerts`
- 前端观测页增强：
  - 展示 budget degrade 状态、persona 信号、趋势与告警面板

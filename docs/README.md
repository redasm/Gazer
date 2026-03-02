# Gazer 文档中心（中文）

Gazer 是一个桌面级具身 AI 伴侣系统，采用"Agent + Tool + Memory + Soul + Device"分层架构。

本目录是项目文档入口，面向：

- 开源协作者快速上手
- 架构演进与运维落地
- 二次开发与模块扩展

## 为什么是 Gazer（项目特色）

- 双脑架构 + BrainHint 声明式路由：快脑保证响应，慢脑保证复杂任务质量，路由逻辑集中可控。
- 多 Agent 协同：复杂任务自动拆解为 DAG，Planner 规划、Worker 并行执行。
- 长期记忆系统：支持跨会话连续理解与召回。
- 人格引擎 Soul：保证风格一致、边界稳定。
- 自我进化闭环：评测、优化、训练、门禁联动。
- 具身硬件支持：可接入设备节点并执行物理动作。
- 卫星模式：支持多节点协同与远端能力扩展。

详见：[项目特色与优势](./features.md)

## 文档 Hub（按功能分组）

### 1) 快速开始（Getting Started）

- [总览](./getting-started/index.md)
- [安装与环境准备](./getting-started/installation.md)
- [启动模式与最小验证](./getting-started/running.md)

### 2) 架构（Architecture）

- [架构总览](./architecture/index.md)
- [Runtime 内核](./architecture/runtime.md)
- [双脑架构](./architecture/dual-brain.md)
- [多 Agent 协同架构](./architecture/multi-agent.md)
- [Agent 与 Tool 调用链](./architecture/agent-tools.md)
- [LLM 路由与部署治理](./architecture/llm-routing.md)
- [Memory 与 Soul](./architecture/memory-soul.md)
- [Bus 与 Channels](./architecture/bus-channels.md)

### 3) 功能模块（Modules）

- [模块索引](./modules/index.md)
- [Runtime 内核（Brain）](./modules/runtime-brain.md)
- [Agent 循环与调度](./modules/agent-loop.md)
- [多 Agent 协同系统](./modules/multi-agent.md)
- [Channel 与 MessageBus](./modules/channel-system.md)
- [Tools 与策略治理](./modules/tools-and-policy.md)
- [LLM 路由与降级](./modules/llm-routing.md)
- [Memory 系统（OpenViking）](./modules/memory-system.md)
- [Soul 系统（认知/情感/人格）](./modules/soul-system.md)
- [设备节点（Device Node）](./modules/device-node.md)
- [硬件抽象层（机械臂 / 球形显示）](./modules/hardware-abstraction.md)
- [卫星模式（Satellite Mode）](./modules/satellite-mode.md)
- [安全模型（Security）](./modules/security.md)
- [Web 管理台（Web Console）](./modules/web-console.md)
- [插件、技能与工作流](./modules/plugins-skills-workflows.md)

### 4) 参考（Reference）

- [参考索引](./reference/index.md)
- [配置参考（Config）](./reference/config.md)
- [管理 API 参考（Admin API）](./reference/admin-api.md)
- [工具策略参考（Tool Policy）](./reference/tool-policy.md)
- [MCP 集成参考](./reference/mcp.md)

### 5) 开发者（Development）

- [开发总览](./development/index.md)
- [开发工作流](./development/workflow.md)
- [测试与质量门禁](./development/testing.md)
- [文档规范](./development/docs-style.md)

### 6) 路线图（Roadmap）

- [开源与能力演进路线图](./roadmap/index.md)

## 最新能力增量（P1~P4）

- P1：新增 Discord 渠道、插件市场最小闭环、插件签名/完整性校验。
- P2：新增训练 Sample Store、Experiment、对比分析、Canary/Promote/回滚链路。
- P3：新增 MentalProcess YAML 编辑（API + UI），人格一致性信号接入门禁健康。
- P4：新增 Router 预算降级策略、Observability 趋势与告警 API + 页面展示。

详见：[项目特色与优势](./features.md)

## 文档设计原则

- 与当前代码结构严格对齐（`src/*`）。
- 按"概念 -> 指南 -> 参考"组织，降低学习成本。
- 支持后续接入 MkDocs/Docusaurus（稳定 URL、分层导航）。

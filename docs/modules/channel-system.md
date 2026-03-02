# Channel 与 MessageBus

Channel 层负责接入外部通信渠道，`MessageBus` 负责把 Channel 与 Agent 解耦。

## 关键代码

- 总线：`src/bus/queue.py`
- 事件模型：`src/bus/events.py`
- 通道抽象：`src/channels/base.py`
- 典型通道：`src/channels/web.py`、`src/channels/telegram.py`、`src/channels/discord.py`

## 设计原则

- Channel 只做协议适配与认证，不做智能决策。
- 所有入站必须调用 `MessageBus.publish_inbound()`。
- 所有出站通过 `subscribe_outbound()` 分发。
- 禁止 Channel 直接调用 Agent 内部方法。

## 入站链路

1. Channel 收到用户输入。
2. `ChannelAdapter.publish()` 执行 DM policy 与配对校验。
3. 封装 `InboundMessage` 后发布到 `MessageBus`。
4. AgentLoop 从 inbound 队列消费并处理。

## 出站链路

1. AgentLoop 产生 `OutboundMessage`。
2. `MessageBus.publish_outbound()` 入队。
3. `dispatch_outbound()` 将消息发给订阅该 channel 的 `send()`。

## DM Policy

`ChannelAdapter` 内置三种策略：

- `open`：默认放行
- `allowlist`：仅已批准 sender 可用
- `pairing`：未知 sender 返回配对码，管理员审批后放行

Owner sender 可跨策略直通。

## 总线防护能力

- 入站会话限流：默认 60 秒最多 30 条。
- 出站发送失败重试：指数退避。
- typing 事件独立订阅与分发。

## 排障建议

- 收到消息但无回复：看 inbound 队列增长与 Agent 消费状态。
- 某渠道收不到出站：检查该 channel 是否已 `bind()` 且 dispatcher 在运行。
- 频繁被拒绝：检查 `dm_policy` 与 pairing 审批状态。

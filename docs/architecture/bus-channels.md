# Bus 与 Channels

消息总线位于 `src/bus/queue.py`，通道适配器位于 `src/channels/`。

## 设计目标

- 多通道统一接入（Web/Telegram/Feishu/...）
- 降低通道层与 Agent 的耦合
- 统一消息结构、统一观测点

## MessageBus 能力

- 入站消息发布：`publish_inbound`
- 出站消息发布：`publish_outbound`
- 出站订阅：`subscribe_outbound`
- 打字事件：`publish_typing` / `subscribe_typing`

## ChannelAdapter 约束

- 仅负责协议适配与消息转换
- 不直接实现业务决策逻辑
- 不绕过总线直接调用 Agent

## 常见问题与建议

- **问题**：通道里塞入业务判断，长期变成隐式分叉。
- **建议**：通道层只做 I/O，策略统一回收至 Agent/Runtime。


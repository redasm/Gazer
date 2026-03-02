# 启动模式与最小验证

## 1. 后端启动

标准运行：

```bash
python main.py
```

CLI 模式：

```bash
python main.py --cli
```

## 2. Web 前端启动

```bash
cd web
npm run dev
```

## 3. 健康检查

后端提供基础健康检查接口：

```bash
GET /health
```

如果返回 `200` 且日志无持续异常，即可进入下一步验证。

## 4. 最小链路验证

建议按以下顺序验证：

1. 打开 Web 配置页并读取配置。
2. 发起一次普通对话消息。
3. 触发一次低风险工具调用（如只读查询）。
4. 查看日志中是否出现完整请求链路。

## 5. 回归测试建议

定向：

```bash
pytest -q tests/test_security_regressions.py
pytest -q tests/test_tools_and_skills.py
```

全量：

```bash
pytest -q
```


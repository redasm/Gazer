# Flowise 互操作（最小子集）

## 支持方向

- `POST /workflows/flowise/import`：Flowise -> Gazer
- `POST /workflows/flowise/export`：Gazer -> Flowise-compatible JSON

## 节点映射（最小子集）

- `chatInput` / `start` / `input` -> `input`
- `chatPromptTemplate` / `prompt*` / `llmChain` -> `prompt`
- `tool*` / `serpapi` / `webSearch` -> `tool`
- `ifElse` / `condition*` -> `condition`
- `chatOutput` / `output` / `answer` -> `output`

## 关键字段映射

- Prompt:
  - Flowise `data.inputs.template` -> Gazer `config.prompt`
- Tool:
  - Flowise `data.inputs.toolName` -> Gazer `config.tool_name`
  - 默认参数模板按工具类型补齐（如 `web_search.q={{prev}}`）
- Condition:
  - Flowise `data.inputs.operator/value` -> Gazer `config.operator/value`
- Output:
  - Flowise `data.inputs.text` -> Gazer `config.text`
- Edge:
  - `source/target` 直映射
  - `label in {true,false,default}` -> Gazer `when`

## 错误与降级策略

- `strict=true`：遇到不支持节点/边立即 `400`，并返回节点级 `errors` 列表。
- `strict=false`：跳过不支持节点与非法边，返回 `error_count/errors`，其余可运行节点继续导入。

## 不支持特性（当前）

- Flowise 自定义 JS/函数节点的执行语义（仅做结构映射，不执行其内部逻辑）。
- 多模型 chain 细粒度 provider 参数（仅保留为 Gazer `prompt/tool/condition/output` 基元）。
- Flowise 特定 memory/vector store/retriever 复杂节点（默认记为不支持并返回节点级错误）。

# 插件与技能

该模块负责”可扩展能力”两件事：插件（能力注入）、技能（提示词知识包）。

## 关键代码

- 插件加载：`src/plugins/loader.py`
- 插件清单：`src/plugins/manifest.py`
- 技能加载：`src/skills/loader.py`
- Brain 装配点：`src/runtime/brain.py`（`_setup_tools`）

## 插件（Plugin）

### 发现优先级

1. `<workspace>/extensions`
2. `~/.gazer/extensions`
3. `core/extensions`（仓库内置）

### 安全校验

- 完整性校验（`integrity` 文件哈希）
- 签名校验（可配置 enforce/allow_unsigned）
- 校验失败插件不会加载，并进入 failed 列表

### 加载过程

- `discover()` 解析 `gazer_plugin.yaml`
- `load_all()` 校验配置、创建 `PluginAPI`、调用入口函数

## 技能（Skill）

- Skill 本体为 `SKILL.md`。
- `SkillLoader` 先读取 frontmatter 元数据，按需加载正文（渐进披露）。
- 可将技能目录注入系统提示词 `<available_skills>`，供 Agent 按任务选择。

## 实战建议

- 需要新增工具能力：优先插件化，而不是改 Agent 核心。
- 需要跨任务可复用经验：优先抽成 Skill，并保持说明可执行。

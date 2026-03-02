# AGENTS.md 层级继承规则

## 解析范围

- 以 `workspace` 为根目录。
- 从 `workspace` 到 `target_dir` 逐级查找 `AGENTS.md`。
- 命中的文件按“由浅到深”聚合到系统提示中。

## 覆盖规则

- 普通文本：按层级顺序拼接，便于追溯来源。
- `skills_priority`：子目录声明会覆盖父目录优先级。
  - 格式示例：`skills_priority: skillA, skillB`
  - 若未声明该字段，继承父级优先级。

## 调试输出

- ContextBuilder 提供：
  - `get_agents_debug()`：每层命中与覆盖信息
  - `get_skill_priority()`：最终生效的 skill 优先级列表

## 兼容说明

- 仍支持在 AGENTS 正文中通过 `$SkillName` 标记技能（作为优先级推断回退）。
- 未命中任何 `AGENTS.md` 时，行为退化为原有 bootstrap 逻辑。

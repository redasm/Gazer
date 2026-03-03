# OpenViking 记忆后端启动要求

本页说明在新分支中启用 OpenViking 记忆后端的最小前置条件。

## 1. 依赖安装

```bash
pip install -e .
```

确认 `openviking` 可导入：

```bash
python -c "import openviking; print('ok')"
```

## 2. 配置项

`config/settings.yaml` 中使用以下配置组：

```yaml
memory:
  context_backend:
    enabled: true
    mode: openviking
    data_dir: data/openviking
    config_file: ""   # 可选，填 ov.conf 绝对/相对路径
    session_prefix: gazer
    default_user: owner
    commit_every_messages: 8
```

说明：

- `enabled=true` 时，启动将执行 OpenViking 前置检查。
- `config_file` 非空时必须存在，否则启动失败。

## 2.1 默认存储位置（本分支）

当前分支默认将记忆相关状态文件写入 `data/openviking`，不再默认写入 `assets/memory`。

包括：

- `memory_events.jsonl`
- `extraction_decisions.jsonl`
- `RELATIONSHIPS.*`
- `pairing*.json/.lock`
- `evolution_history.jsonl`
- `emotions/`

## 3. 启动失败行为

若缺少依赖或配置无效，系统在启动阶段直接报错并终止，不会静默降级到旧记忆后端。

典型错误：

- `openviking` 未安装
- `memory.context_backend.mode` 非法
- `memory.context_backend.config_file` 指向不存在文件

## 4. 旧数据迁移

提供一次性迁移脚本，将 `assets/memory/events/*.md` 和 `assets/memory/knowledge/**/*.md` 导入 OpenViking 存储。

```bash
# 先预演（不写入）
python -m memory.migration_to_openviking --dry-run

# 执行真实迁移
python -m memory.migration_to_openviking
```

输出报告默认写入：

- dry-run: `data/openviking/migration_report_dry_run.json`
- execute: `data/openviking/migration_report.json`

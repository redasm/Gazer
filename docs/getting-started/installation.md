# 安装与环境准备

## 1. 前置条件

- Python `3.10+`
- Node.js `18+`（如果需要 `web/` 前端）
- Windows/macOS/Linux 任一

## 2. Python 虚拟环境

```bash
python -m venv .venv
```

Windows:

```bash
.venv\Scripts\activate
```

macOS / Linux:

```bash
source .venv/bin/activate
```

## 3. 安装后端依赖

```bash
pip install -r requirements.txt
pip install -e ".[dev,perception,ui,browser]"
```

## 4. 安装前端依赖（可选）

```bash
cd web
npm install
```

## 5. 配置准备

- 复制环境变量模板：`.env.example` → `.env`
- 核对运行配置：`config/settings.yaml`
- 生产环境禁止将真实密钥提交到仓库

## 6. 安装后检查

```bash
python -c "import fastapi, pydantic; print('ok')"
pytest -q tests/test_security_regressions.py
```


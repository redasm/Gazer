# 安全修复执行报告

**执行日期**: 2026-03-01  
**修复版本**: v0.2.0-security-hardening

---

## ✅ 已完成的高危修复 (Critical - 已实施)

### 1. ✅ 加密敏感数据存储 (owner.json)
**问题**: `config/owner.json` 中的 `admin_token` 以明文存储

**修复内容**:
- 新建 `src/security/file_crypto.py` - AES-256-GCM 加密模块
- 修改 `src/security/owner.py` 集成加密存储
- 自动迁移现有明文 owner.json 到加密格式
- 添加 `cryptography>=42.0.0` 依赖到 pyproject.toml

**特性**:
- 机器特征派生密钥 (Windows UUID/处理器/主机名)
- 开发环境可降级到明文 (配置 `GAZER_ENV=dev`)
- 生产环境强制加密 (需安装 cryptography 库)

**使用**:
```bash
# 安装依赖
pip install cryptography

# 自动迁移 (首次运行时)
python main.py
```

**后续行动**:
- [ ] 在生产服务器运行一次完成迁移
- [ ] 备份旧的 `config/owner.json.plaintext.bak`
- [ ] 验证加密后的文件权限为 0600

---

### 2. ✅ 移除 Loopback 认证豁免
**问题**: 本地回环请求可绕过认证,存在 SSRF/本地攻击风险

**修复内容**:
- 修改 `src/tools/admin/auth.py`:
  - `_allow_local_auth_bypass()` 函数永久返回 `False`
  - 移除 HTTP 和 WebSocket 的 loopback bypass 逻辑
- 修改 `src/runtime/config_manager.py`:
  - 移除 `allow_loopback_without_token` 配置
  - 移除 `local_bypass_environments` 配置

**影响**:
- ⚠️ **所有请求现在都需要有效的 admin_token 或 session cookie**
- 开发环境也需要通过 `/auth/session` 创建会话
- 自动化脚本需要在请求头中携带 Token

**开发流程调整**:
```bash
# 获取 admin_token
cat config/owner.json

# 方法 1: 使用 Bearer Token (API 调用)
curl -H "Authorization: Bearer <admin_token>" http://localhost:8080/api/status

# 方法 2: 创建 Session Cookie (Web UI)
curl -X POST http://localhost:8080/auth/session \
  -H "Content-Type: application/json" \
  -d '{"token": "<admin_token>"}'
```

---

### 3. ✅ 增强 CORS 配置安全
**问题**: 动态 CORS 配置可能被绕过,缺少 CSRF 保护

**修复内容**:
- 新增配置项:
  - `api.cors_strict_mode`: 默认 `true`,拒绝通配符 origin
  - `api.require_origin_for_mutations`: 默认 `true`,状态变更操作强制 Origin 头
- 修改 `src/tools/admin/auth.py`:
  - `_is_allowed_origin()` 增强验证逻辑
  - `verify_admin_token()` 对 POST/PUT/DELETE/PATCH 强制验证 Origin

**安全增强**:
- ✅ 拒绝 `cors_origins: ["*"]` 配置 (strict mode)
- ✅ 缺少 Origin 头的状态变更请求被拒绝
- ✅ 精确匹配 origin (不支持子域通配符)

**生产环境配置** (config/settings.yaml):
```yaml
api:
  cors_origins:
    - "https://admin.yourdomain.com"  # 替换为实际域名
  cors_strict_mode: true
  require_origin_for_mutations: true
  cors_credentials: true  # 仅当 origin 为 HTTPS 时
```

---

## 📋 待执行修复 (High Priority - 需手动处理)

### 4. ⏳ IPC 队列安全性增强
**问题**: `multiprocessing.Queue` 无认证,可被本地恶意进程劫持

**建议方案** (需要您选择):

**方案 A**: HMAC 签名验证 (最简单)
- 在队列消息中添加 HMAC-SHA256 签名
- 使用共享密钥 (从 `owner.json` 加密存储中读取)

**方案 B**: 加密 IPC (最安全)
- 改用 Unix Domain Socket (Linux/Mac) 或 Named Pipe (Windows)
- 每条消息用 AES-GCM 加密

**实施复杂度**:
- 方案 A: ~2小时 (修改 main.py 和 admin_api.py)
- 方案 B: ~6小时 (重构 IPC 架构)

---

### 5. ⏳ 配置脱敏机制完善
**问题**: API 响应和日志可能泄露敏感配置

**待实施**:
1. 在所有 `/config` 相关 API 返回前强制脱敏
2. 集成到 `logging.Handler`,自动脱敏日志输出
3. 更新 `_SENSITIVE_KEY_PATTERNS` 包含所有敏感字段

**文件位置**:
- `src/runtime/config_manager.py` (脱敏逻辑)
- `src/tools/admin/config_routes.py` (API 端点)

---

### 6. ⏳ Session 刷新机制 (Refresh Token)
**当前问题**: Session 过期后需要重新完整认证

**待实施**:
- 短寿命 Access Token (15分钟)
- 长寿命 Refresh Token (7天)
- Rotating Refresh Token (单次使用后作废)

**参考实现**: OAuth 2.0 Refresh Token Flow

---

## 📈 中长期优化 (Medium Priority)

### 7. 配置热重载保护
- 配置版本号和变更审计
- Schema 验证 (Pydantic 模型)
- 两阶段提交 (Staging → Apply)

### 8. 依赖版本锁定
- 生成 `requirements.lock` 文件
- CI/CD 集成依赖安全扫描 (`pip-audit`/`safety`)

### 9. 插件安全机制
- 默认启用签名验证 (`plugins.signature.enforce: true`)
- 插件沙箱 (独立进程 + 资源限制)
- 工具调用超时保护

---

## 🧪 验证与测试

### 测试加密存储迁移
```bash
# 备份现有配置
cp config/owner.json config/owner.json.backup

# 启动应用触发迁移
python main.py

# 验证文件格式 (应包含 "encrypted": true)
cat config/owner.json

# 验证应用正常工作
curl -H "Authorization: Bearer $(jq -r .admin_token config/owner.json.backup)" \
  http://localhost:8080/api/status
```

### 测试认证强化
```bash
# 应该失败 (401)
curl http://localhost:8080/api/status

# 应该成功
curl -H "Authorization: Bearer <your_token>" http://localhost:8080/api/status
```

### 测试 CORS 安全
```bash
# 模拟不允许的 origin (应该失败)
curl -X POST http://localhost:8080/config \
  -H "Origin: https://evil.com" \
  -H "Authorization: Bearer <token>" \
  -d '{}'

# 允许的 origin (应该成功)
curl -X POST http://localhost:8080/config \
  -H "Origin: http://localhost:5173" \
  -H "Authorization: Bearer <token>" \
  -d '{}'
```

---

## ⚠️ 兼容性影响

### Breaking Changes
1. ✅ **本地开发环境也需要认证**
   - 影响: 所有 API 调用脚本
   - 解决: 在请求中添加 Token 或使用 Session Cookie

2. ✅ **config/owner.json 格式变更**
   - 影响: 手动编辑 owner.json 的工具
   - 解决: 使用 API 端点修改或等待自动迁移

### 非破坏性变更
- 配置新增字段 (有默认值,向后兼容)
- 新增 cryptography 依赖 (可选,开发环境可降级)

---

## 📞 问题排查

### 问题 1: "cryptography library required"
```bash
# 解决: 安装加密库
pip install cryptography
```

### 问题 2: "Authentication required" (本地开发)
```bash
# 获取 Token
jq -r .admin_token config/owner.json

# 或创建 Session
curl -X POST http://localhost:8080/auth/session \
  -H "Content-Type: application/json" \
  -d "{\"token\": \"$(jq -r .admin_token config/owner.json)\"}" \
  -c cookies.txt

# 后续请求使用 cookie
curl -b cookies.txt http://localhost:8080/api/status
```

### 问题 3: "Origin not allowed"
```bash
# 检查配置
cat config/settings.yaml | grep -A 5 cors_origins

# 添加您的前端域名到 cors_origins
```

---

## 📚 相关文档

- [SECURITY_ALERT.md](./SECURITY_ALERT.md) - 密钥泄露处理指南
- [src/security/file_crypto.py](./src/security/file_crypto.py) - 加密实现细节
- [src/tools/admin/auth.py](./src/tools/admin/auth.py) - 认证实现

---

**安全审计人员**: Oz (Warp AI Agent)  
**审核标准**: 生产级安全 (OWASP Top 10)  
**后续审计**: 建议 3 个月后复审

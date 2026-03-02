# 🚨 安全警告 - 需要立即处理

## 泄露的 API 密钥已清理

**日期**: 2026-03-01

### 受影响的密钥

以下 API 密钥已从 `.env` 文件中移除,但**必须在对应服务商后台撤销并重新生成**:

1. **DashScope API Key** (阿里云百炼)
   - 已泄露密钥前缀: `sk-85a9b32354...`
   - 操作: 登录 [阿里云百炼控制台](https://dashscope.console.aliyun.com/) 撤销并重新生成

2. **GMN API Key**
   - 已泄露密钥前缀: `sk-a8029b7175...`
   - 操作: 联系 GMN 服务商撤销密钥

3. **Feishu App Secret** (飞书应用密钥)
   - 已泄露 App ID: `cli_a903efa4f9b81cd1`
   - 已泄露 Secret 前缀: `SZydlSxmRZ...`
   - 操作: 登录 [飞书开放平台](https://open.feishu.cn/) 重置应用凭证

### 后续操作

#### 1. 立即撤销密钥 (0-2小时)
- [ ] 在 DashScope 控制台撤销旧密钥
- [ ] 在 GMN 服务商撤销旧密钥
- [ ] 在飞书开放平台重置应用凭证
- [ ] 检查这些服务的账单,确认是否有未授权使用

#### 2. 更新本地配置 (2-4小时)
- [ ] 复制 `.env.example` 为 `.env`
- [ ] 填写新生成的密钥到 `.env` 文件
- [ ] 确认 `.env` 不会被提交到 Git (已在 .gitignore 中)

#### 3. 清理 Git 历史 (可选,推荐)
如果此代码仓库已推送到远程(如 GitHub),需要清理历史记录:

```bash
# 使用 BFG Repo-Cleaner (推荐)
# 下载: https://rtyley.github.io/bfg-repo-cleaner/
java -jar bfg.jar --replace-text passwords.txt

# 或使用 git-filter-repo (需安装)
git filter-repo --invert-paths --path .env

# 强制推送 (⚠️ 会覆盖远程历史,团队需同步)
git push origin --force --all
git push origin --force --tags
```

创建 `passwords.txt` 文件(用于 BFG):
```
sk-85a9b32354ba4e2bbf2225b725bfde71===>REDACTED_DASHSCOPE_KEY
sk-a8029b71754233166d0bf5a156511b02cce49fd15a18e3199723ed2c617f432b===>REDACTED_GMN_KEY
===>REDACTED_FEISHU_SECRET
cli_a903efa4f9b81cd1===>REDACTED_FEISHU_APP_ID
```

#### 4. 安全加固 (1周内)
参考 [安全修复计划](./SECURITY_FIXES.md) 完成后续加固:
- [ ] 加密 `config/owner.json` 中的 admin_token
- [ ] 移除 Loopback 认证豁免
- [ ] 修复 CORS 配置
- [ ] 增强 IPC 队列安全性

### 预防措施

为避免未来再次泄露密钥:

1. **使用密钥管理服务** (生产环境)
   - Windows: Windows Credential Manager
   - macOS: Keychain
   - Linux: Secret Service API / Pass
   - 云平台: AWS Secrets Manager, Azure Key Vault, HashiCorp Vault

2. **CI/CD 密钥检测**
   - 安装 pre-commit hook 检测密钥泄露
   - 使用 [git-secrets](https://github.com/awslabs/git-secrets) 或 [gitleaks](https://github.com/zricethezav/gitleaks)

3. **最小权限原则**
   - 为每个环境生成独立的 API 密钥
   - 开发环境使用受限配额的测试密钥

### 受影响分析

如果这些密钥已被未授权使用,可能导致:
- ✅ **已缓解**: API 调用费用增加
- ✅ **已缓解**: 飞书应用发送垃圾消息
- ⚠️ **需监控**: 敏感数据泄露(如果 AI 对话涉及机密信息)

### 联系信息

如有疑问或发现安全问题,请联系:
- 邮件: security@yourdomain.com (请替换为实际邮箱)
- Issue: 在此仓库创建 Security Issue (private)

---

**⚠️ 重要**: 请不要将此文档提交到公开仓库。完成处理后可删除。

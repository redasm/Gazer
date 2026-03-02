# Gazer Web Onboarding Guide

This guide is for users who only operate Gazer from the web console.

## 1. Configure LLM Provider and Model

1. Open `System Config -> Onboarding`.
2. Fill `Provider` and `Model` first.
3. If provider is remote, fill `Base URL` and `API Key`.
4. Click `Validate Draft` and confirm there is no blocking error.

## 2. Enable at Least One Channel

1. Enable one or more channels:
- Telegram
- Feishu / Lark
- Discord
2. Fill channel credentials:
- Telegram: token
- Feishu: app_id + app_secret
- Discord: token
3. Fill allowlist IDs (csv) for production safety.

## 3. Apply Security Baseline

1. Set `DM Policy` to `pairing` or `allowlist`.
2. Set `Tool Max Tier` to `standard` (or `safe` for strict mode).
3. Fill `Owner Channel IDs` with JSON map, for example:

```json
{
  "telegram": "123456",
  "feishu": "ou_xxx",
  "discord": "9876543210"
}
```

Notes:
- `security.auto_approve_privileged` is protected and intentionally not writable from onboarding APIs.
- Keep privileged auto-approval disabled in production.

## 4. Validate and Apply

1. Click `Validate Draft` to get issue list and fix suggestions.
2. Resolve `error` level issues first.
3. Click `Apply Wizard` to persist changes.
4. Refresh and confirm all onboarding steps are marked `Completed`.

## 5. Operational Checklist

- Owner mapping is configured.
- At least one channel is enabled and tested.
- LLM provider has reachable endpoint and model set.
- Security baseline score is acceptable in validation report.

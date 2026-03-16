"""Structured security audit for Gazer configuration.

Produces a :class:`SecurityAuditReport` with typed findings, each carrying a
stable ``check_id``, ``severity``, human-readable ``title`` / ``detail``, and
an optional ``remediation`` string.

Inspired by OpenClaw's ``security/audit.ts``.

Usage::

    from runtime.config_manager import config
    from security.audit import run_security_audit

    report = run_security_audit(config)
    for f in report.findings:
        print(f.severity.upper(), f.check_id, f.title)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

# ---------------------------------------------------------------------------
# Finding / Report data types
# ---------------------------------------------------------------------------

SecurityAuditSeverity = Literal["info", "warn", "critical"]


@dataclass
class SecurityAuditFinding:
    """One security finding produced by the audit."""

    check_id: str
    """Stable dot-separated identifier, e.g. ``gateway.bind_no_auth``."""

    severity: SecurityAuditSeverity
    title: str
    detail: str
    remediation: Optional[str] = None


@dataclass
class SecurityAuditSummary:
    critical: int = 0
    warn: int = 0
    info: int = 0


@dataclass
class SecurityAuditReport:
    ts: float = field(default_factory=time.time)
    summary: SecurityAuditSummary = field(default_factory=SecurityAuditSummary)
    findings: List[SecurityAuditFinding] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _norm_str(value: object) -> str:
    return str(value or "").strip()


def _count_by_severity(findings: List[SecurityAuditFinding]) -> SecurityAuditSummary:
    s = SecurityAuditSummary()
    for f in findings:
        if f.severity == "critical":
            s.critical += 1
        elif f.severity == "warn":
            s.warn += 1
        else:
            s.info += 1
    return s


# ---------------------------------------------------------------------------
# Check: gateway binding + auth
# ---------------------------------------------------------------------------

def _check_gateway(cfg: Any) -> List[SecurityAuditFinding]:
    findings: List[SecurityAuditFinding] = []

    bind = _norm_str(cfg.get("gateway.bind", "loopback") or "loopback")
    token = _norm_str(cfg.get("gateway.auth.token", "") or "")
    password = _norm_str(cfg.get("gateway.auth.password", "") or "")
    auth_mode = _norm_str(cfg.get("gateway.auth.mode", "") or "")
    has_auth = bool(token or password or auth_mode == "trusted-proxy")

    if bind not in ("loopback", "", "127.0.0.1", "::1") and not has_auth:
        findings.append(SecurityAuditFinding(
            check_id="gateway.bind_no_auth",
            severity="critical",
            title="Gateway binds beyond loopback without auth",
            detail=f'gateway.bind="{bind}" but no gateway.auth token/password is configured.',
            remediation="Set gateway.auth.token (recommended) or restrict gateway.bind to loopback.",
        ))

    if token and len(token) < 24:
        findings.append(SecurityAuditFinding(
            check_id="gateway.token_too_short",
            severity="warn",
            title="Gateway auth token looks short",
            detail=f"gateway.auth.token is {len(token)} chars; prefer a random token of at least 32 chars.",
            remediation="Regenerate the gateway token with a cryptographically secure random string.",
        ))

    rate_limit = cfg.get("gateway.auth.rate_limit", None)
    if bind not in ("loopback", "", "127.0.0.1", "::1") and auth_mode != "trusted-proxy" and not rate_limit:
        findings.append(SecurityAuditFinding(
            check_id="gateway.auth_no_rate_limit",
            severity="warn",
            title="No auth rate limiting configured",
            detail="gateway.bind is non-loopback but no gateway.auth.rate_limit is configured.",
            remediation="Set gateway.auth.rate_limit (e.g. {max_attempts: 10, window_seconds: 60}).",
        ))

    return findings


# ---------------------------------------------------------------------------
# Check: rate limiter
# ---------------------------------------------------------------------------

def _check_rate_limiter(cfg: Any) -> List[SecurityAuditFinding]:
    findings: List[SecurityAuditFinding] = []

    max_req = cfg.get("security.rate_limit_requests", None)
    window = cfg.get("security.rate_limit_window", None)

    if max_req is None and window is None:
        findings.append(SecurityAuditFinding(
            check_id="security.rate_limiter_unconfigured",
            severity="info",
            title="Per-sender rate limiter uses defaults",
            detail="security.rate_limit_requests and security.rate_limit_window are not set; defaults apply (20 req / 60 s).",
            remediation="Explicitly set security.rate_limit_requests and security.rate_limit_window if you need tighter control.",
        ))

    try:
        if max_req is not None and int(max_req) > 100:
            findings.append(SecurityAuditFinding(
                check_id="security.rate_limit_very_high",
                severity="warn",
                title="Per-sender rate limit is very permissive",
                detail=f"security.rate_limit_requests={max_req} allows very high request rates per sender.",
                remediation="Consider lowering security.rate_limit_requests to 20–50 for production.",
            ))
    except (TypeError, ValueError):
        pass

    return findings


# ---------------------------------------------------------------------------
# Check: dangerous / insecure config flags
# ---------------------------------------------------------------------------

_DANGEROUS_FLAGS: List[tuple[str, str]] = [
    ("gateway.control_ui.allow_insecure_auth", "Control UI insecure auth flag enabled"),
    ("gateway.control_ui.dangerously_allow_host_header_origin_fallback",
     "DANGEROUS: Host-header origin fallback enabled (weakens DNS rebinding protection)"),
    ("gateway.control_ui.dangerously_disable_device_auth",
     "DANGEROUS: Control UI device auth disabled"),
]


def _check_dangerous_flags(cfg: Any) -> List[SecurityAuditFinding]:
    findings: List[SecurityAuditFinding] = []
    enabled: List[str] = []
    for key, desc in _DANGEROUS_FLAGS:
        if cfg.get(key, False) is True:
            enabled.append(f"{key}=true ({desc})")
    if enabled:
        findings.append(SecurityAuditFinding(
            check_id="config.dangerous_flags",
            severity="warn",
            title="Insecure or dangerous config flags enabled",
            detail=f"Detected {len(enabled)} enabled flag(s):\n" + "\n".join(f"  - {e}" for e in enabled),
            remediation="Disable these flags when not actively debugging or testing.",
        ))
    return findings


# ---------------------------------------------------------------------------
# Check: plaintext secrets in config
# ---------------------------------------------------------------------------

_SECRET_KEYS = (
    "telegram.token", "discord.token", "email.password",
    "feishu.app_secret", "gateway.auth.token", "gateway.auth.password",
)

_SECRET_PREFIXES = (
    "bot", "AAAAAA", "xoxb-", "xoxp-", "ghp_", "sk-",
)


def _looks_like_plaintext_secret(value: object) -> bool:
    if not isinstance(value, str):
        return False
    s = value.strip()
    if len(s) < 8:
        return False
    # SecretRef patterns (env:, file:, op://) are safe
    if s.startswith(("env:", "file:", "op://", "${", "%(")):
        return False
    # Looks like a real token if it starts with a known prefix or is long
    if any(s.startswith(p) for p in _SECRET_PREFIXES):
        return True
    if len(s) >= 32 and s.replace("-", "").replace("_", "").isalnum():
        return True
    return False


def _check_plaintext_secrets(cfg: Any) -> List[SecurityAuditFinding]:
    findings: List[SecurityAuditFinding] = []
    hits: List[str] = []
    for key in _SECRET_KEYS:
        val = cfg.get(key, None)
        if _looks_like_plaintext_secret(val):
            hits.append(key)
    if hits:
        findings.append(SecurityAuditFinding(
            check_id="config.plaintext_secrets",
            severity="warn",
            title="Possible plaintext secrets detected in config",
            detail=f"Keys that may contain plaintext credentials: {', '.join(hits)}.",
            remediation="Use SecretRef syntax (e.g. env:MY_TOKEN) to keep secrets out of settings.yaml.",
        ))
    return findings


# ---------------------------------------------------------------------------
# Check: tools / safe_eval policy
# ---------------------------------------------------------------------------

def _check_tools_policy(cfg: Any) -> List[SecurityAuditFinding]:
    findings: List[SecurityAuditFinding] = []

    safe_eval_enabled = cfg.get("tools.safe_eval.enabled", True)
    if safe_eval_enabled is False:
        findings.append(SecurityAuditFinding(
            check_id="tools.safe_eval_disabled",
            severity="critical",
            title="Safe-eval sandbox disabled",
            detail="tools.safe_eval.enabled=false removes expression sandboxing for eval-based tools.",
            remediation="Re-enable tools.safe_eval (remove the flag or set it to true).",
        ))

    exec_enabled = cfg.get("tools.exec.enabled", False)
    if exec_enabled:
        exec_approval = cfg.get("tools.exec.require_approval", True)
        if not exec_approval:
            findings.append(SecurityAuditFinding(
                check_id="tools.exec.approval_disabled",
                severity="critical",
                title="Shell exec tool enabled without approval requirement",
                detail="tools.exec.enabled=true and tools.exec.require_approval is not true.",
                remediation="Set tools.exec.require_approval=true or disable tools.exec.",
            ))

    return findings


# ---------------------------------------------------------------------------
# Check: logging / redaction
# ---------------------------------------------------------------------------

def _check_logging(cfg: Any) -> List[SecurityAuditFinding]:
    findings: List[SecurityAuditFinding] = []
    redact = _norm_str(cfg.get("logging.redact_sensitive", "tools") or "tools")
    if redact == "off":
        findings.append(SecurityAuditFinding(
            check_id="logging.redact_off",
            severity="warn",
            title="Tool-summary redaction is disabled",
            detail='logging.redact_sensitive="off" can leak secrets into logs and status output.',
            remediation='Set logging.redact_sensitive="tools" (default).',
        ))
    return findings


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_security_audit(cfg: Any) -> SecurityAuditReport:
    """Run all built-in security checks against *cfg* and return a report.

    *cfg* may be any object that supports ``.get(key, default)`` (e.g. a
    :class:`~runtime.config_manager.ConfigManager` instance or a plain dict).
    """
    findings: List[SecurityAuditFinding] = []
    findings.extend(_check_gateway(cfg))
    findings.extend(_check_rate_limiter(cfg))
    findings.extend(_check_dangerous_flags(cfg))
    findings.extend(_check_plaintext_secrets(cfg))
    findings.extend(_check_tools_policy(cfg))
    findings.extend(_check_logging(cfg))

    summary = _count_by_severity(findings)
    return SecurityAuditReport(ts=time.time(), summary=summary, findings=findings)

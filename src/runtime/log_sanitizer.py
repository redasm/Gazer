"""Logging filter to automatically sanitize sensitive data from log records.

Prevents accidental leakage of API keys, tokens, and passwords in log files.
"""

import logging
import re
from typing import Pattern, List

# Patterns to detect and redact sensitive data
_SENSITIVE_PATTERNS: List[tuple[Pattern, str]] = [
    # API Keys (various formats)
    (re.compile(r'(api[_-]?key["\']?\s*[:=]\s*["\']?)([a-zA-Z0-9_\-]{20,})(["\']?)', re.IGNORECASE), r'\1***REDACTED***\3'),
    (re.compile(r'(sk-[a-zA-Z0-9]{32,})', re.IGNORECASE), r'sk-***REDACTED***'),
    
    # Tokens
    (re.compile(r'(token["\']?\s*[:=]\s*["\']?)([a-zA-Z0-9_\-\.]{20,})(["\']?)', re.IGNORECASE), r'\1***REDACTED***\3'),
    (re.compile(r'(bearer\s+)([a-zA-Z0-9_\-\.]{20,})', re.IGNORECASE), r'\1***REDACTED***'),
    
    # Passwords
    (re.compile(r'(password["\']?\s*[:=]\s*["\']?)([^"\'\s]{4,})(["\']?)', re.IGNORECASE), r'\1***REDACTED***\3'),
    (re.compile(r'(pwd["\']?\s*[:=]\s*["\']?)([^"\'\s]{4,})(["\']?)', re.IGNORECASE), r'\1***REDACTED***\3'),
    
    # Secrets
    (re.compile(r'(secret["\']?\s*[:=]\s*["\']?)([a-zA-Z0-9_\-]{8,})(["\']?)', re.IGNORECASE), r'\1***REDACTED***\3'),
    
    # Authorization headers
    (re.compile(r'(authorization:\s*)([a-zA-Z0-9_\-\.=+/]{20,})', re.IGNORECASE), r'\1***REDACTED***'),
    
    # JWT tokens (3 base64 segments separated by dots)
    (re.compile(r'\b([a-zA-Z0-9_-]{20,})\.([a-zA-Z0-9_-]{20,})\.([a-zA-Z0-9_-]{20,})\b'), r'***JWT_REDACTED***'),
    
    # Email addresses (optional - uncomment if needed)
    # (re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'), r'***EMAIL***'),
]


class SensitiveDataFilter(logging.Filter):
    """Logging filter that redacts sensitive data from log records.
    
    Usage:
        # Add to root logger
        logging.getLogger().addFilter(SensitiveDataFilter())
        
        # Or add to specific handler
        handler = logging.StreamHandler()
        handler.addFilter(SensitiveDataFilter())
    """
    
    def __init__(self, name: str = ""):
        super().__init__(name)
        self.patterns = _SENSITIVE_PATTERNS
    
    def filter(self, record: logging.LogRecord) -> bool:
        """Sanitize the log record message.
        
        Returns:
            True to allow the record to pass (after sanitization)
        """
        # Sanitize the main message
        if hasattr(record, 'msg') and isinstance(record.msg, str):
            record.msg = self._sanitize(record.msg)
        
        # Sanitize arguments (if any)
        if hasattr(record, 'args') and record.args:
            if isinstance(record.args, tuple):
                record.args = tuple(
                    self._sanitize(str(arg)) if isinstance(arg, str) else arg
                    for arg in record.args
                )
            elif isinstance(record.args, dict):
                record.args = {
                    k: self._sanitize(str(v)) if isinstance(v, str) else v
                    for k, v in record.args.items()
                }
        
        return True
    
    def _sanitize(self, text: str) -> str:
        """Apply all redaction patterns to the text."""
        result = text
        for pattern, replacement in self.patterns:
            result = pattern.sub(replacement, result)
        return result


def install_log_sanitizer(*, also_on_root: bool = True) -> None:
    """Install the sensitive data filter on logging handlers.
    
    Args:
        also_on_root: If True, also add filter to root logger
    """
    filter_instance = SensitiveDataFilter()
    
    # Add to all existing handlers
    for handler in logging.root.handlers:
        handler.addFilter(filter_instance)
    
    # Add to root logger if requested
    if also_on_root:
        logging.root.addFilter(filter_instance)
    
    logging.info("Log sanitization filter installed")


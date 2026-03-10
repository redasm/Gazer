"""Coding tools: safety.

Extracted from coding.py.
"""

import re
from pathlib import Path
from typing import List, Optional

# ---------------------------------------------------------------------------
# Dangerous-command guard (inspired by OpenClaw's elevated-mode safety)
# ---------------------------------------------------------------------------

# Pattern categories for dangerous commands
_DANGEROUS_PATTERNS: List[re.Pattern] = [
    # --- Destructive file operations ---
    re.compile(r"\brm\s+(-\w*[rR]\w*\s+)?/", re.IGNORECASE),            # rm -rf /
    re.compile(r"\brm\s+(-\w*[rR]\w*\s+)?\.\.", re.IGNORECASE),          # rm -rf ..
    re.compile(r"\brm\s+(-\w*[rR]\w*\s+)?~", re.IGNORECASE),             # rm -rf ~
    re.compile(r"\brm\s+(-\w*[rR]\w*\s+)?\*", re.IGNORECASE),            # rm -rf *
    re.compile(r"\brm\s+(-\w*[rR]\w*\s+)?\$", re.IGNORECASE),            # rm -rf $VAR
    re.compile(r"\brmdir\s+/", re.IGNORECASE),                           # rmdir /
    re.compile(r"\bdel\s+/[sS]", re.IGNORECASE),                         # Windows: del /s
    re.compile(r"\brd\s+/[sS]", re.IGNORECASE),                          # Windows: rd /s

    # --- Disk/filesystem operations ---
    re.compile(r"\bformat\s+[A-Za-z]:", re.IGNORECASE),                  # format C:
    re.compile(r"\bdd\s+.*of=/dev/", re.IGNORECASE),                     # dd of=/dev/sda
    re.compile(r"\bmkfs\b", re.IGNORECASE),                              # mkfs
    re.compile(r">\s*/dev/sd[a-z]", re.IGNORECASE),                      # > /dev/sda
    re.compile(r"\bfdisk\b", re.IGNORECASE),                             # fdisk
    re.compile(r"\bparted\b", re.IGNORECASE),                            # parted

    # --- Privilege escalation ---
    re.compile(r"\bsudo\s+rm\b", re.IGNORECASE),                         # sudo rm
    re.compile(r"\bsudo\s+dd\b", re.IGNORECASE),                         # sudo dd
    re.compile(r"\bsudo\s+mkfs\b", re.IGNORECASE),                       # sudo mkfs
    re.compile(r"\bsudo\s+chmod\b.*777\s+/", re.IGNORECASE),             # sudo chmod 777 /
    re.compile(r"\bsudo\s+chown\b.*root", re.IGNORECASE),                # sudo chown root
    re.compile(r"\brunas\s+/user:Administrator", re.IGNORECASE),         # Windows runas

    # --- Permission changes ---
    re.compile(r"\bchmod\s+(-\w+\s+)?777\s+/", re.IGNORECASE),           # chmod 777 /
    re.compile(r"\bchmod\s+(-\w+\s+)?[-+]s", re.IGNORECASE),             # setuid/setgid
    re.compile(r"\battrib\s+.*\+[hHsS]", re.IGNORECASE),                 # Windows hidden/system

    # --- Remote code execution ---
    re.compile(r"\bcurl\b.*\|\s*(ba)?sh\b", re.IGNORECASE),              # curl | sh
    re.compile(r"\bwget\b.*\|\s*(ba)?sh\b", re.IGNORECASE),              # wget | sh
    re.compile(r"\bcurl\b.*\|\s*python", re.IGNORECASE),                 # curl | python
    re.compile(r"\bwget\b.*\|\s*python", re.IGNORECASE),                 # wget | python
    re.compile(r"\beval\s*\$\(", re.IGNORECASE),                         # eval $(command)
    re.compile(r"\bsource\s+<\(", re.IGNORECASE),                        # source <(command)
    re.compile(r"\bpowershell\b.*-[eE]nc", re.IGNORECASE),               # PowerShell encoded
    re.compile(r"\biex\s*\(", re.IGNORECASE),                            # PowerShell IEX
    re.compile(r"Invoke-Expression", re.IGNORECASE),                     # PowerShell Invoke-Expression

    # --- Fork bombs and resource exhaustion ---
    re.compile(r"::\(\)\{.*\|.*&", re.IGNORECASE),                       # bash fork bomb
    re.compile(r"\bfork\b.*while.*true", re.IGNORECASE),                 # fork loops
    re.compile(r"\bwhile\s+true\s*;\s*do\s*:", re.IGNORECASE),           # infinite loops

    # --- System control ---
    re.compile(r"\bshutdown\b", re.IGNORECASE),                          # shutdown
    re.compile(r"\breboot\b", re.IGNORECASE),                            # reboot
    re.compile(r"\binit\s+[06]\b", re.IGNORECASE),                       # init 0/6
    re.compile(r"\bsystemctl\s+(halt|poweroff|reboot)", re.IGNORECASE),  # systemctl halt

    # --- Environment/config tampering ---
    re.compile(r">\.?bashrc", re.IGNORECASE),                            # >.bashrc
    re.compile(r">\.?profile", re.IGNORECASE),                           # >.profile
    re.compile(r">\.?zshrc", re.IGNORECASE),                             # >.zshrc
    re.compile(r">\s*/etc/", re.IGNORECASE),                             # >/etc/
    re.compile(r"\breg\s+delete", re.IGNORECASE),                        # Windows registry delete
    re.compile(r"\breg\s+add.*\\Run", re.IGNORECASE),                    # Windows autorun

    # --- Base64/hex encoded command execution ---
    re.compile(r"\bbase64\s+-d\s*\|\s*(ba)?sh", re.IGNORECASE),          # base64 -d | sh
    re.compile(r"\becho\b.*\|\s*base64\s+-d\s*\|\s*(ba)?sh", re.IGNORECASE),
    re.compile(r"\bxxd\s+-r.*\|\s*(ba)?sh", re.IGNORECASE),              # xxd -r | sh

    # --- Network exfiltration ---
    re.compile(r"\bnc\s+-e\b", re.IGNORECASE),                           # netcat reverse shell
    re.compile(r"\b/dev/tcp/", re.IGNORECASE),                           # bash /dev/tcp

    # --- PowerShell native dangerous commands ---
    re.compile(r"\bRemove-Item\b.*-Recurse\b.*\*", re.IGNORECASE),       # Remove-Item -Recurse *
    re.compile(r"\bRemove-Item\b.*\*.*-Recurse\b", re.IGNORECASE),       # Remove-Item * -Recurse
    re.compile(r"\bStop-Process\b.*-Name\s+\*", re.IGNORECASE),          # Stop-Process -Name * (all)
    re.compile(r"\bSet-ExecutionPolicy\b.*(Bypass|Unrestricted)", re.IGNORECASE),  # weaken execution policy
]

# Environment variable patterns that might expand to dangerous paths
_DANGEROUS_VAR_PATTERNS: List[re.Pattern] = [
    re.compile(r"\$\{?HOME\}?", re.IGNORECASE),
    re.compile(r"\$\{?USER\}?", re.IGNORECASE),
    re.compile(r"\$\{?PATH\}?", re.IGNORECASE),
    re.compile(r"%USERPROFILE%", re.IGNORECASE),
    re.compile(r"%SYSTEMROOT%", re.IGNORECASE),
    re.compile(r"%WINDIR%", re.IGNORECASE),
]

def check_dangerous_command(command: str) -> Optional[str]:
    """Return a warning message if *command* matches a dangerous pattern, else None.
    
    This function performs multi-layer detection:
    1. Direct pattern matching for known dangerous commands
    2. Detection of obfuscation attempts (base64, hex encoding, variable expansion)
    3. Detection of command chaining that could bypass individual checks
    """
    # Layer 1: Direct pattern matching
    for pat in _DANGEROUS_PATTERNS:
        if pat.search(command):
            return f"Blocked: command matches dangerous pattern '{pat.pattern}'."
    
    # Layer 2: Check for dangerous commands with env var expansion
    # Commands like "rm -rf $HOME" or "rm -rf ${HOME}"
    cmd_lower = command.lower()
    dangerous_cmds = ['rm -rf', 'rm -r', 'del /s', 'rd /s', 'rmdir']
    for dcmd in dangerous_cmds:
        if dcmd in cmd_lower:
            for var_pat in _DANGEROUS_VAR_PATTERNS:
                if var_pat.search(command):
                    return f"Blocked: dangerous command with environment variable expansion detected."
    
    # Layer 3: Detect command substitution that could execute arbitrary code
    # Patterns like $(malicious) or `malicious`
    dangerous_with_substitution = ['rm', 'del', 'dd', 'mkfs', 'format']
    has_substitution = bool(re.search(r'\$\([^)]+\)|`[^`]+`', command))
    if has_substitution:
        for dcmd in dangerous_with_substitution:
            if re.search(rf'\b{dcmd}\b', cmd_lower):
                return f"Blocked: dangerous command '{dcmd}' with command substitution detected."
    
    # Layer 4: Detect pipe chains that could bypass detection
    # e.g., "echo 'cm0gLXJmIC8=' | base64 -d | sh" 
    if '|' in command:
        pipe_parts = command.split('|')
        # Check if any pipe chain ends with shell execution
        for i, part in enumerate(pipe_parts):
            part_lower = part.lower().strip()
            if i > 0 and any(sh in part_lower for sh in ['sh', 'bash', 'zsh', 'python', 'perl', 'ruby', 'node', 'powershell', 'cmd']):
                # Check if earlier parts contain encoding/decoding
                earlier = '|'.join(pipe_parts[:i]).lower()
                if any(enc in earlier for enc in ['base64', 'xxd', 'hex', 'decode', 'gunzip', 'unzip']):
                    return f"Blocked: potential encoded command execution via pipe chain detected."
    
    return None


def _is_within_workspace(path: Path, workspace: Path) -> bool:
    """Check whether *path* is within *workspace* using secure path validation.
    
    Uses pathlib's parent chain check instead of string prefix comparison
    to properly handle symlinks and edge cases.
    """
    try:
        resolved = path.resolve(strict=False)
        ws_resolved = workspace.resolve(strict=False)
        
        # Check if resolved path equals workspace or is a child of workspace
        if resolved == ws_resolved:
            return True
        
        # Check if workspace is in the parent chain
        return ws_resolved in resolved.parents
    except (ValueError, OSError):
        return False



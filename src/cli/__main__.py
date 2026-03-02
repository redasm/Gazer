"""Gazer CLI — ``python -m cli``.

Delegates to the click-based CLI in ``cli.main``.
Kept for backward compatibility with ``python -m cli`` invocations.
"""

from cli.main import cli

if __name__ == "__main__":
    cli()

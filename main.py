import logging
import os
import sys
import asyncio
import multiprocessing
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent
SOURCE_ROOT = PROJECT_ROOT / "src"
if SOURCE_ROOT.exists() and str(SOURCE_ROOT) not in sys.path:
    # Keep stdlib ahead of project packages to avoid shadowing modules like
    # ``email`` (project has ``src/email``).
    sys.path.append(str(SOURCE_ROOT))

# Load environment variables before importing runtime modules that read env at import-time.
load_dotenv()

from runtime.brain import GazerBrain
from runtime.config_manager import config

logger = logging.getLogger("GazerMain")

async def main():
    # 1. Start face UI process if enabled (only subsystem that needs a separate process)
    ui_queue = None
    ui_process = None
    if config.get("ui.enabled", False):
        try:
            from ui.head import run_head
            ui_queue = multiprocessing.Queue()
            ui_process = multiprocessing.Process(target=run_head, args=(ui_queue,))
            ui_process.start()
        except ImportError:
            logger.warning("PySide6 not installed, skipping face UI.")
    else:
        logger.info("Face UI disabled in config.")

    # 2. Start core brain (Admin API runs as asyncio task in the same process)
    brain = GazerBrain(ui_queue=ui_queue)
    try:
        await brain.start()
    finally:
        if ui_process:
            ui_process.terminate()
            ui_process.join()

if __name__ == "__main__":
    # On Windows, multiprocessing must run under if __name__ == "__main__":
    if sys.platform == "win32":
        try:
            # WindowsSelectorEventLoopPolicy is needed for compatibility with
            # python-telegram-bot and other libraries that use select().
            # Trade-off: this limits asyncio subprocess support; if subprocess
            # tools are needed, consider ProactorEventLoopPolicy instead.
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        except Exception:
            pass
    if "--cli" in sys.argv:
        from cli.interactive import main as cli_main
        cli_main()
    else:
        try:
            asyncio.run(main())
        except KeyboardInterrupt:
            logger.info("Gazer shutting down...")
            sys.exit(0)

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
from runtime.ipc_secure import wrap_queue

async def main():
    # Create IPC queues with HMAC authentication
    ui_queue_raw = multiprocessing.Queue()
    chat_input_raw = multiprocessing.Queue()
    chat_output_raw = multiprocessing.Queue()
    
    # Wrap with secure communication layer
    ui_queue = wrap_queue(ui_queue_raw, max_age_seconds=60.0)
    chat_input_q = wrap_queue(chat_input_raw, max_age_seconds=300.0)
    chat_output_q = wrap_queue(chat_output_raw, max_age_seconds=300.0)
    
    # 1. Start face UI process if enabled in config
    ui_process = None
    if config.get("ui.enabled", False):
        try:
            from ui.head import run_head
            ui_process = multiprocessing.Process(target=run_head, args=(ui_queue,))
            ui_process.start()
        except ImportError:
            print("PySide6 not installed, skipping face UI.")
    else:
        print("Face UI disabled in config.")
    
    # 2. Start Admin API process (pass in Chat Queues)
    from tools.admin_api import run_admin_api
    api_port = int(os.environ.get("ADMIN_API_PORT", config.get("web.port", 8080)))
    api_process = multiprocessing.Process(
        target=run_admin_api,
        args=(api_port, chat_input_q, chat_output_q),
    )
    api_process.start()
    
    # 3. Start core brain
    brain = GazerBrain(ui_queue=ui_queue, ipc_input=chat_input_q, ipc_output=chat_output_q)
    try:
        await brain.start()
    finally:
        if ui_process:
            ui_process.terminate()
            ui_process.join()
        # Graceful shutdown for api_process
        api_process.terminate()
        api_process.join(timeout=5)
        if api_process.is_alive():
            api_process.kill()
            api_process.join()

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
            print("Gazer shutting down...")
            sys.exit(0)

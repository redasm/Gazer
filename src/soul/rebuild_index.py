import os
import re
import asyncio
import logging
from memory import MemoryManager
from soul.core import MemoryEntry
from datetime import datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("GazerConsistency")

async def rebuild_index():
    """Scan all markdown files and rebuild the memory index (Vector + FTS)."""
    manager = MemoryManager()
    manager.index.clear()
    
    daily_path = manager.daily_path
    if not os.path.exists(daily_path):
        return

    logger.info("Starting index rebuild from Markdown files (Vector + FTS)...")
    
    for filename in sorted(os.listdir(daily_path)):
        if filename.endswith(".md"):
            date_str = filename.replace(".md", "")
            file_path = os.path.join(daily_path, filename)
            
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
                blocks = re.findall(r"### \[(.*?)\] (.*?)\n(.*?)(?=\n###|\Z)", content, re.DOTALL)
                
                for time_str, sender, text in blocks:
                    try:
                        dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M:%S")
                        await manager.index.add_memory(text.strip(), sender.strip(), dt)
                    except Exception as e:
                        logger.warning(f"Failed to index block in {filename}: {e}")
    
    logger.info("Index rebuild complete.")

if __name__ == "__main__":
    asyncio.run(rebuild_index())


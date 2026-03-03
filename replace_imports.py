import os

target_dir = r"c:\AppProject\Gazer\tests"

for root, _, files in os.walk(target_dir):
    for f in files:
        if not f.endswith(".py"):
            continue
        path = os.path.join(root, f)
        try:
            with open(path, "r", encoding="utf-8") as file:
                content = file.read()
        except:
            continue
            
        new_content = content.replace("tools.admin.workflows", "tools.admin.api_facade")
        new_content = new_content.replace("from tools.admin import workflows", "from tools.admin import api_facade")
        
        if new_content != content:
            with open(path, "w", encoding="utf-8") as file:
                file.write(new_content)
            print(f"Updated {f}")

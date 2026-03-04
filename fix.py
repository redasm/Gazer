import os

for root, _, files in os.walk('tests'):
    for file in files:
        if file.endswith('.py'):
            path = os.path.join(root, file)
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
            if 'agent.loop.get_owner_manager' in content:
                content = content.replace('agent.loop.get_owner_manager', 'security.owner.get_owner_manager')
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(content)
                print(f"Fixed {path}")

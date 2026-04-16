class Facade:
    _MODULES = [
        'system', 'memory', 'observability', 'debug', 'websockets',
        'state', 'auth', 'config_routes', 'mcp_routes', 'persona_routes',
        'training_routes', 'logs', 'error_handlers', 'gateway', 'evolution',
        'deployment', 'channel_webhooks', 'cron', 'git', 'plugins',
        'policy', 'skills', 'utils', 'validation',
        'strategy_helpers', 'coding_helpers',
        'training_helpers', 'observability_helpers',
    ]

    def __getattr__(self, name):
        import importlib
        import tools.admin as admin
        for mod_name in self._MODULES:
            mod = importlib.import_module(f'tools.admin.{mod_name}')
            if hasattr(mod, name):
                return getattr(mod, name)
        raise AttributeError(f"Facade has no attribute {name}")
        
    def __setattr__(self, name, value):
        import importlib
        found = False
        for mod_name in self._MODULES:
            mod = importlib.import_module(f'tools.admin.{mod_name}')
            if hasattr(mod, name):
                setattr(mod, name, value)
                found = True
        
        if not found:
            mod = importlib.import_module('tools.admin.state')
            setattr(mod, name, value)

    def __dir__(self):
        import importlib
        keys = set()
        for mod_name in self._MODULES:
            mod = importlib.import_module(f'tools.admin.{mod_name}')
            keys.update(dir(mod))
        return list(keys)

import sys
sys.modules[__name__] = Facade()

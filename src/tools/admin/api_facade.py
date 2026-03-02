class Facade:
    def __getattr__(self, name):
        import tools.admin.system as system
        import tools.admin.workflows as workflows
        import tools.admin.memory as memory
        import tools.admin.observability as observability
        import tools.admin.debug as debug
        import tools.admin.websockets as websockets
        import tools.admin._shared as _shared
        
        for mod in [system, workflows, memory, observability, debug, websockets, _shared]:
            if hasattr(mod, name):
                return getattr(mod, name)
        raise AttributeError(f"Facade has no attribute {name}")
        
    def __setattr__(self, name, value):
        import tools.admin.system as system
        import tools.admin.workflows as workflows
        import tools.admin.memory as memory
        import tools.admin.observability as observability
        import tools.admin.debug as debug
        import tools.admin.websockets as websockets
        import tools.admin._shared as _shared
        
        found = False
        for mod in [system, workflows, memory, observability, debug, websockets, _shared]:
            if hasattr(mod, name):
                setattr(mod, name, value)
                found = True
        
        if not found:
            setattr(_shared, name, value)

    def __dir__(self):
        import tools.admin.system as system
        import tools.admin.workflows as workflows
        import tools.admin.memory as memory
        import tools.admin.observability as observability
        import tools.admin.debug as debug
        import tools.admin.websockets as websockets
        import tools.admin._shared as _shared
        keys = set()
        for mod in [system, workflows, memory, observability, debug, websockets, _shared]:
            keys.update(dir(mod))
        return list(keys)

import sys
sys.modules[__name__] = Facade()

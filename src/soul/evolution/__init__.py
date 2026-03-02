from .gazer_evolution import (
    GazerEvolution, 
    get_evolution, 
    FEEDBACK_PATH, 
    HISTORY_PATH
)
from .apo_optimizer import APOOptimizer

__all__ = ["GazerEvolution", "APOOptimizer", "get_evolution", "FEEDBACK_PATH", "HISTORY_PATH"]

def __getattr__(name: str):
    if name == "evolution":
        return get_evolution()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

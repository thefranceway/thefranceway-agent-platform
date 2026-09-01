# Lazy exports - avoids importing anthropic on `import core`
_LAZY_IMPORTS = {
    "BaseAgent": ("core.base_agent", "BaseAgent"),
    "BEHAVIORAL_PROFILES": ("core.base_agent", "BEHAVIORAL_PROFILES"),
    "Orchestrator": ("core.orchestrator", "Orchestrator"),
    "SPARDebater": ("core.spar", "SPARDebater"),
}

def __getattr__(name: str):
    if name in _LAZY_IMPORTS:
        mod_name, attr = _LAZY_IMPORTS[name]
        import importlib
        mod = importlib.import_module(mod_name)
        return getattr(mod, attr)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

def __dir__():
    return sorted(list(globals().keys()) + list(_LAZY_IMPORTS.keys()))

__all__ = list(_LAZY_IMPORTS.keys())

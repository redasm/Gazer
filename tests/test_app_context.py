import pytest
from runtime.app_context import AppContext, get_app_context, set_app_context
import tools.admin.state as admin_state

def test_app_context_singleton():
    # Ensure starting clean
    import runtime.app_context as _app_context_module
    old_ctx = _app_context_module._ctx
    _app_context_module._ctx = None
    
    try:
        assert get_app_context() is None
        
        ctx = AppContext(llm_router="fake_router", usage_tracker="fake_usage")
        set_app_context(ctx)
        
        assert get_app_context() is ctx
        assert get_app_context().llm_router == "fake_router"
        assert get_app_context().usage_tracker == "fake_usage"
    finally:
        _app_context_module._ctx = old_ctx

def test_state_getters_delegate_to_app_context():
    # Setup
    import runtime.app_context as _app_context_module
    old_ctx = _app_context_module._ctx
    
    ctx = AppContext(
        llm_router="context_router",
        usage_tracker=None,
    )
    _app_context_module._ctx = ctx
    
    try:
        # Check delegation
        assert admin_state.get_llm_router() == "context_router"
        
        # Check fallback when AppContext doesn't have the field
        # wait, the getter logic says: `ctx.usage_tracker if (ctx and ctx.usage_tracker is not None) else USAGE_TRACKER`
        admin_state.USAGE_TRACKER = "global_usage"
        # since AppContext usage_tracker is None, it should fall back to global
        assert admin_state.get_usage_tracker() == "global_usage"
        
        # Check fallback when AppContext is None entirely
        _app_context_module._ctx = None
        admin_state.LLM_ROUTER = "global_router"
        assert admin_state.get_llm_router() == "global_router"
        
    finally:
        _app_context_module._ctx = old_ctx

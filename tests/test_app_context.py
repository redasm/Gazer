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
    import runtime.app_context as _app_context_module
    old_ctx = _app_context_module._ctx

    ctx = AppContext(
        llm_router="context_router",
        usage_tracker=None,
    )
    _app_context_module._ctx = ctx

    try:
        assert admin_state.get_llm_router() == "context_router"
        # usage_tracker is None in AppContext -> returns None
        assert admin_state.get_usage_tracker() is None
        # No AppContext -> returns None
        _app_context_module._ctx = None
        assert admin_state.get_llm_router() is None
    finally:
        _app_context_module._ctx = old_ctx

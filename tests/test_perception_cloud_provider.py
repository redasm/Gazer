from perception import cloud_provider


class _StubRegistry:
    def __init__(self, providers):
        self._providers = providers

    def get_provider(self, name: str):
        return dict(self._providers.get(name, {}))


def test_resolve_cloud_config_disabled(monkeypatch):
    monkeypatch.setattr(cloud_provider, "get_provider_registry", lambda: _StubRegistry({}))
    payload = cloud_provider.resolve_openai_compatible_cloud_config({"provider": "disabled"})
    assert payload["enabled"] is False
    assert payload["reason"] == "disabled"


def test_resolve_cloud_config_from_provider_ref(monkeypatch):
    monkeypatch.setattr(
        cloud_provider,
        "get_provider_registry",
        lambda: _StubRegistry(
            {
                "dashscope": {
                    "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                    "api_key": "k",
                    "default_model": "qwen-vl-max",
                }
            }
        ),
    )
    payload = cloud_provider.resolve_openai_compatible_cloud_config(
        {"provider": "openai_compatible", "provider_ref": "dashscope"},
        require_base_url=True,
    )
    assert payload["enabled"] is True
    assert payload["base_url"].startswith("https://dashscope")
    assert payload["model"] == "qwen-vl-max"


def test_resolve_cloud_config_explicit_overrides_provider_ref(monkeypatch):
    monkeypatch.setattr(
        cloud_provider,
        "get_provider_registry",
        lambda: _StubRegistry(
            {"r1": {"base_url": "https://a", "api_key": "k1", "default_model": "m1"}}
        ),
    )
    payload = cloud_provider.resolve_openai_compatible_cloud_config(
        {
            "provider": "openai_compatible",
            "provider_ref": "r1",
            "base_url": "https://override",
            "api_key": "k2",
            "model": "m2",
        },
        require_base_url=True,
    )
    assert payload["enabled"] is True
    assert payload["base_url"] == "https://override"
    assert payload["api_key"] == "k2"
    assert payload["model"] == "m2"


def test_resolve_cloud_config_provider_ref_not_found(monkeypatch):
    monkeypatch.setattr(cloud_provider, "get_provider_registry", lambda: _StubRegistry({}))
    payload = cloud_provider.resolve_openai_compatible_cloud_config(
        {"provider": "openai_compatible", "provider_ref": "missing"},
        default_model="fallback-model",
        require_base_url=True,
    )
    assert payload["enabled"] is False
    assert payload["reason"].startswith("provider_ref_not_found:")


def test_resolve_cloud_config_provider_name_direct(monkeypatch):
    monkeypatch.setattr(
        cloud_provider,
        "get_provider_registry",
        lambda: _StubRegistry(
            {
                "deepseek": {
                    "base_url": "https://api.deepseek.com/v1",
                    "api_key": "k",
                    "default_model": "deepseek-chat",
                }
            }
        ),
    )
    payload = cloud_provider.resolve_openai_compatible_cloud_config(
        {"provider": "deepseek"},
        require_base_url=True,
    )
    assert payload["enabled"] is True
    assert payload["provider_ref"] == "deepseek"
    assert payload["model"] == "deepseek-chat"


def test_resolve_cloud_config_fast_brain_provider(monkeypatch):
    monkeypatch.setattr(
        cloud_provider,
        "get_provider_registry",
        lambda: _StubRegistry(
            {
                "dashscope": {
                    "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                    "api_key": "k",
                    "default_model": "qwen-plus",
                }
            }
        ),
    )

    monkeypatch.setattr(
        cloud_provider.ModelRegistry,
        "resolve_model_ref",
        staticmethod(lambda profile_name: ("dashscope", "qwen-plus") if profile_name == "fast_brain" else (None, None)),
    )
    payload = cloud_provider.resolve_openai_compatible_cloud_config(
        {"provider": "fast_brain"},
        require_base_url=True,
    )
    assert payload["enabled"] is True
    assert payload["provider_ref"] == "dashscope"

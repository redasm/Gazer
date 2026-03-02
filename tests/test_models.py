import copy
import unittest
from unittest.mock import patch

from runtime.config_manager import config
from soul.models import ModelRegistry


class _StubProviderRegistry:
    def __init__(self, providers):
        self._providers = providers

    def get_provider(self, name):
        return dict(self._providers.get(name, {}))

    def list_providers(self):
        return dict(self._providers)


class TestModelRegistry(unittest.TestCase):
    def setUp(self):
        self.original_config = copy.deepcopy(config.data)

    def tearDown(self):
        config.data = self.original_config

    def test_missing_profile_returns_empty(self):
        config.data = {"agents": {"defaults": {"model": {"primary": "", "fallbacks": []}}}}
        api_key, base_url, model_name, headers = ModelRegistry.resolve_model("slow_brain")
        self.assertIsNone(api_key)
        self.assertIsNone(base_url)
        self.assertIsNone(model_name)
        self.assertIsNone(headers)

    def test_active_profile_resolution(self):
        config.data = {
            "agents": {
                "defaults": {
                    "model": {
                        "primary": "openai/gpt-4o",
                        "fallbacks": ["custom_ollama/llama3-8b"],
                    }
                }
            }
        }
        providers = {
            "custom_ollama": {
                "base_url": "http://localhost:11434/v1",
                "api_key": "ollama",
                "default_model": "llama3",
            }
        }
        with patch("soul.models.get_provider_registry", return_value=_StubProviderRegistry(providers)):
            api_key, base_url, model_name, _headers = ModelRegistry.resolve_model("fast_brain")
        self.assertEqual(api_key, "ollama")
        self.assertEqual(base_url, "http://localhost:11434/v1")
        self.assertEqual(model_name, "llama3-8b")

    def test_openai_default_base_url(self):
        config.data = {
            "agents": {"defaults": {"model": {"primary": "openai/gpt-4o", "fallbacks": []}}}
        }
        providers = {"openai": {"api_key": "sk-test"}}
        with patch("soul.models.get_provider_registry", return_value=_StubProviderRegistry(providers)):
            _, base_url, _, _ = ModelRegistry.resolve_model("slow_brain")
        self.assertEqual(base_url, "https://api.openai.com/v1")


if __name__ == "__main__":
    unittest.main()

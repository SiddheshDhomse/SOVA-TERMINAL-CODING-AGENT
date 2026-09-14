"""Unit tests for multi-provider configuration, credentials, and client initialization."""
import os
import unittest
from unittest.mock import MagicMock, patch

from agent import credentials, llm


class TestProviderConfiguration(unittest.TestCase):
    def setUp(self):
        llm.invalidate_client()

    def tearDown(self):
        llm.invalidate_client()

    def test_available_providers(self):
        providers = llm.get_available_providers()
        self.assertIn("groq", providers)
        self.assertIn("nvidia", providers)
        self.assertIn("ollama", providers)
        self.assertIn("openai", providers)
        self.assertIn("gemini", providers)
        self.assertIn("openrouter", providers)

    def test_groq_configuration(self):
        models = llm.get_models_for_provider("groq")
        self.assertIn("openai/gpt-oss-120b", models)
        self.assertIn("openai/gpt-oss-20b", models)
        self.assertIn("llama-3.3-70b-versatile", models)

    def test_gemini_configuration(self):
        self.assertEqual(llm.DEFAULT_MODELS["gemini"], "gemini-2.5-flash")
        models = llm.get_models_for_provider("gemini")
        self.assertIn("gemini-2.5-flash", models)
        self.assertIn("gemini-2.5-pro", models)
        self.assertIn("gemini-2.0-flash", models)
        self.assertEqual(llm.PROVIDERS_CONFIG["gemini"]["default_budget"], 64000)

    def test_openrouter_configuration(self):
        self.assertEqual(llm.DEFAULT_MODELS["openrouter"], "anthropic/claude-3.7-sonnet")
        models = llm.get_models_for_provider("openrouter")
        self.assertIn("anthropic/claude-3.7-sonnet", models)
        self.assertIn("deepseek/deepseek-r1", models)
        self.assertIn("openai/gpt-4o", models)
        self.assertEqual(llm.PROVIDERS_CONFIG["openrouter"]["default_budget"], 64000)

    def test_token_budget_resolution(self):
        # Specific Groq model budgets
        self.assertEqual(llm.get_token_budget("groq", "openai/gpt-oss-120b"), 5500)
        self.assertEqual(llm.get_token_budget("groq", "openai/gpt-oss-20b"), 12000)
        self.assertEqual(llm.get_token_budget("groq", "llama-3.3-70b-versatile"), 9000)
        # Gemini / OpenRouter budgets
        self.assertEqual(llm.get_token_budget("gemini", "gemini-2.5-flash"), 64000)
        self.assertEqual(llm.get_token_budget("openrouter", "anthropic/claude-3.7-sonnet"), 64000)


class TestProviderClients(unittest.TestCase):
    def setUp(self):
        llm.invalidate_client()

    def tearDown(self):
        llm.invalidate_client()

    def test_gemini_client_creation(self):
        with patch.dict(os.environ, {"SOVA_PROVIDER": "gemini"}), \
             patch("agent.credentials.get_active_credential", return_value="fake-gemini-key"), \
             patch("agent.llm.OpenAI") as mock_openai:
            client = llm.get_client()
            mock_openai.assert_called_once_with(
                api_key="fake-gemini-key",
                base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            )
            self.assertIsNotNone(client)

    def test_openrouter_client_creation(self):
        with patch.dict(os.environ, {"SOVA_PROVIDER": "openrouter"}), \
             patch("agent.credentials.get_active_credential", return_value="fake-openrouter-key"), \
             patch("agent.llm.OpenAI") as mock_openai:
            client = llm.get_client()
            mock_openai.assert_called_once_with(
                api_key="fake-openrouter-key",
                base_url="https://openrouter.ai/api/v1",
            )
            self.assertIsNotNone(client)

    def test_missing_credentials_raises(self):
        with patch.dict(os.environ, {"SOVA_PROVIDER": "gemini"}), \
             patch("agent.credentials.get_active_credential", return_value=None):
            with self.assertRaises(RuntimeError) as ctx:
                llm.get_client()
            self.assertIn("GEMINI_API_KEY is not set", str(ctx.exception))

        llm.invalidate_client()
        with patch.dict(os.environ, {"SOVA_PROVIDER": "openrouter"}), \
             patch("agent.credentials.get_active_credential", return_value=None):
            with self.assertRaises(RuntimeError) as ctx:
                llm.get_client()
            self.assertIn("OPENROUTER_API_KEY is not set", str(ctx.exception))


class TestProviderCredentials(unittest.TestCase):
    def test_known_keys_catalog(self):
        known = credentials.get_known_keys()
        keys = [k["key"] for k in known]
        self.assertIn("GEMINI_API_KEY", keys)
        self.assertIn("OPENROUTER_API_KEY", keys)

        gemini_entry = next(k for k in known if k["key"] == "GEMINI_API_KEY")
        self.assertEqual(gemini_entry["hint"], "aistudio.google.com")

        openrouter_entry = next(k for k in known if k["key"] == "OPENROUTER_API_KEY")
        self.assertEqual(openrouter_entry["hint"], "openrouter.ai")

    def test_test_credential_success(self):
        with patch("openai.OpenAI") as mock_openai:
            mock_instance = MagicMock()
            mock_instance.models.list.return_value = ["gemini-2.5-flash", "gemini-2.5-pro"]
            mock_openai.return_value = mock_instance

            res = credentials.test_credential(".", "GEMINI_API_KEY", "valid-key")
            self.assertTrue(res["ok"])
            self.assertIn("Google Gemini connected", res["message"])

    def test_test_credential_auth_failure(self):
        with patch("openai.OpenAI") as mock_openai:
            mock_openai.side_effect = Exception("401 Authentication error")
            res = credentials.test_credential(".", "OPENROUTER_API_KEY", "bad-key")
            self.assertFalse(res["ok"])
            self.assertIn("401 Invalid Key", res["message"])

    def test_open_route_api_key_alias(self):
        with patch.dict(os.environ, {"OPEN_ROUTE_API_KEY": "sk-or-v1-alias-key"}, clear=True):
            val = credentials.get_active_credential("OPENROUTER_API_KEY")
            self.assertEqual(val, "sk-or-v1-alias-key")
            val_direct = credentials.get_active_credential("OPEN_ROUTE_API_KEY")
            self.assertEqual(val_direct, "sk-or-v1-alias-key")


if __name__ == "__main__":
    unittest.main()

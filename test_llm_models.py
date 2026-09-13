import os
import unittest

from agent.llm import DEFAULT_MODELS, get_model, normalize_model


class TestModelNormalization(unittest.TestCase):
    def test_groq_alias_maps_human_name_to_model_id(self):
        self.assertEqual(
            normalize_model("GPT-OSS 120B", provider="groq"),
            "openai/gpt-oss-120b",
        )

    def test_deprecated_groq_model_maps_to_supported_default(self):
        self.assertEqual(
            normalize_model("llama-3.3-70b-versatile", provider="groq"),
            "openai/gpt-oss-120b",
        )

    def test_unknown_model_is_left_unchanged(self):
        self.assertEqual(
            normalize_model("custom-model-name", provider="groq"),
            "custom-model-name",
        )

    def test_env_model_is_normalized(self):
        original_provider = os.environ.get("SOVA_PROVIDER")
        original_model = os.environ.get("SOVA_MODEL")
        try:
            os.environ["SOVA_PROVIDER"] = "groq"
            os.environ["SOVA_MODEL"] = "GPT-OSS 120B"
            self.assertEqual(get_model(), "openai/gpt-oss-120b")
        finally:
            if original_provider is None:
                os.environ.pop("SOVA_PROVIDER", None)
            else:
                os.environ["SOVA_PROVIDER"] = original_provider
            if original_model is None:
                os.environ.pop("SOVA_MODEL", None)
            else:
                os.environ["SOVA_MODEL"] = original_model

    def test_groq_default_model_is_current(self):
        self.assertEqual(DEFAULT_MODELS["groq"], "openai/gpt-oss-120b")

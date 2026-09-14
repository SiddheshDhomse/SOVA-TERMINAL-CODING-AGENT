"""Unit tests for agent.cost token pricing, turn cost calculation, and formatting."""
import unittest

from agent.cost import (
    MODEL_PRICING,
    calculate_turn_cost,
    format_cost,
    format_token_cost_summary,
)


class TestCostTracking(unittest.TestCase):
    def test_calculate_turn_cost_groq(self):
        # 1,000 prompt tokens and 500 completion tokens on Groq default ($0.59 / $0.79)
        cost = calculate_turn_cost("groq", "llama-3.3-70b-versatile", 1000, 500)
        expected = ((1000 / 1e6) * 0.59) + ((500 / 1e6) * 0.79)
        self.assertAlmostEqual(cost, expected, places=5)

    def test_calculate_turn_cost_gemini(self):
        # Gemini 2.5 Flash: $0.15 prompt, $0.60 completion
        cost = calculate_turn_cost("gemini", "gemini-2.5-flash", 10000, 2000)
        expected = ((10000 / 1e6) * 0.15) + ((2000 / 1e6) * 0.60)
        self.assertAlmostEqual(cost, expected, places=5)

    def test_calculate_turn_cost_openrouter_claude(self):
        # OpenRouter Claude 3.7 Sonnet: $3.00 prompt, $15.00 completion
        cost = calculate_turn_cost("openrouter", "anthropic/claude-3.7-sonnet", 5000, 1000)
        expected = ((5000 / 1e6) * 3.00) + ((1000 / 1e6) * 15.00)
        self.assertAlmostEqual(cost, expected, places=5)

    def test_calculate_turn_cost_ollama_and_nvidia_free(self):
        # Local / free tiers
        self.assertEqual(calculate_turn_cost("ollama", "llama3.1:8b", 50000, 10000), 0.0)
        self.assertEqual(calculate_turn_cost("nvidia", "nemotron", 50000, 10000), 0.0)

    def test_format_cost(self):
        self.assertEqual(format_cost(0.0), "$0.00 (Free/Local)")
        self.assertEqual(format_cost(0.0045), "$0.0045")
        self.assertEqual(format_cost(1.50), "$1.50")

    def test_format_token_cost_summary(self):
        metrics = {
            "total_tokens": 12500,
            "prompt_tokens": 10000,
            "completion_tokens": 2500,
            "cost_usd": 0.0085,
            "turns": 4,
            "elapsed_seconds": 6.3,
        }
        summary = format_token_cost_summary(metrics)
        self.assertIn("4 turn(s) in 6.3s", summary)
        self.assertIn("12,500", summary)
        self.assertIn("$0.0085", summary)


if __name__ == "__main__":
    unittest.main()

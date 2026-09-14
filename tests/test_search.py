"""Unit tests for Okapi BM25 conceptual code search and tool integration."""
import os
import shutil
import tempfile
import unittest

from agent.search import BM25Index, tokenize_code
from agent.tools import build_tools


class TestBM25Search(unittest.TestCase):
    def test_tokenize_code_splits_identifiers(self):
        tokens = tokenize_code("parseJwtTokenAndVerify_signature(authHeader, db_pool)")
        self.assertIn("jwt", tokens)
        self.assertIn("token", tokens)
        self.assertIn("verify", tokens)
        self.assertIn("signature", tokens)
        self.assertIn("auth", tokens)
        self.assertIn("header", tokens)
        self.assertIn("db", tokens)
        self.assertIn("pool", tokens)

    def test_tokenize_code_filters_stopwords(self):
        tokens = tokenize_code("function to calculate the price for all items in cart")
        self.assertIn("calculate", tokens)
        self.assertIn("price", tokens)
        self.assertIn("items", tokens)
        self.assertIn("cart", tokens)
        self.assertNotIn("the", tokens)
        self.assertNotIn("for", tokens)
        self.assertNotIn("in", tokens)

    def test_bm25_search_ranks_relevant_file_first(self):
        temp_dir = tempfile.mkdtemp()
        try:
            # File 1: Authentication module
            auth_py = os.path.join(temp_dir, "auth.py")
            with open(auth_py, "w", encoding="utf-8") as f:
                f.write(
                    "def verify_jwt_token(token):\n"
                    "    \"\"\"Validate JWT signature and calculate expiration time.\"\"\"\n"
                    "    decoded = jwt.decode(token, secret_key)\n"
                    "    return decoded\n"
                )

            # File 2: Database module
            db_py = os.path.join(temp_dir, "db.py")
            with open(db_py, "w", encoding="utf-8") as f:
                f.write(
                    "def get_db_connection():\n"
                    "    \"\"\"Acquire SQLite connection pool connection.\"\"\"\n"
                    "    return sqlite3.connect('app.db')\n"
                )

            idx = BM25Index(temp_dir)
            matches = idx.search("JWT token expiration signature")
            self.assertTrue(len(matches) >= 1)
            best_chunk, score = matches[0]
            self.assertEqual(best_chunk.file_path, "auth.py")
            self.assertGreater(score, 0)

            # Search db concept
            db_matches = idx.search("SQLite database connection pool")
            self.assertTrue(len(db_matches) >= 1)
            self.assertEqual(db_matches[0][0].file_path, "db.py")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_bm25_search_path_filter(self):
        temp_dir = tempfile.mkdtemp()
        try:
            os.makedirs(os.path.join(temp_dir, "pkg_a"), exist_ok=True)
            os.makedirs(os.path.join(temp_dir, "pkg_b"), exist_ok=True)

            with open(os.path.join(temp_dir, "pkg_a", "worker.py"), "w", encoding="utf-8") as f:
                f.write("def run_job(): return 'common task'\n")

            with open(os.path.join(temp_dir, "pkg_b", "worker.py"), "w", encoding="utf-8") as f:
                f.write("def run_job(): return 'common task'\n")

            idx = BM25Index(temp_dir)
            matches_a = idx.search("common task", path_filter="pkg_a")
            self.assertTrue(all("pkg_a" in m[0].file_path for m in matches_a))
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_search_code_tool_integration(self):
        temp_dir = tempfile.mkdtemp()
        try:
            with open(os.path.join(temp_dir, "payment.py"), "w", encoding="utf-8") as f:
                f.write("def process_stripe_payment(amount, currency='usd'):\n    pass\n")

            _schemas, impls = build_tools(temp_dir)
            self.assertIn("search_code", impls)
            output = impls["search_code"]("Stripe payment processing")
            self.assertIn("payment.py", output)
            self.assertIn("process_stripe_payment", output)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_bm25_search_empty_workspace(self):
        temp_dir = tempfile.mkdtemp()
        try:
            idx = BM25Index(temp_dir)
            matches = idx.search("any query")
            self.assertEqual(matches, [])
            output = idx.format_search_results("any query")
            self.assertIn("No code snippets found", output)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()

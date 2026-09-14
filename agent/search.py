"""In-memory Okapi BM25 lexical code search for SOVA codebase intelligence."""
import math
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

_CODE_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java", ".c", ".cpp",
    ".h", ".hpp", ".html", ".css", ".json", ".md", ".yaml", ".yml", ".toml", ".sh",
}

_EXCLUDED_DIRS = {
    ".git", ".sova", "node_modules", ".venv", "venv", "env",
    "__pycache__", ".pytest_cache", ".mypy_cache", "build", "dist",
}

_STOPWORDS = {
    "a", "about", "above", "after", "again", "against", "all", "am", "an", "and",
    "any", "are", "aren't", "as", "at", "be", "because", "been", "before", "being",
    "below", "between", "both", "but", "by", "can't", "cannot", "could", "couldn't",
    "did", "didn't", "do", "does", "doesn't", "doing", "don't", "down", "during",
    "each", "few", "for", "from", "further", "had", "hadn't", "has", "hasn't",
    "have", "haven't", "having", "he", "he'd", "he'll", "he's", "her", "here",
    "here's", "hers", "herself", "him", "himself", "his", "how", "how's", "i",
    "i'd", "i'll", "i'm", "i've", "if", "in", "into", "is", "isn't", "it", "it's",
    "its", "itself", "let's", "me", "more", "most", "mustn't", "my", "myself",
    "no", "nor", "not", "of", "off", "on", "once", "only", "or", "other", "ought",
    "our", "ours", "ourselves", "out", "over", "own", "same", "shan't", "she",
    "she'd", "she'll", "she's", "should", "shouldn't", "so", "some", "such",
    "than", "that", "that's", "the", "their", "theirs", "them", "themselves",
    "then", "there", "there's", "these", "they", "they'd", "they'll", "they're",
    "they've", "this", "those", "through", "to", "too", "under", "until", "up",
    "very", "was", "wasn't", "we", "we'd", "we'll", "we're", "we've", "were",
    "weren't", "what", "what's", "when", "when's", "where", "where's", "which",
    "while", "who", "who's", "whom", "why", "why's", "with", "won't", "would",
    "wouldn't", "you", "you'd", "you'll", "you're", "you've", "your", "yours",
    "yourself", "yourselves", "true", "false", "none", "null", "self", "return",
}


def tokenize_code(text: str) -> List[str]:
    """Tokenize code into normalized words, splitting camelCase and snake_case."""
    # Split camelCase and snake_case
    words = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z][a-z]|\d|\W|$)|[a-zA-Z0-9]+", text)
    tokens: List[str] = []
    for w in words:
        w_clean = w.lower().strip("_")
        if len(w_clean) > 1 and w_clean not in _STOPWORDS:
            tokens.append(w_clean)
    return tokens


@dataclass
class CodeChunk:
    file_path: str
    start_line: int
    end_line: int
    name: str
    content: str
    tokens: List[str] = field(default_factory=list)


class BM25Index:
    """Fast in-memory Okapi BM25 index for conceptual code search."""

    def __init__(self, root_dir: str, k1: float = 1.5, b: float = 0.75):
        self.root_dir = os.path.abspath(root_dir)
        self.k1 = k1
        self.b = b
        self.chunks: List[CodeChunk] = []
        self.doc_lengths: List[int] = []
        self.avg_doc_len: float = 0.0
        self.doc_freqs: Dict[str, int] = {}
        self.num_docs: int = 0
        self.indexed: bool = False

    def build_index(self) -> None:
        """Scan workspace and populate BM25 index."""
        self.chunks = []
        for root, dirs, files in os.walk(self.root_dir):
            dirs[:] = [d for d in dirs if d not in _EXCLUDED_DIRS]
            for f in files:
                ext = os.path.splitext(f)[1].lower()
                if ext not in _CODE_EXTENSIONS:
                    continue
                full_path = os.path.join(root, f)
                rel_path = os.path.relpath(full_path, self.root_dir).replace("\\", "/")
                try:
                    with open(full_path, "r", encoding="utf-8", errors="replace") as fh:
                        content = fh.read()
                except OSError:
                    continue

                self._chunk_file(rel_path, content)

        self.num_docs = len(self.chunks)
        self.doc_lengths = [len(c.tokens) for c in self.chunks]
        self.avg_doc_len = (sum(self.doc_lengths) / self.num_docs) if self.num_docs > 0 else 0.0

        # Calculate document frequencies
        self.doc_freqs = {}
        for chunk in self.chunks:
            unique_terms = set(chunk.tokens)
            for t in unique_terms:
                self.doc_freqs[t] = self.doc_freqs.get(t, 0) + 1

        self.indexed = True

    def _chunk_file(self, rel_path: str, content: str) -> None:
        lines = content.splitlines()
        total_lines = len(lines)
        if total_lines == 0:
            return

        chunk_size = 35
        overlap = 10
        start = 0

        while start < total_lines:
            end = min(start + chunk_size, total_lines)
            chunk_slice = lines[start:end]
            chunk_content = "\n".join(chunk_slice)
            chunk_name = f"{rel_path}:{start + 1}-{end}"
            tokens = tokenize_code(chunk_content)

            if tokens:
                self.chunks.append(
                    CodeChunk(
                        file_path=rel_path,
                        start_line=start + 1,
                        end_line=end,
                        name=chunk_name,
                        content=chunk_content,
                        tokens=tokens,
                    )
                )

            if end >= total_lines:
                break
            start += (chunk_size - overlap)

    def search(self, query: str, path_filter: Optional[str] = None, top_k: int = 5) -> List[Tuple[CodeChunk, float]]:
        """Query the index and return top matching chunks with scores."""
        if not self.indexed:
            self.build_index()

        q_tokens = tokenize_code(query)
        if not q_tokens or self.num_docs == 0:
            return []

        filter_norm = path_filter.replace("\\", "/").lstrip("./") if path_filter else None
        scores: List[Tuple[int, float]] = []

        for idx, chunk in enumerate(self.chunks):
            if filter_norm and filter_norm not in chunk.file_path:
                continue

            doc_len = self.doc_lengths[idx]
            if doc_len == 0:
                continue

            score = 0.0
            term_counts: Dict[str, int] = {}
            for t in chunk.tokens:
                term_counts[t] = term_counts.get(t, 0) + 1

            for q in q_tokens:
                if q not in term_counts:
                    continue
                tf = term_counts[q]
                df = self.doc_freqs.get(q, 0)
                # Okapi BM25 formula with smoothing
                idf = math.log(1.0 + (self.num_docs - df + 0.5) / (df + 0.5))
                denom = tf + self.k1 * (1.0 - self.b + self.b * (doc_len / (self.avg_doc_len or 1.0)))
                score += idf * ((tf * (self.k1 + 1.0)) / denom)

            if score > 0.05:
                scores.append((idx, score))

        scores.sort(key=lambda x: x[1], reverse=True)
        results = [(self.chunks[i], s) for i, s in scores[:top_k]]
        return results

    def format_search_results(self, query: str, path_filter: Optional[str] = None, top_k: int = 5) -> str:
        """Execute search and return formatted summary string."""
        matches = self.search(query, path_filter=path_filter, top_k=top_k)
        if not matches:
            return f"No code snippets found matching query '{query}'."

        res = [f"Found {len(matches)} relevant code block(s) for '{query}':\n"]
        for rank, (chunk, score) in enumerate(matches, start=1):
            res.append(f"--- [{rank}] {chunk.file_path}:{chunk.start_line}-{chunk.end_line} (score: {score:.2f}) ---")
            # Truncate preview if very long
            preview_lines = chunk.content.splitlines()[:20]
            preview = "\n".join(preview_lines)
            if len(chunk.content.splitlines()) > 20:
                preview += "\n... [lines omitted]"
            res.append(preview)
            res.append("")

        return "\n".join(res)

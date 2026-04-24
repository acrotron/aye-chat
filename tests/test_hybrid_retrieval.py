"""Unit tests for the hybrid retrieval signals (filename boost + BM25 + fusion)."""

import sys
import unittest
from pathlib import Path

try:
    from aye.model.hybrid_retrieval import (
        BM25,
        compute_filename_boost,
        extract_path_mentions,
        hybrid_rerank,
        rrf_fuse,
        tokenize,
    )
    from aye.model.models import VectorIndexResult
except ImportError:
    project_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(project_root / "src"))
    from aye.model.hybrid_retrieval import (
        BM25,
        compute_filename_boost,
        extract_path_mentions,
        hybrid_rerank,
        rrf_fuse,
        tokenize,
    )
    from aye.model.models import VectorIndexResult


class TestTokenize(unittest.TestCase):
    def test_basic_words(self):
        self.assertEqual(tokenize("fix auth bug"), ["fix", "auth", "bug"])

    def test_lowercases(self):
        self.assertEqual(tokenize("FIX Auth BUG"), ["fix", "auth", "bug"])

    def test_splits_snake_case(self):
        self.assertEqual(tokenize("fetch_user_data"), ["fetch", "user", "data"])

    def test_splits_camel_case(self):
        self.assertEqual(tokenize("fetchUserData"), ["fetch", "user", "data"])

    def test_splits_pascal_case(self):
        self.assertEqual(tokenize("AuthService"), ["auth", "service"])

    def test_splits_acronym_boundary(self):
        self.assertEqual(tokenize("HTTPServer"), ["http", "server"])

    def test_strips_punctuation(self):
        # Periods and dashes are separators.
        self.assertEqual(tokenize("auth.py"), ["auth", "py"])
        self.assertEqual(tokenize("foo-bar"), ["foo", "bar"])

    def test_empty_and_none(self):
        self.assertEqual(tokenize(""), [])


class TestExtractPathMentions(unittest.TestCase):
    def test_filename_with_extension(self):
        self.assertEqual(extract_path_mentions("fix auth.py"), ["auth.py"])

    def test_relative_path_with_slash(self):
        self.assertEqual(
            extract_path_mentions("update src/config.py please"),
            ["src/config.py"],
        )

    def test_backslash_normalized(self):
        self.assertEqual(
            extract_path_mentions(r"touch src\config.py"),
            ["src/config.py"],
        )

    def test_no_paths(self):
        self.assertEqual(extract_path_mentions("fix the database bug"), [])

    def test_multiple_paths(self):
        result = extract_path_mentions("diff a.py and b/c.ts")
        self.assertIn("a.py", result)
        self.assertIn("b/c.ts", result)


class TestComputeFilenameBoost(unittest.TestCase):
    def test_full_path_match_scores_highest(self):
        scores = compute_filename_boost(
            "update src/config.py to set DEBUG=False",
            ["src/config.py", "src/other.py"],
        )
        self.assertEqual(scores["src/config.py"], 1.0)
        self.assertNotIn("src/other.py", scores)

    def test_basename_match(self):
        scores = compute_filename_boost(
            "fix auth.py",
            ["src/auth.py", "src/unrelated.py"],
        )
        self.assertEqual(scores["src/auth.py"], 0.9)

    def test_stem_match_as_word(self):
        scores = compute_filename_boost(
            "fix the config module",
            ["src/config.py"],
        )
        self.assertEqual(scores["src/config.py"], 0.5)

    def test_case_insensitive(self):
        scores = compute_filename_boost(
            "fix AUTH.PY",
            ["src/auth.py"],
        )
        self.assertEqual(scores["src/auth.py"], 0.9)

    def test_substring_does_not_falsely_match_basename(self):
        # "config" appears inside "reconfig" but config.py should not match as a basename.
        scores = compute_filename_boost(
            "please reconfigure the service",
            ["config.py"],
        )
        self.assertNotIn("config.py", scores)

    def test_empty_query_or_paths(self):
        self.assertEqual(compute_filename_boost("", ["a.py"]), {})
        self.assertEqual(compute_filename_boost("something", []), {})

    def test_windows_path_in_query(self):
        scores = compute_filename_boost(
            r"edit src\config.py now",
            ["src/config.py"],
        )
        self.assertEqual(scores["src/config.py"], 1.0)


class TestBM25(unittest.TestCase):
    def test_empty_corpus(self):
        bm25 = BM25.from_documents({})
        self.assertEqual(bm25.get_scores("anything"), {})

    def test_ranks_matching_doc_above_non_matching(self):
        corpus = {
            "config.py": "DATABASE_URL postgres connection string",
            "utils.py": "helper function returns forty two",
        }
        bm25 = BM25.from_documents(corpus)
        scores = bm25.get_scores("database url")
        self.assertIn("config.py", scores)
        self.assertNotIn("utils.py", scores)

    def test_rare_term_outranks_common_term(self):
        corpus = {
            "common.py": "the the the the function foo bar",
            "rare.py": "the zxqqyyy special token here",
            "other.py": "the another random content",
        }
        bm25 = BM25.from_documents(corpus)
        # Query with rare term: rare.py should score highest.
        scores = bm25.get_scores("zxqqyyy")
        ordered = sorted(scores, key=lambda d: scores[d], reverse=True)
        self.assertEqual(ordered[0], "rare.py")

    def test_empty_query(self):
        bm25 = BM25.from_documents({"a.py": "some content"})
        self.assertEqual(bm25.get_scores(""), {})

    def test_query_with_no_matches(self):
        bm25 = BM25.from_documents({"a.py": "some content"})
        self.assertEqual(bm25.get_scores("completely_foreign_word"), {})

    def test_snake_case_query_matches_snake_case_content(self):
        corpus = {
            "a.py": "def fetch_user_data():\n    pass",
            "b.py": "def some_other_func():\n    pass",
        }
        bm25 = BM25.from_documents(corpus)
        scores = bm25.get_scores("fetch_user_data")
        self.assertIn("a.py", scores)
        self.assertNotIn("b.py", scores)


class TestRrfFuse(unittest.TestCase):
    def test_single_list_preserves_order(self):
        scores = rrf_fuse([["a", "b", "c"]], k=10)
        ordered = sorted(scores, key=lambda x: scores[x], reverse=True)
        self.assertEqual(ordered, ["a", "b", "c"])

    def test_combines_multiple_lists(self):
        # File in top position of both lists should rank first after fusion.
        scores = rrf_fuse(
            [
                ["a", "b", "c"],
                ["a", "d", "e"],
            ],
            k=60,
        )
        ordered = sorted(scores, key=lambda x: scores[x], reverse=True)
        self.assertEqual(ordered[0], "a")

    def test_ignores_empty_lists(self):
        scores = rrf_fuse([[], ["a", "b"]])
        self.assertEqual(set(scores.keys()), {"a", "b"})

    def test_all_empty(self):
        self.assertEqual(rrf_fuse([]), {})
        self.assertEqual(rrf_fuse([[], []]), {})


class TestHybridRerank(unittest.TestCase):
    def _vr(self, path, score, content="..."):
        return VectorIndexResult(file_path=path, content=content, score=score)

    def test_passthrough_when_no_signals(self):
        # No BM25, no known file paths → return vector_results unchanged.
        results = [self._vr("a.py", 0.9), self._vr("b.py", 0.5)]
        out = hybrid_rerank(results, "anything", bm25=None, all_file_paths=[])
        self.assertEqual([r.file_path for r in out], ["a.py", "b.py"])
        self.assertEqual(out[0].content, "...")

    def test_filename_mention_promotes_to_top(self):
        # auth.py is buried at rank 3 in vector results but mentioned by name in the query.
        vector = [
            self._vr("database.py", 0.95),
            self._vr("settings.py", 0.90),
            self._vr("auth.py", 0.70),
        ]
        out = hybrid_rerank(
            vector,
            "fix the bug in auth.py",
            bm25=None,
            all_file_paths=["database.py", "settings.py", "auth.py"],
        )
        self.assertEqual(out[0].file_path, "auth.py")

    def test_full_path_mention_promotes(self):
        vector = [
            self._vr("src/database.py", 0.95),
            self._vr("src/config.py", 0.60),
        ]
        out = hybrid_rerank(
            vector,
            "update src/config.py please",
            bm25=None,
            all_file_paths=["src/database.py", "src/config.py"],
        )
        self.assertEqual(out[0].file_path, "src/config.py")

    def test_dedupes_chunks_to_file(self):
        # Same file appears three times in vector results (multiple chunks).
        vector = [
            self._vr("config.py", 0.9, "chunk-a"),
            self._vr("config.py", 0.7, "chunk-b"),
            self._vr("config.py", 0.8, "chunk-c"),
            self._vr("main.py", 0.6, "main"),
        ]
        out = hybrid_rerank(
            vector,
            "query",
            bm25=None,
            all_file_paths=["config.py", "main.py"],
        )
        paths = [r.file_path for r in out]
        self.assertEqual(paths.count("config.py"), 1)
        # The retained chunk should be the highest-scoring one.
        retained = next(r for r in out if r.file_path == "config.py")
        self.assertEqual(retained.content, "chunk-a")

    def test_bm25_surfaces_file_absent_from_vector_results(self):
        # Vector returns an unrelated file; BM25 catches the rare term in another.
        vector = [self._vr("unrelated.py", 0.5, "noise")]
        corpus = {
            "unrelated.py": "the the the noise",
            "special.py": "the zxqqyyy special",
        }
        bm25 = BM25.from_documents(corpus)
        out = hybrid_rerank(
            vector,
            "zxqqyyy",
            bm25=bm25,
            all_file_paths=list(corpus.keys()),
        )
        paths = [r.file_path for r in out]
        self.assertIn("special.py", paths)
        self.assertLess(paths.index("special.py"), paths.index("unrelated.py"))

    def test_strong_mention_beats_high_vector_score(self):
        # Vector puts other.py well ahead, but query mentions target.py by name.
        vector = [
            self._vr("other.py", 0.99),
            self._vr("target.py", 0.10),
        ]
        out = hybrid_rerank(
            vector,
            "update target.py to fix the thing",
            bm25=None,
            all_file_paths=["other.py", "target.py"],
        )
        self.assertEqual(out[0].file_path, "target.py")

    def test_multiple_strong_mentions_both_at_top(self):
        vector = [
            self._vr("noise.py", 0.95),
        ]
        out = hybrid_rerank(
            vector,
            "sync a.py with b.py",
            bm25=None,
            all_file_paths=["noise.py", "a.py", "b.py"],
        )
        top_two = {out[0].file_path, out[1].file_path}
        self.assertEqual(top_two, {"a.py", "b.py"})

    def test_empty_vector_results_still_returns_filename_matches(self):
        # If vector retrieval finds nothing, filename boost can still surface a file.
        out = hybrid_rerank(
            [],
            "edit auth.py",
            bm25=None,
            all_file_paths=["auth.py", "other.py"],
        )
        self.assertGreater(len(out), 0)
        self.assertEqual(out[0].file_path, "auth.py")


if __name__ == "__main__":
    unittest.main()

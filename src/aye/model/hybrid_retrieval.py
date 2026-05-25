"""Hybrid retrieval signals layered on top of the vector index.

This module adds two non-embedding signals to file retrieval:

1. Filename / path boost: when the user's prompt mentions a file name, path,
   or identifier that matches an indexed file, that file is promoted to the
   top of the result list regardless of embedding similarity.

2. BM25 lexical search: a classic sparse-retrieval score over file contents
   that surfaces files containing rare tokens from the query (error strings,
   function names) even when semantic similarity is diluted.

The two signals are combined with the embedding-based ranking using
Reciprocal Rank Fusion. Strong filename matches bypass fusion and are
prepended to the output unconditionally.
"""
import math
import re
from collections import Counter
from typing import Dict, List, Optional

from aye.model.models import VectorIndexResult


_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*|\d+")
_CAMEL_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")
_PATH_LIKE_RE = re.compile(r"[A-Za-z0-9_./\\-]*[A-Za-z0-9_][A-Za-z0-9_./\\-]*\.[A-Za-z0-9]+|[A-Za-z0-9_.-]+/[A-Za-z0-9_./\\-]+")

# Filename-match tiers. A file with BOOST_BASENAME or higher is considered
# explicitly mentioned and bypasses fusion to take a top slot.
BOOST_FULL_PATH = 1.0
BOOST_BASENAME = 0.9
# Slightly below basename: a symbol mention is strong evidence, but when both
# a filename and a symbol are named we prefer the filename as the more
# deliberate reference.
BOOST_SYMBOL = 0.85
BOOST_PARTIAL_PATH = 0.7
BOOST_STEM = 0.5
STRONG_MENTION_THRESHOLD = BOOST_BASENAME

# Symbols shared by more than this many files are considered ambiguous and
# excluded from the boost. A name like `init` or `get` typically appears in
# dozens of files; boosting all of them is noise.
MAX_SYMBOL_FILES = 3


def tokenize(text: str) -> List[str]:
    """Split text into lowercase word tokens, breaking snake_case and camelCase.

    Used for both BM25 indexing and query analysis. Non-alphanumeric characters
    act purely as separators; dots, slashes, and dashes are dropped.
    """
    if not text:
        return []

    tokens: List[str] = []
    for word in _WORD_RE.findall(text):
        for part in word.split("_"):
            if not part:
                continue
            for sub in _CAMEL_RE.findall(part):
                if sub:
                    tokens.append(sub.lower())
    return tokens


def extract_path_mentions(query: str) -> List[str]:
    """Return path-like tokens found in the query (e.g. 'auth.py', 'src/foo.py').

    Paths are normalized to forward slashes and lowercased so they can be
    compared against indexed file paths regardless of OS conventions.
    """
    if not query:
        return []
    raw = _PATH_LIKE_RE.findall(query)
    return [m.replace("\\", "/").lower() for m in raw]


def _normalize_path(path: str) -> str:
    return path.replace("\\", "/").lower()


def compute_filename_boost(
    query: str,
    file_paths: List[str],
    query_tokens: Optional[List[str]] = None,
) -> Dict[str, float]:
    """Score each file path by how strongly the query mentions it.

    Scoring tiers (highest wins per file): full path, basename, partial path
    substring, standalone stem token. Files with no mention are absent from
    the returned dict. Pass `query_tokens` to reuse an already-tokenized
    query and avoid redundant work.
    """
    if not query or not file_paths:
        return {}

    q_norm = _normalize_path(query)
    mentions = extract_path_mentions(query)
    q_token_set = set(query_tokens) if query_tokens is not None else set(tokenize(query))

    scores: Dict[str, float] = {}
    for fp in file_paths:
        fp_norm = _normalize_path(fp)
        basename = fp_norm.rsplit("/", 1)[-1]
        stem = basename.rsplit(".", 1)[0]

        if fp_norm and fp_norm in q_norm:
            scores[fp] = BOOST_FULL_PATH
            continue

        # Basename must be surrounded by non-identifier chars to avoid matching
        # "config.py" inside "reconfig.python"; we check it as a word.
        if basename and _contains_as_word(q_norm, basename):
            scores[fp] = BOOST_BASENAME
            continue

        if any(m == fp_norm or (m and m in fp_norm) or (fp_norm and fp_norm in m) for m in mentions):
            scores[fp] = BOOST_PARTIAL_PATH
            continue

        if stem and stem in q_token_set:
            scores[fp] = BOOST_STEM

    return scores


def _contains_as_word(haystack: str, needle: str) -> bool:
    """Check whether `needle` appears in `haystack` bordered by non-identifier chars."""
    idx = 0
    n = len(needle)
    while True:
        found = haystack.find(needle, idx)
        if found == -1:
            return False
        before_ok = found == 0 or not _is_ident_char(haystack[found - 1])
        after = found + n
        after_ok = after == len(haystack) or not _is_ident_char(haystack[after])
        if before_ok and after_ok:
            return True
        idx = found + 1


def _is_ident_char(ch: str) -> bool:
    return ch.isalnum() or ch == "_"


class BM25:  # pylint: disable=too-many-instance-attributes
    """Pure-Python BM25 over a fixed document corpus keyed by document id.

    Suitable for a few thousand documents. Not thread-safe; rebuild the
    index when the underlying corpus changes.
    """

    def __init__(
        self,
        doc_ids: List[str],
        tokenized_docs: List[List[str]],
        k1: float = 1.5,
        b: float = 0.75,
    ):
        if len(doc_ids) != len(tokenized_docs):
            raise ValueError("doc_ids and tokenized_docs must have equal length")

        self.doc_ids = list(doc_ids)
        self.k1 = k1
        self.b = b
        self.n_docs = len(doc_ids)
        self.doc_freqs: List[Counter] = []
        self.doc_lens: List[int] = []
        self.idf: Dict[str, float] = {}

        df: Counter = Counter()
        for doc in tokenized_docs:
            freq = Counter(doc)
            self.doc_freqs.append(freq)
            self.doc_lens.append(len(doc))
            for term in freq:
                df[term] += 1

        total_len = sum(self.doc_lens)
        self.avgdl = total_len / self.n_docs if self.n_docs else 0.0
        for term, dfreq in df.items():
            # BM25+ IDF floor to keep scores non-negative for common terms.
            self.idf[term] = math.log(1 + (self.n_docs - dfreq + 0.5) / (dfreq + 0.5))

    @classmethod
    def from_documents(cls, doc_map: Dict[str, str]) -> "BM25":
        """Build a BM25 index from a {doc_id: raw_text} mapping."""
        doc_ids = list(doc_map.keys())
        tokenized = [tokenize(doc_map[d]) for d in doc_ids]
        return cls(doc_ids, tokenized)

    def get_scores(
        self, query: str, query_tokens: Optional[List[str]] = None
    ) -> Dict[str, float]:
        """Return {doc_id: score} for docs with at least one matching term.

        Pass `query_tokens` to reuse an already-tokenized query.
        """
        if self.n_docs == 0:
            return {}

        q_tokens = query_tokens if query_tokens is not None else tokenize(query)
        if not q_tokens:
            return {}

        scores: Dict[str, float] = {}
        for i, doc_id in enumerate(self.doc_ids):
            freq = self.doc_freqs[i]
            dl = self.doc_lens[i]
            score = 0.0
            for term in q_tokens:
                tf = freq.get(term)
                if not tf:
                    continue
                idf = self.idf.get(term, 0.0)
                denom = tf + self.k1 * (1 - self.b + self.b * dl / self.avgdl) if self.avgdl else tf
                score += idf * (tf * (self.k1 + 1)) / denom if denom else 0.0
            if score > 0:
                scores[doc_id] = score
        return scores


class SymbolIndex:
    """Maps symbol names (function/class identifiers) to the files defining them.

    Lookups are case-insensitive. Names that appear in more than
    `MAX_SYMBOL_FILES` files are suppressed on lookup to avoid boosting every
    file that happens to define a common name like `init` or `get`.
    """

    def __init__(self, max_symbol_files: int = MAX_SYMBOL_FILES):
        self._by_name: Dict[str, List[str]] = {}
        self._max = max_symbol_files

    def add(self, file_path: str, symbols: List[str]) -> None:
        """Record `symbols` as defined in `file_path`."""
        for sym in symbols:
            if not sym:
                continue
            key = sym.lower()
            bucket = self._by_name.setdefault(key, [])
            if file_path not in bucket:
                bucket.append(file_path)

    def files_for(self, name: str) -> List[str]:
        """Return files defining `name`, or [] if absent or ambiguous."""
        bucket = self._by_name.get(name.lower())
        if not bucket or len(bucket) > self._max:
            return []
        return list(bucket)

    def __len__(self) -> int:
        """Return the number of unique symbol names (not the number of files)."""
        return len(self._by_name)


def compute_symbol_boost(
    query: str,
    symbol_index: Optional["SymbolIndex"],
    query_tokens: Optional[List[str]] = None,  # pylint: disable=unused-argument
) -> Dict[str, float]:
    """Return files defining any identifier-like token present in the query.

    Only tokens that look like source identifiers (snake_case, camelCase,
    SCREAMING_CASE, or containing a digit — i.e. unlikely to be prose) are
    used, so common words like `fix` or `update` never match symbols even
    when a file happens to define a function with that exact name. The
    `query_tokens` parameter is accepted for API symmetry but not used, since
    symbol matching needs case-preserving tokens that `tokenize()` discards.
    """
    if symbol_index is None or len(symbol_index) == 0 or not query:
        return {}

    candidates = {t.lower() for t in _identifier_like_tokens(query)}
    if not candidates:
        return {}

    scores: Dict[str, float] = {}
    for candidate in candidates:
        for fp in symbol_index.files_for(candidate):
            if fp not in scores:
                scores[fp] = BOOST_SYMBOL
    return scores


_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _identifier_like_tokens(query: str) -> List[str]:
    """Pull tokens from the query that plausibly name a source identifier.

    We consider a token identifier-like if it contains an underscore, a digit,
    or a lower→upper case transition (camelCase). Plain English words are
    excluded so that `fix`, `update`, `the` can't accidentally match a symbol.
    """
    out: List[str] = []
    for tok in _IDENT_RE.findall(query or ""):
        if "_" in tok or any(c.isdigit() for c in tok):
            out.append(tok)
            continue
        # camelCase / PascalCase detection: a lower-then-upper transition.
        has_lower_upper = any(
            tok[i].islower() and tok[i + 1].isupper() for i in range(len(tok) - 1)
        )
        # A pure-uppercase token of length >= 2 is also plausibly an identifier
        # (e.g. HTTPServer, SECRET_KEY constant referenced as SECRET).
        if has_lower_upper or (len(tok) >= 2 and tok.isupper()):
            out.append(tok)
    return out


def rrf_fuse(rankings: List[List[str]], k: int = 60) -> Dict[str, float]:
    """Combine multiple ranked lists into a single score map via Reciprocal Rank Fusion.

    Each item's contribution from a list is 1 / (k + rank_position).
    Empty lists are ignored. The constant k dampens the effect of
    early-position noise; the standard literature value is 60.
    """
    fused: Dict[str, float] = {}
    for rank_list in rankings:
        for position, item in enumerate(rank_list):
            fused[item] = fused.get(item, 0.0) + 1.0 / (k + position + 1)
    return fused


def hybrid_rerank(
    vector_results: List[VectorIndexResult],
    query: str,
    bm25: Optional[BM25],
    all_file_paths: List[str],
    symbol_index: Optional[SymbolIndex] = None,
) -> List[VectorIndexResult]:
    """Re-rank a list of vector results using BM25, filename, and symbol signals.

    The input is a list of per-chunk results (as returned by the vector DB);
    the output is a list of per-file results in the recommended examination
    order. Files mentioned explicitly by name or path in the query, or files
    defining an identifier named in the query, are placed at the top; the
    remainder is fused from vector, BM25, and weak filename rankings using
    Reciprocal Rank Fusion.

    If no ancillary signals are available (no BM25, no known file paths, no
    symbol index), the function is a no-op that returns the input unchanged.
    """
    if bm25 is None and not all_file_paths and symbol_index is None:
        return list(vector_results)

    file_to_best: Dict[str, VectorIndexResult] = {}
    for r in vector_results:
        existing = file_to_best.get(r.file_path)
        if existing is None or r.score > existing.score:
            file_to_best[r.file_path] = r

    q_tokens = tokenize(query)
    filename_scores = compute_filename_boost(query, all_file_paths, query_tokens=q_tokens)
    symbol_scores = compute_symbol_boost(query, symbol_index, query_tokens=q_tokens)

    # Strong mentions include both explicit filename/path matches and symbol
    # definitions: either one is a near-certain signal of user intent.
    strong_candidates = dict(symbol_scores)
    for fp, s in filename_scores.items():
        if s >= STRONG_MENTION_THRESHOLD:
            strong_candidates[fp] = max(strong_candidates.get(fp, 0.0), s)

    strong_mentions = sorted(
        strong_candidates,
        key=lambda fp: (-strong_candidates[fp], fp),
    )

    vector_ranked = sorted(
        file_to_best.keys(),
        key=lambda fp: (-file_to_best[fp].score, fp),
    )

    bm25_scores = bm25.get_scores(query, query_tokens=q_tokens) if bm25 is not None else {}
    bm25_ranked = sorted(bm25_scores, key=lambda fp: (-bm25_scores[fp], fp))

    weak_filename_ranked = sorted(
        (fp for fp, s in filename_scores.items() if 0 < s < STRONG_MENTION_THRESHOLD),
        key=lambda fp: (-filename_scores[fp], fp),
    )

    fused = rrf_fuse([vector_ranked, bm25_ranked, weak_filename_ranked])

    strong_set = set(strong_mentions)
    fused_ranked = sorted(
        (fp for fp in fused if fp not in strong_set),
        key=lambda fp: (-fused[fp], fp),
    )

    ordered = list(strong_mentions) + fused_ranked
    seen = set()
    output: List[VectorIndexResult] = []
    for fp in ordered:
        if fp in seen:
            continue
        seen.add(fp)
        best = file_to_best.get(fp)
        if best is not None:
            output.append(best)
        else:
            output.append(VectorIndexResult(file_path=fp, content="", score=0.0))
    return output

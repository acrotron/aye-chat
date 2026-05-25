from typing import List, Optional
from pathlib import Path

try:
    from tree_sitter import Language, Parser
    from tree_sitter_languages import get_language, get_parser
    TREE_SITTER_AVAILABLE = True
except ImportError:
    TREE_SITTER_AVAILABLE = False
    get_language = None
    get_parser = None

# Mapping from file extensions to tree-sitter language names
# This should cover common languages found in projects.
LANGUAGE_MAP = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".java": "java",
    ".c": "c",
    ".cpp": "cpp",
    ".h": "c",
    ".hpp": "cpp",
    ".rs": "rust",
    ".go": "go",
    ".rb": "ruby",
    ".html": "html",
    ".css": "css",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".sh": "bash",
}

# Queries to find top-level nodes like functions and classes.
# These are language-specific and aim to capture logical blocks of code.
CHUNK_QUERIES = {
    "python": """
    (function_definition) @chunk
    (class_definition) @chunk
    """,
    "javascript": """
    (function_declaration) @chunk
    (class_declaration) @chunk
    (lexical_declaration
        (variable_declarator
            value: [(arrow_function) (function)])) @chunk
    (export_statement declaration: [(function_declaration) (class_declaration)]) @chunk
    """,
    "typescript": """
    (function_declaration) @chunk
    (class_declaration) @chunk
    (lexical_declaration
        (variable_declarator
            value: [(arrow_function) (function)])) @chunk
    (export_statement declaration: [(function_declaration) (class_declaration)]) @chunk
    """,
    "tsx": """
    (function_declaration) @chunk
    (class_declaration) @chunk
    (lexical_declaration
        (variable_declarator
            value: [(arrow_function) (function)])) @chunk
    (export_statement declaration: [(function_declaration) (class_declaration)]) @chunk
    """,
    "java": """
    (class_declaration) @chunk
    (interface_declaration) @chunk
    (method_declaration) @chunk
    """,
    "c": """
    (function_definition) @chunk
    (struct_specifier) @chunk
    (union_specifier) @chunk
    (enum_specifier) @chunk
    """,
    "cpp": """
    (function_definition) @chunk
    (class_specifier) @chunk
    (struct_specifier) @chunk
    (namespace_definition) @chunk
    """,
    "rust": """
    (function_item) @chunk
    (struct_item) @chunk
    (impl_item) @chunk
    (trait_item) @chunk
    (enum_item) @chunk
    """,
    "go": """
    (function_declaration) @chunk
    (method_declaration) @chunk
    (type_declaration) @chunk
    """,
    "ruby": """
    (method) @chunk
    (class) @chunk
    (module) @chunk
    """,
    "html": """
    (element) @chunk
    (script_element) @chunk
    (style_element) @chunk
    """,
    "css": """
    (rule_set) @chunk
    """,
    "bash": """
    (function_definition) @chunk
    """
}

def get_language_from_file_path(file_path: str) -> Optional[str]:
    """Determine the tree-sitter language name from a file path."""
    suffix = Path(file_path).suffix.lower()
    return LANGUAGE_MAP.get(suffix)

def _parse_and_capture(content: str, language_name: str) -> Optional[list]:
    """Parse `content` and run the language's CHUNK_QUERIES, returning captures.

    Returns None if tree-sitter is missing, the language is unsupported, or
    parsing/querying raised. Returns a (possibly empty) list on success; an
    empty list means the grammar ran cleanly but matched nothing.
    """
    if not TREE_SITTER_AVAILABLE:
        return None

    query_string = CHUNK_QUERIES.get(language_name)
    if not query_string:
        return None

    try:
        language = get_language(language_name)
        parser = get_parser(language_name)
        tree = parser.parse(bytes(content, "utf8"))
        query = language.query(query_string)
        return query.captures(tree.root_node)
    except Exception:
        return None


def extract_symbols(content: str, language_name: str) -> List[str]:
    """Return top-level function/class/method names defined in the source.

    Uses the same AST queries as `ast_chunker` but captures the `name` field
    of each matched node. Returns an empty list if tree-sitter is unavailable,
    the language has no query, parsing fails, or the grammar's captured node
    doesn't expose a 'name' field (e.g. CSS rule sets, HTML elements).
    """
    captures = _parse_and_capture(content, language_name)
    if captures is None:
        return []

    symbols: List[str] = []
    for node, _ in captures:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            continue
        try:
            symbols.append(name_node.text.decode("utf8"))
        except Exception:
            continue
    return symbols


def ast_chunker(content: str, language_name: str) -> List[str]:
    """
    Chunks code using tree-sitter to extract AST-based chunks.
    Returns an empty list if tree-sitter is not available, the language is
    not supported, or no chunks are found.
    """
    captures = _parse_and_capture(content, language_name)
    if captures is None:
        return []

    chunks = [node.text.decode('utf8') for node, _ in captures]

    # If no high-level chunks are found, but the file has content,
    # return the whole file as a single chunk. This is better than nothing.
    if not chunks and content.strip():
        return [content]

    return chunks

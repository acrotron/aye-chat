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

def ast_chunker(content: str, language_name: str) -> List[str]:
    """
    Chunks code using tree-sitter to extract AST-based chunks.
    Returns an empty list if tree-sitter is not available, the language is
    not supported, or no chunks are found.
    """
    if not TREE_SITTER_AVAILABLE:
        return []

    try:
        language = get_language(language_name)
        parser = get_parser(language_name)
    except Exception:
        # Language not supported or tree-sitter-languages not fully installed
        return []

    tree = parser.parse(bytes(content, "utf8"))
    root_node = tree.root_node

    query_string = CHUNK_QUERIES.get(language_name)
    if not query_string:
        return []  # No query defined for this language

    try:
        query = language.query(query_string)
        captures = query.captures(root_node)
    except Exception:
        # tree-sitter might fail on invalid syntax or query errors
        return []

    chunks = []
    for node, _ in captures:
        chunk_text = node.text.decode('utf8')
        chunks.append(chunk_text)

    # If no high-level chunks are found, but the file has content,
    # return the whole file as a single chunk. This is better than nothing.
    if not chunks and content.strip():
        return [content]

    return chunks

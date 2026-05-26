"""Code-aware chunking via tree-sitter.

For supported languages we walk the AST and emit one chunk per top-level
definition (or pack adjacent small definitions together). Each chunk carries
its declared symbol names so callers can filter recalls by symbol
(``filter={"defines": "MyClass"}``).

When a language is unsupported, the tree-sitter wheel is missing, or parsing
fails, this module falls back transparently to the prose chunker in
:mod:`qilin.chunking`. That keeps ingest robust on platforms where the
native dependency cannot be loaded.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .chunking import Chunk, _AtomicUnit, chunk_text, count_tokens
from .config import Settings, get_settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _LanguageSpec:
    """Per-language tree-sitter node mapping."""

    definition_types: frozenset[str]
    import_types: frozenset[str]
    name_field: str = "name"
    qualified_method_prefix: bool = True


# A deliberately small first cohort. Adding more languages is a matter of
# probing the tree-sitter grammar with a sample file and filling in the
# definition/import node types.
LANGUAGE_SPECS: dict[str, _LanguageSpec] = {
    "python": _LanguageSpec(
        definition_types=frozenset({"function_definition", "class_definition"}),
        import_types=frozenset({"import_statement", "import_from_statement"}),
    ),
    "go": _LanguageSpec(
        definition_types=frozenset(
            {
                "function_declaration",
                "method_declaration",
                "type_declaration",
            }
        ),
        import_types=frozenset({"import_declaration"}),
    ),
    "javascript": _LanguageSpec(
        definition_types=frozenset(
            {
                "function_declaration",
                "class_declaration",
                "method_definition",
                "generator_function_declaration",
            }
        ),
        import_types=frozenset({"import_statement"}),
    ),
    "typescript": _LanguageSpec(
        definition_types=frozenset(
            {
                "function_declaration",
                "class_declaration",
                "method_definition",
                "interface_declaration",
                "type_alias_declaration",
                "enum_declaration",
                "abstract_class_declaration",
            }
        ),
        import_types=frozenset({"import_statement"}),
    ),
    "tsx": _LanguageSpec(
        definition_types=frozenset(
            {
                "function_declaration",
                "class_declaration",
                "method_definition",
                "interface_declaration",
                "type_alias_declaration",
                "enum_declaration",
            }
        ),
        import_types=frozenset({"import_statement"}),
    ),
    "rust": _LanguageSpec(
        definition_types=frozenset(
            {
                "function_item",
                "impl_item",
                "struct_item",
                "enum_item",
                "trait_item",
                "mod_item",
            }
        ),
        import_types=frozenset({"use_declaration"}),
    ),
}


def is_supported(language: str | None) -> bool:
    """Return True iff Qilin has an AST recipe for this language."""
    return bool(language) and language.lower() in LANGUAGE_SPECS


def _load_parser(language: str):
    """Return a parser for ``language`` or None if tree-sitter is unavailable.

    Wrapped so a missing native wheel never breaks ingest - the caller falls
    back to prose chunking when this returns None.
    """
    try:
        from tree_sitter import Parser
        from tree_sitter_language_pack import get_language
    except ImportError as exc:
        logger.warning(
            "tree-sitter not available; falling back to text chunking (%s)", exc
        )
        return None
    try:
        lang = get_language(language)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "tree-sitter grammar for %r not loadable; falling back: %s",
            language,
            exc,
        )
        return None
    return Parser(lang)


def _node_text(source_bytes: bytes, node: Any) -> str:
    return source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _node_name(node: Any, name_field: str) -> str | None:
    child = node.child_by_field_name(name_field)
    if child is not None:
        raw = (child.text or b"").decode("utf-8", errors="replace")
        if raw:
            return raw
    # Some grammars (Go's ``type_declaration``, Rust's ``use_declaration``) put
    # the name one level deeper. Walk through known wrapper children.
    for wrapper_type in ("type_spec", "type_alias_spec", "const_spec", "var_spec"):
        for c in node.children:
            if c.type == wrapper_type:
                inner = c.child_by_field_name(name_field)
                if inner is not None:
                    raw = (inner.text or b"").decode("utf-8", errors="replace")
                    if raw:
                        return raw
    return None


def _qualified_method_names(
    source_bytes: bytes,
    class_node: Any,
    name_field: str,
) -> list[str]:
    """Return ``Class.method`` for each method defined inside ``class_node``."""
    class_name = _node_name(class_node, name_field)
    if class_name is None:
        return []
    out: list[str] = []
    body = class_node.child_by_field_name("body")
    if body is None:
        return [class_name]
    out.append(class_name)
    for child in body.children:
        if child.type in {
            "function_definition",
            "method_definition",
            "function_declaration",
        }:
            method_name = _node_name(child, name_field)
            if method_name:
                out.append(f"{class_name}.{method_name}")
    return out


def _first_line(node_text: str) -> str:
    for line in node_text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:200]
    return ""


_EXPORT_WRAPPERS: frozenset[str] = frozenset(
    {"export_statement", "export_default_statement"}
)


def _effective_top_level(root: Any) -> Iterable[Any]:
    """Yield top-level nodes, transparently unwrapping JS/TS export statements.

    A naked ``export function foo() {}`` is parsed as an ``export_statement``
    containing a ``function_declaration``; for chunking purposes we want to
    see the inner declaration directly.
    """
    for child in root.children:
        if child.type in _EXPORT_WRAPPERS:
            yielded_any = False
            for grand in child.children:
                if grand.type in _EXPORT_WRAPPERS:
                    continue
                if grand.type in {"export", "default", ";", "{", "}"}:
                    continue
                yield grand
                yielded_any = True
            if not yielded_any:
                yield child
        else:
            yield child


def _collect_imports(source_bytes: bytes, root: Any, spec: _LanguageSpec) -> list[str]:
    imports: list[str] = []
    for child in _effective_top_level(root):
        if child.type in spec.import_types:
            text = _node_text(source_bytes, child).strip()
            if text:
                imports.append(text[:200])
    return imports


def _extract_definitions(
    source_bytes: bytes,
    root: Any,
    spec: _LanguageSpec,
) -> list[tuple[Any, tuple[str, ...]]]:
    """Walk top-level nodes and return ``(node, defines)`` pairs in source order.

    ``defines`` for a class node includes the class itself and its methods as
    ``Class.method``; for a function it is just the function name.
    """
    out: list[tuple[Any, tuple[str, ...]]] = []
    for child in _effective_top_level(root):
        if child.type not in spec.definition_types:
            continue
        defines: list[str] = []
        primary = _node_name(child, spec.name_field)
        if primary:
            defines.append(primary)
        if spec.qualified_method_prefix and child.type in {
            "class_definition",
            "class_declaration",
            "abstract_class_declaration",
        }:
            qualified = _qualified_method_names(source_bytes, child, spec.name_field)
            for q in qualified:
                if q not in defines:
                    defines.append(q)
        out.append((child, tuple(defines)))
    return out


def _split_class_into_methods(
    source_bytes: bytes,
    class_node: Any,
    spec: _LanguageSpec,
    max_tokens: int,
) -> list[tuple[Any, tuple[str, ...]]]:
    """When a class is too large, return its methods as separate atomic units."""
    class_name = _node_name(class_node, spec.name_field) or ""
    body = class_node.child_by_field_name("body")
    if body is None:
        return [(class_node, (class_name,) if class_name else ())]

    out: list[tuple[Any, tuple[str, ...]]] = []
    for child in body.children:
        if child.type not in {
            "function_definition",
            "method_definition",
            "function_declaration",
        }:
            continue
        method_name = _node_name(child, spec.name_field) or ""
        qualified = (
            f"{class_name}.{method_name}" if class_name and method_name else method_name
        )
        defines = (qualified,) if qualified else ()
        out.append((child, defines))

    method_text_tokens = sum(
        count_tokens(_node_text(source_bytes, n)) for n, _ in out
    )
    if not out or method_text_tokens == 0:
        return [(class_node, (class_name,) if class_name else ())]
    return out


def _definition_units(
    source_bytes: bytes,
    text: str,
    root: Any,
    spec: _LanguageSpec,
    max_tokens: int,
) -> Iterable[tuple[_AtomicUnit, tuple[str, ...], str | None]]:
    """Yield ``(unit, defines, signature)`` triples for each top-level definition.

    Definitions that don't fit in ``max_tokens`` are recursively expanded
    (classes split into methods; oversize methods/functions are hard-split via
    the text chunker).
    """
    for node, defines in _extract_definitions(source_bytes, root, spec):
        node_text = _node_text(source_bytes, node)
        token_count = count_tokens(node_text)
        if token_count <= max_tokens:
            yield (
                _AtomicUnit(
                    text=node_text,
                    token_count=token_count,
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                ),
                defines,
                _first_line(node_text),
            )
            continue

        if node.type in {
            "class_definition",
            "class_declaration",
            "abstract_class_declaration",
            "impl_item",
        }:
            method_units = _split_class_into_methods(
                source_bytes, node, spec, max_tokens
            )
            for method_node, method_defines in method_units:
                method_text = _node_text(source_bytes, method_node)
                method_tokens = count_tokens(method_text)
                if method_tokens <= max_tokens:
                    yield (
                        _AtomicUnit(
                            text=method_text,
                            token_count=method_tokens,
                            start_line=method_node.start_point[0] + 1,
                            end_line=method_node.end_point[0] + 1,
                        ),
                        method_defines,
                        _first_line(method_text),
                    )
                    continue
                yield from _hard_split_unit(
                    method_text,
                    method_node.start_point[0] + 1,
                    max_tokens,
                    method_defines,
                )
            continue

        yield from _hard_split_unit(
            node_text, node.start_point[0] + 1, max_tokens, defines
        )


def _hard_split_unit(
    text: str,
    start_line: int,
    max_tokens: int,
    defines: tuple[str, ...],
) -> Iterable[tuple[_AtomicUnit, tuple[str, ...], str | None]]:
    """Fall back to prose chunking when even a single definition is too big."""
    pieces = chunk_text(text, chunk_size_tokens=max_tokens, chunk_overlap_tokens=0)
    sig = _first_line(text)
    for piece in pieces:
        adjusted = _AtomicUnit(
            text=piece.text,
            token_count=piece.token_count,
            start_line=start_line + piece.start_line - 1,
            end_line=start_line + piece.end_line - 1,
        )
        yield adjusted, defines, sig


def chunk_code(
    text: str,
    language: str | None,
    *,
    chunk_size_tokens: int | None = None,
    chunk_overlap_tokens: int | None = None,
    settings: Settings | None = None,
) -> list[Chunk]:
    """Chunk ``text`` using language-aware boundaries when possible.

    Falls back to :func:`qilin.chunking.chunk_text` when ``language`` is None,
    unsupported, or when the tree-sitter native dependency cannot be loaded.
    """
    if not is_supported(language):
        return chunk_text(
            text,
            chunk_size_tokens=chunk_size_tokens,
            chunk_overlap_tokens=chunk_overlap_tokens,
            settings=settings,
        )

    assert language is not None  # narrowed by is_supported
    spec = LANGUAGE_SPECS[language.lower()]
    parser = _load_parser(language.lower())
    if parser is None:
        return chunk_text(
            text,
            chunk_size_tokens=chunk_size_tokens,
            chunk_overlap_tokens=chunk_overlap_tokens,
            settings=settings,
        )

    settings_obj = settings or get_settings()
    size = chunk_size_tokens or settings_obj.chunk_size_tokens
    overlap = chunk_overlap_tokens or settings_obj.chunk_overlap_tokens
    if overlap >= size:
        overlap = max(0, size // 5)

    source_bytes = text.encode("utf-8")
    try:
        tree = parser.parse(source_bytes)
    except Exception as exc:  # noqa: BLE001
        logger.warning("tree-sitter parse failed for %s: %s", language, exc)
        return chunk_text(
            text,
            chunk_size_tokens=chunk_size_tokens,
            chunk_overlap_tokens=chunk_overlap_tokens,
            settings=settings,
        )
    if tree is None or tree.root_node is None:
        return chunk_text(
            text,
            chunk_size_tokens=chunk_size_tokens,
            chunk_overlap_tokens=chunk_overlap_tokens,
            settings=settings,
        )

    root = tree.root_node
    imports = tuple(_collect_imports(source_bytes, root, spec))
    triples = list(_definition_units(source_bytes, text, root, spec, size))

    if not triples:
        # No definitions detected (e.g. a script or pure data file).
        return chunk_text(
            text,
            chunk_size_tokens=chunk_size_tokens,
            chunk_overlap_tokens=chunk_overlap_tokens,
            settings=settings,
        )

    chunks: list[Chunk] = []
    pending: list[tuple[_AtomicUnit, tuple[str, ...], str | None]] = []
    pending_tokens = 0

    def flush() -> None:
        nonlocal pending, pending_tokens
        if not pending:
            return
        joined_text = "\n\n".join(t[0].text for t in pending).strip()
        if not joined_text:
            pending = []
            pending_tokens = 0
            return
        start_line = min(t[0].start_line for t in pending)
        end_line = max(t[0].end_line for t in pending)
        defines: list[str] = []
        for _, defs, _ in pending:
            for d in defs:
                if d not in defines:
                    defines.append(d)
        signature = next((sig for _, _, sig in pending if sig), None)
        chunks.append(
            Chunk(
                text=joined_text,
                ordinal=len(chunks),
                token_count=pending_tokens,
                start_line=start_line,
                end_line=end_line,
                defines=tuple(defines),
                imports=imports,
                signature=signature,
            )
        )
        pending = []
        pending_tokens = 0

    for triple in triples:
        unit, _defs, _sig = triple
        if pending_tokens + unit.token_count > size and pending:
            flush()
        pending.append(triple)
        pending_tokens += unit.token_count

    flush()
    return chunks

# Extraction: FalkorDB code-graph → loom `indexer/`

Source clone: `<vendor-clone>/code-graph`
(package `falkordb-code-graph` v0.4.2, `pyproject.toml:1-4`).
All file:line references below are relative to that clone root.

There is **no separate analyzers repo or submodule**. The analyzers are `api/analyzers/` inside the
main repo. The Python analyzer is `api/analyzers/python/analyzer.py` plus
`api/analyzers/python/ts_resolver.py`.

Everything in ADOPT was verified by executing the queries against `tree-sitter==0.25.2` and
`0.26.0` with `tree-sitter-python` in a scratch venv; capture outputs are quoted inline.

---

## 1. LICENSE

`LICENSE:1-3` — **MIT License, Copyright (c) 2024 FalkorDB**.

Full permission grant: "to deal in the Software without restriction, including without limitation
the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies"
(`LICENSE:5-9`).

The one restriction that matters (`LICENSE:11-12`):

> The above copyright notice and this permission notice shall be included in all copies or
> substantial portions of the Software.

**Consequence for loom:** verbatim code and query strings may be copied. This is *not* a
patterns-only source. Requirement: carry a `NOTICE` line where the copied material lands —
put this at the top of `indexer/queries/python.py` (or `indexer/queries/NOTICE`):

```
# Portions derived from FalkorDB/code-graph (api/analyzers/python/).
# Copyright (c) 2024 FalkorDB. MIT License. See third_party/LICENSES/falkordb-code-graph.txt
```

and copy the clone's `LICENSE` verbatim to `third_party/LICENSES/falkordb-code-graph.txt`.
No copyleft, no attribution-in-docs requirement beyond that, no field-of-use restriction.

Dependency note: `pyproject.toml:8-28` pulls `falkordb`, `graphrag-sdk`, `falkordb-multilspy`,
`javatools`, `pygit2`. loom takes **none** of these — see REJECT.

---

## 2. ADOPT

### 2.1 Node kinds and the entity-type map

`api/analyzers/python/analyzer.py:26-34`:

```python
class PythonAnalyzer(TreeSitterAnalyzer):
    entity_node_types = {
        'class_definition': "Class",
        'function_definition': "Function",
    }
    type_definition_node_types = ('class_definition',)
    callable_definition_node_types = ('function_definition', 'class_definition')
    type_resolution_keys = ("base_class", "parameters", "return_type")
    method_resolution_keys = ("call",)
```

Two Python node types are graph entities: `class_definition` → `Class`,
`function_definition` → `Function`. `async def` also parses as `function_definition` in
tree-sitter-python, so async functions are covered for free. Lambdas, comprehensions and
module-level statements are *not* entities.

Dispatch helpers on the base class, `api/analyzers/tree_sitter_base.py:34-43`:

```python
    def get_entity_types(self) -> list[str]:
        return list(self.entity_node_types.keys())

    def get_entity_label(self, node: Node) -> str:
        try:
            return self.entity_node_types[node.type]
        except KeyError as exc:
            raise ValueError(f"Unknown entity type: {node.type}") from exc
```

### 2.2 Name and docstring extraction

`api/analyzers/python/analyzer.py:101-113`:

```python
    def get_entity_name(self, node: Node) -> str:
        if node.type in ['class_definition', 'function_definition']:
            return node.child_by_field_name('name').text.decode('utf-8')
        raise ValueError(f"Unknown entity type: {node.type}")

    def get_entity_docstring(self, node: Node) -> Optional[str]:
        if node.type in ['class_definition', 'function_definition']:
            body = node.child_by_field_name('body')
            if body.child_count > 0 and body.children[0].type == 'expression_statement':
                docstring_node = body.children[0].child(0)
                return docstring_node.text.decode('utf-8')
            return None
```

Name is the `name:` field, not a query. Docstring is "first child of the body block is an
`expression_statement`, take its first child" — no string-node type check, so a leading non-string
expression statement is mis-read as a docstring. Cheap fix in loom: guard on
`docstring_node.type == 'string'`.

### 2.3 The definition / method / import capture queries (the core lift)

`api/analyzers/python/ts_resolver.py:88-122` — copy these verbatim into `indexer/queries/python.py`:

```python
_QUERY_TOP_LEVEL_FUNC = """
(module (function_definition name: (identifier) @name) @def)
(module (decorated_definition
    definition: (function_definition name: (identifier) @name)) @def)
"""

_QUERY_TOP_LEVEL_CLASS = """
(module (class_definition name: (identifier) @name) @def)
(module (decorated_definition
    definition: (class_definition name: (identifier) @name)) @def)
"""

_QUERY_TOP_LEVEL_ASSIGN = """
(module (expression_statement (assignment left: (identifier) @name) @def))
"""

_QUERY_CLASS_METHODS = """
(class_definition
    name: (identifier) @class_name
    body: (block (function_definition name: (identifier) @method_name) @method_def))
(class_definition
    name: (identifier) @class_name
    body: (block (decorated_definition
        definition: (function_definition name: (identifier) @method_name) @method_def)))
"""

# Plain ``import x`` / ``import x.y`` / ``import x as y`` / ``import x.y as z``.
_QUERY_IMPORT = """
(import_statement) @stmt
"""

# ``from x import y`` / ``from x import y as z`` / ``from . import y`` / ``from .x import y``.
_QUERY_IMPORT_FROM = """
(import_from_statement) @stmt
"""
```

Verified capture output on a fixture containing a decorated class, a decorated method, a nested
function, and five import forms:

| query | matches |
|---|---|
| `_QUERY_TOP_LEVEL_FUNC` | `{'name': ['top'], 'def': [function_definition]}` |
| `_QUERY_TOP_LEVEL_CLASS` | `{'name': ['Foo'], 'def': [decorated_definition `@deco class Foo…`]}` |
| `_QUERY_TOP_LEVEL_ASSIGN` | `{'name': ['X'], 'def': ['X = Foo']}` |
| `_QUERY_CLASS_METHODS` | `{'class_name': ['Foo'], 'method_name': ['method'], 'method_def': [function_definition]}` |
| `_QUERY_IMPORT` / `_IMPORT_FROM` | `i: ['import os', 'import os.path as op']`, `f: ['from pkg.mod import thing as t, other', 'from . import sibling', 'from ..up import deep']` |

Note the decorated forms capture the **`decorated_definition`** as `@def`, which is why
`_strip_decorator` (§2.8) exists. `_QUERY_CLASS_METHODS` matches only methods that are direct
children of the class `body: (block …)`, i.e. one level — methods inside `if TYPE_CHECKING:` blocks
are missed. Acceptable for loom.

### 2.4 Call-site, parameter-type and base-class capture

`api/analyzers/python/analyzer.py:115-134`:

```python
    def add_symbols(self, entity: Entity) -> None:
        if entity.node.type == 'class_definition':
            superclasses = entity.node.child_by_field_name("superclasses")
            if superclasses:
                base_classes_captures = self._captures("(argument_list (_) @base_class)", superclasses)
                if 'base_class' in base_classes_captures:
                    for base_class in base_classes_captures['base_class']:
                        entity.add_symbol("base_class", base_class)
        elif entity.node.type == 'function_definition':
            captures = self._captures("(call) @reference.call", entity.node)
            if 'reference.call' in captures:
                for caller in captures['reference.call']:
                    entity.add_symbol("call", caller)
            captures = self._captures("(typed_parameter type: (_) @parameter)", entity.node)
            if 'parameter' in captures:
                for parameter in captures['parameter']:
                    entity.add_symbol("parameters", parameter)
            return_type = entity.node.child_by_field_name('return_type')
            if return_type:
                entity.add_symbol("return_type", return_type)
```

**The call-site query loom needs is exactly `"(call) @reference.call"` scoped to the enclosing
`function_definition` node** (not to the file root). Verified: on
`def top(a): \n def inner(): return helper(a)\n return inner()`, scoping to `top` yields
`['helper(a)', 'inner()']` and scoping to `inner` yields `['helper(a)']` — see ADAPT §3.2 for the
double-attribution this causes.

Base classes are captured by scoping `(argument_list (_) @base_class)` to the `superclasses:`
field — see ADAPT §3.1 for the `metaclass=` leak.

### 2.5 Call-target normalization (`self.foo()` / `a.b.c()` → the name node)

`api/analyzers/python/analyzer.py:249-259`:

```python
    def _extract_type_target(self, node: Node) -> Optional[Node]:
        if node.type == 'attribute':
            return node.child_by_field_name('attribute')
        return node

    def _extract_call_target(self, node: Node) -> Optional[Node]:
        if node.type == 'call':
            node = node.child_by_field_name('function')
            if node and node.type == 'attribute':
                node = node.child_by_field_name('attribute')
        return node
```

A captured `(call)` node is reduced to the callee name node: `helper(x)` → `helper`,
`self.other(x)` → `other`, `os.path.join(...)` → `join`.

### 2.6 The AST walk that produces DEFINES (File→Class, Class→Function, Function→Function)

`api/analyzers/source_analyzer.py:50-81`:

```python
    def create_entity_hierarchy(self, entity: Entity, file: File, analyzer, graph):
        types = analyzer.get_entity_types()
        stack = list(entity.node.children)
        while stack:
            node = stack.pop()
            if node.type in types:
                child = Entity(node)
                child.id = graph.add_entity(analyzer.get_entity_label(node),
                                            analyzer.get_entity_name(node),
                                            analyzer.get_entity_docstring(node),
                                            str(file.path), node.start_point.row,
                                            node.end_point.row, {})
                if not analyzer.is_dependency(str(file.path)):
                    analyzer.add_symbols(child)
                file.add_entity(child)
                entity.add_child(child)
                graph.connect_entities("DEFINES", entity.id, child.id)
                self.create_entity_hierarchy(child, file, analyzer, graph)
            else:
                stack.extend(node.children)

    def create_hierarchy(self, file: File, analyzer, graph):
        types = analyzer.get_entity_types()
        stack = [file.tree.root_node]
        while stack:
            node = stack.pop()
            if node.type in types:
                entity = Entity(node)
                entity.id = graph.add_entity(...)
                ...
                graph.connect_entities("DEFINES", file.id, entity.id)
                self.create_entity_hierarchy(entity, file, analyzer, graph)
            else:
                stack.extend(node.children)
```

This is the pattern loom's `indexer/walk.py` wants: a stack walk that descends through *any*
non-entity node (so a class inside `if:` inside `try:` is still found) and, on hitting an entity
node, emits a `DEFINES` edge from the current parent and recurses with that entity as the new
parent. It handles decorated definitions implicitly because the walk descends into
`decorated_definition` and finds the inner `function_definition`.

Two things loom must change: the walk uses `stack.pop()` (LIFO → children visited in reverse source
order; use `stack.pop(0)` or reverse-extend for deterministic source order), and the parent chain is
where loom's qualname comes from (ADAPT §3.4).

### 2.7 Static import resolution → IMPORTS edges (no LSP, pure tree-sitter)

`api/analyzers/python/analyzer.py:139-247`. This is the whole file→file import story and loom
should take it near-verbatim. Module naming (`:139-148`):

```python
    def _module_parts(self, file_path: Path, root: Path) -> Optional[list[str]]:
        try:
            rel = file_path.relative_to(root)
        except ValueError:
            return None
        parts = list(rel.with_suffix('').parts)
        if parts and parts[-1] == '__init__':
            parts = parts[:-1]
        return parts
```

Index build (`:150-176`) — two maps, `exact` keyed on the full dotted path from root and `suffix`
keyed on **every trailing sub-path** (first file wins), so `src/`-layout repos where the import name
`pkg.mod` differs from the path-from-root `src.pkg.mod` still resolve:

```python
        exact: dict[str, File] = {}
        suffix: dict[str, File] = {}
        for fpath, file in files.items():
            if fpath.suffix != '.py':      # a Python import must not resolve to pkg/mod.java
                continue
            if self.is_dependency(str(fpath)):
                continue
            parts = self._module_parts(fpath, root)
            if not parts:
                continue
            exact.setdefault('.'.join(parts), file)
            for i in range(len(parts)):
                suffix.setdefault('.'.join(parts[i:]), file)
        return {'exact': exact, 'suffix': suffix}
```

Lookup with symbol-drop fallback (`:178-186`):

```python
    def _resolve_dotted(self, dotted: str, index: dict) -> Optional[File]:
        f = index['exact'].get(dotted) or index['suffix'].get(dotted)
        if f is None and '.' in dotted:
            # imported name may be a symbol inside a module; drop the last part.
            parent = dotted.rsplit('.', 1)[0]
            f = index['exact'].get(parent) or index['suffix'].get(parent)
        return f
```

Import-statement extraction (`:188-222`) — emits `(dotted, level)` requests, `level` = number of
leading dots on a relative import:

```python
    def _import_requests(self, file: File) -> list[tuple[str, int]]:
        requests: list[tuple[str, int]] = []
        captures = self._captures(
            "(import_statement) @i (import_from_statement) @f", file.tree.root_node)
        for node in captures.get('i', []):
            for child in node.named_children:
                target = child
                if child.type == 'aliased_import':
                    target = child.child_by_field_name('name')
                if target is not None and target.type == 'dotted_name':
                    requests.append((target.text.decode('utf-8'), 0))
        for node in captures.get('f', []):
            module = node.child_by_field_name('module_name')
            level = 0
            base = ''
            if module is not None:
                if module.type == 'relative_import':
                    prefix = next((c for c in module.children if c.type == 'import_prefix'), None)
                    level = len(prefix.text.decode('utf-8')) if prefix is not None else 1
                    dotted_part = next((c for c in module.named_children if c.type == 'dotted_name'), None)
                    base = dotted_part.text.decode('utf-8') if dotted_part is not None else ''
                else:
                    base = module.text.decode('utf-8')
            requests.append((base, level))
            for name_node in node.children_by_field_name('name'):
                leaf = name_node
                if name_node.type == 'aliased_import':
                    leaf = name_node.child_by_field_name('name')
                if leaf is not None:
                    name_txt = leaf.text.decode('utf-8')
                    requests.append((f"{base}.{name_txt}" if base else name_txt, level))
        return requests
```

Relative-import arithmetic and dedupe (`:224-247`):

```python
    def resolve_imports(self, file: File, root: Path, index: object) -> list[File]:
        package_parts = self._module_parts(file.path, root)
        if package_parts is None:
            return []
        package_parts = package_parts[:-1] if package_parts else []   # importing file's package
        seen: set[Path] = set()
        targets: list[File] = []
        for dotted, level in self._import_requests(file):
            if level:
                base = package_parts[: len(package_parts) - (level - 1)] if level > 1 else list(package_parts)
                full = '.'.join([*base, dotted]) if dotted else '.'.join(base)
            else:
                full = dotted
            resolved = self._resolve_dotted(full, index)
            if resolved is None or resolved.path == file.path or resolved.path in seen:
                continue
            if self.is_dependency(str(resolved.path)):
                continue
            seen.add(resolved.path)
            targets.append(resolved)
        return targets
```

Driver, `api/analyzers/source_analyzer.py:318-338` — build the index once per language, then one
`IMPORTS` edge per resolved target, skipping unresolved (stdlib / third-party) imports silently:

```python
            for target in analyzer.resolve_imports(file, root, index):
                if getattr(file, "id", None) is None or getattr(target, "id", None) is None:
                    continue
                graph.connect_entities("IMPORTS", file.id, target.id)
```

### 2.8 Static call resolution → CALLS edges (`ts_resolver.py`, the LSP-free path)

`api/analyzers/python/ts_resolver.py` is a complete jedi/LSP replacement built only from the
already-parsed trees. loom must use this path, never the LSP one. Its scope is documented at
`ts_resolver.py:16-32`: resolves module-local names, `from X import Y [as Z]`, `import X` + `X.Y`,
`import X as Z` + `Z.Y`, and a guarded bare-name fallback; does not resolve dynamic dispatch,
star-imports, inferred types beyond `x = Foo()`, or out-of-project imports.

**Data model** (`:56-80`):

```python
@dataclass(frozen=True)
class _Definition:
    file_path: Path
    node: Node
    kind: str  # 'class' | 'func' | 'method' | 'var'

@dataclass
class _ModuleIndex:
    module: str
    file_path: Path
    top_level: dict[str, _Definition] = field(default_factory=dict)
    class_methods: dict[str, dict[str, _Definition]] = field(default_factory=dict)
    imports: dict[str, str] = field(default_factory=dict)   # local name -> dotted target
```

**Per-file indexing** (`:253-308`) runs the four definition queries with `matches()` and fills
`top_level`, `class_methods`, and a project-wide `by_name` fallback map. Top-level assignment
definitions are only recorded when the name is not already a func/class (`:288-289`).

**Import table** (`:310-351`) — note the `import pkg.lib` rule: bind only the **head** package to
itself so `pkg.lib.x` resolves by walking, and the aliased form binds alias → full dotted name:

```python
        for stmt in _captures(self._queries.imports, root).get("stmt", []):
            for child in stmt.named_children:
                if child.type == "dotted_name":
                    name = child.text.decode("utf-8")
                    head = name.split(".")[0]
                    mi.imports[head] = head
                elif child.type == "aliased_import":
                    dotted = child.child_by_field_name("name")
                    alias  = child.child_by_field_name("alias")
                    if dotted and alias:
                        mi.imports[alias.text.decode("utf-8")] = dotted.text.decode("utf-8")

        for stmt in _captures(self._queries.imports_from, root).get("stmt", []):
            module_node = stmt.child_by_field_name("module_name")
            if module_node is None:
                continue
            is_package = mi.file_path.name == "__init__.py"
            base_module = self._resolve_from_module(module_node, mi.module, is_package)
            if base_module is None:
                continue
            for child in stmt.named_children:
                if child == module_node:
                    continue
                if child.type == "dotted_name":
                    name = child.text.decode("utf-8")
                    mi.imports[name.split(".")[-1]] = f"{base_module}.{name}"
                elif child.type == "aliased_import":
                    dotted = child.child_by_field_name("name")
                    alias  = child.child_by_field_name("alias")
                    if dotted and alias:
                        mi.imports[alias.text.decode("utf-8")] = f"{base_module}.{dotted.text.decode('utf-8')}"
                # Wildcard: ignored
```

**Relative-import climbing** (`:353-386`), including the `__init__.py` off-by-one that loom must
keep — for a package `__init__.py` the module name *is* the package, so climb one level fewer:

```python
            up = dot_count - 1 if is_package else dot_count
            if up > len(base_parts):
                return None                      # climbs above project root
            base = base_parts[: len(base_parts) - up]
            if tail:
                base.append(tail)
            return ".".join(p for p in base if p) or None
```

**Resolution order** (`:416-458`) — local top-level, then local imports, then a *deliberately
conservative* bare-name fallback. Take this precision rule verbatim; it is the difference between a
usable CALLS graph and a false-edge factory:

```python
        # 1. Local module top-level
        if current_module and current_module in self._modules:
            mi = self._modules[current_module]
            if head in mi.top_level:
                return self._walk_tail(mi.top_level[head], tail)
            # 2. Local file's imports
            if head in mi.imports:
                imported = mi.imports[head]
                full_dotted = ".".join([imported, *tail]) if tail else imported
                target_def = self._lookup_dotted(full_dotted)
                if target_def is not None:
                    return [target_def]
                if imported in self._modules and tail:
                    mi2 = self._modules[imported]
                    if tail[0] in mi2.top_level:
                        return self._walk_tail(mi2.top_level[tail[0]], tail[1:])

        # 3. Cross-project bare-name fallback (last resort)
        candidates = [d for d in self._by_name.get(head, ()) if d.kind != "method"]
        if len(candidates) != 1:
            return []
        return self._walk_tail(candidates[0], tail) if tail else list(candidates)
```

The two rules in the fallback, from the comment at `:445-452`: **methods are excluded** (a
receiver-less `run()` cannot pick between every `Foo.run` in the repo) and **ambiguity is dropped**
(resolve only when exactly one module-level candidate matches). Precision over recall.

**Dotted-name reduction** (`:533-565`) — turns any reference expression into `['a','b','c']`:

```python
def _node_to_dotted_parts(node: Node) -> list[str]:
    if node.type == "identifier":
        return [node.text.decode("utf-8")]
    if node.type == "attribute":
        obj  = node.child_by_field_name("object")
        attr = node.child_by_field_name("attribute")
        if obj is None or attr is None:
            return []
        head_parts = _node_to_dotted_parts(obj)
        if not head_parts:
            return []
        return head_parts + [attr.text.decode("utf-8")]
    if node.type == "call":
        func = node.child_by_field_name("function")
        return _node_to_dotted_parts(func) if func else []
    if node.type in ("subscript", "generic_type"):
        # Optional[Node] / dict[Path, File] — resolve the outer name.
        if node.type == "subscript":
            inner = node.child_by_field_name("value")
        else:
            inner = node.named_children[0] if node.named_children else None
        return _node_to_dotted_parts(inner) if inner else []
    if node.type == "type":
        inner = node.named_children[0] if node.named_children else None
        return _node_to_dotted_parts(inner) if inner else []
    return []
```

**Decorator unwrapping** (`:519-530`) — always store the inner definition node, because that is what
`find_parent` and the entity map key on:

```python
def _strip_decorator(def_node: Node) -> Node:
    if def_node.type == "decorated_definition":
        for child in def_node.named_children:
            if child.type in ("class_definition", "function_definition"):
                return child
    return def_node
```

**Module naming** (`:142-158`) is duplicated from `_module_parts` but returns a dotted string and
handles out-of-root files by stringifying the path.

### 2.9 `matches()` vs `captures()` — a correctness trap, take the docstring with the code

`ts_resolver.py:166-184`:

```python
def _captures(query, root: Node) -> dict[str, list[Node]]:
    cursor = QueryCursor(query)
    return cursor.captures(root)

def _matches(query, root: Node) -> list[tuple[int, dict[str, list[Node]]]]:
    """Return per-match capture groups.

    Unlike :func:`_captures` (which groups *all* nodes by capture name into
    parallel lists that are **not** guaranteed to be index-aligned across
    different capture names), this yields one dict per match so that, e.g.,
    a ``@name`` capture is always paired with the ``@def`` capture from the
    *same* match. Zipping the two independent lists from ``captures()`` mis-
    aligns names and definitions whenever the per-capture node orderings
    diverge, scrambling the module symbol table.
    """
    cursor = QueryCursor(query)
    return cursor.matches(root)
```

**Rule for loom:** any query with two or more capture names that must be correlated
(`@name`+`@def`, `@class_name`+`@method_name`+`@method_def`) MUST be read via `matches()`. Only
single-capture queries (`(call) @reference.call`, `(import_statement) @stmt`) may use `captures()`.

### 2.10 Query memoization

`api/analyzers/analyzer.py:14-28`:

```python
        # Memoise compiled queries; tree-sitter query compilation is ~370us
        # each and adds up to seconds on large repos.
        self._query_cache: dict[str, Query] = {}

    def _get_query(self, pattern: str) -> Query:
        q = self._query_cache.get(pattern)
        if q is None:
            q = Query(self.language, pattern)
            self._query_cache[pattern] = q
        return q

    def _captures(self, pattern: str, node: Node) -> dict:
        cursor = QueryCursor(self._get_query(pattern))
        return cursor.captures(node)
```

Compile every query once at module import in `indexer/queries/python.py`; do not compile per file.
`QueryCursor` is cheap and must be fresh per execution.

### 2.11 `locator.py` primitive: edit range → enclosing symbol

Two pieces combine into exactly what loom's `hook/locator.py` needs (PLAN §4.3: "find the enclosing
function or class for the edited range").

`api/analyzers/analyzer.py:30-33`:

```python
    def find_parent(self, node: Node, parent_types: list) -> Node:
        while node and node.type not in parent_types:
            node = node.parent
        return node
```

and the descendant lookup inside `resolve`, `api/analyzers/analyzer.py:67`:

```python
    files[...].tree.root_node.descendant_for_point_range(
        Point(location['range']['start']['line'], location['range']['start']['character']),
        Point(location['range']['end']['line'], location['range']['end']['character']))
```

loom's locator is therefore:

```python
node = tree.root_node.descendant_for_point_range(Point(start_row, start_col), Point(end_row, end_col))
sym  = find_parent(node, ('function_definition', 'class_definition'))   # innermost wins
```

`descendant_for_point_range` returns the smallest node spanning the range; `find_parent` climbs to
the nearest entity. If it returns `None`, the edit is at module level → fall back to the file-level
node ID. Note `find_parent` returns the node itself when it is already an entity type, which is the
behaviour you want.

### 2.12 Drop-subtree-then-reindex, for the incremental path

`api/graph.py:523-539` — deleting a file deletes everything it `DEFINES`, transitively:

```python
        q = """UNWIND $files AS file
               MATCH (f:File {path: file['path'], name: file['name'], ext: file['ext']})
               OPTIONAL MATCH (f)-[:DEFINES*]->(e)
               DELETE f, e
        """
```

loom's SQL equivalent for `loom index --changed`: for each changed file, recursively collect
descendants over the `CONTAINS`/`DEFINES` edge, delete those `nodes` rows and all `edges` rows
touching them (`src` or `dst`), then re-run the walk on that one file. Claims on deleted node ids
must be handled explicitly (v1: orphan them and log an event; v1.5 body-hash rename transfer per
PLAN §4.1 attaches here).

### 2.13 Overall pipeline order

`api/analyzers/source_analyzer.py:340-343`:

```python
    def analyze_files(self, files: list[Path], path: Path, graph: Graph) -> None:
        self.first_pass(path, files, [], graph)
        self.link_imports(graph, path)
        self.second_pass(graph, files, path)
        graph.derive_overrides()
```

Two passes are mandatory and loom keeps the shape: **pass 1** parses every file and creates all
File/Class/Function nodes + DEFINES edges (`first_pass`, `:83-122`); **pass 2** resolves call
symbols to node ids and writes CALLS edges — it cannot run earlier because a call may target a
definition in a file not yet parsed. `link_imports` sits between them because it is purely
syntactic. loom drops `derive_overrides`.

---

## 3. ADAPT

### 3.1 Fix the base-class query: it captures `metaclass=Meta`

`(argument_list (_) @base_class)` uses a wildcard, so on
`class Foo(Base, Mixin, metaclass=Meta):` the scoped query returns
`['Base', 'Mixin', 'metaclass=Meta']` — verified. The third is a `keyword_argument`, not a base
class, and `_extract_type_target` will hand `Meta`'s enclosing node to resolution and mint a bogus
EXTENDS edge. loom's fix:

```python
_QUERY_BASE_CLASS = "(argument_list [(identifier) (attribute) (subscript)] @base_class)"
```

(`subscript` keeps `Generic[T]` / `Protocol[T]` bases.) Low priority for loom v1 since EXTENDS is
out of the MVP schema (REJECT §4.3), but the same wildcard idiom must not be copied into any other
query.

### 3.2 Fix nested-call double attribution — this one matters for conflict precision

`add_symbols` runs `(call) @reference.call` over the **whole** `function_definition` subtree, and
`create_entity_hierarchy` separately creates an entity for each nested `function_definition`.
Verified: `helper(a)` inside `inner` is attributed to both `inner` and `top`, producing two CALLS
edges for one call site.

Why loom cannot tolerate this: `declare_plan` expands write_targets by **one hop over CALLS**
(PLAN §4.2). Every spurious CALLS edge widens the claimed set and manufactures false conflicts —
the exact failure that makes an advisory system get ignored.

Fix in loom's walk: after computing calls for an entity, drop any call node whose nearest enclosing
`function_definition`/`class_definition` is not that entity:

```python
def own_calls(entity_node, all_call_nodes):
    for c in all_call_nodes:
        if find_parent(c.parent, ('function_definition', 'class_definition')) is entity_node:
            yield c
```

(Start from `c.parent` so a call that *is* the entity's own child is attributed once.)
Equivalently: collect calls once per file and bucket each by its nearest enclosing entity.

Related gap to close: `add_symbols` collects calls **only** for `function_definition`
(`analyzer.py:123`). Calls in a class body but outside any method (decorator invocations, field
initialisers like `x = factory()`) are dropped entirely. loom should bucket calls at file level so
class-body and module-level calls land on the Class / File node respectively.

### 3.3 Replace `Language.query()` with `Query(...)` + `QueryCursor`

`ts_resolver.py:125-134` builds its queries with the removed API:

```python
class _Queries:
    def __init__(self, language: Language) -> None:
        self.top_level_func = language.query(_QUERY_TOP_LEVEL_FUNC)
        ...
```

Empirically: `tree-sitter==0.25.2` still has `Language.query` but emits
`DeprecationWarning: query() is deprecated. Use the Query() constructor instead.`;
`tree-sitter==0.26.0` **removed it** (`hasattr(Language, 'query') is False`), so `_Queries.__init__`
raises `AttributeError` on 0.26 — inside the clone's own pin of `>=0.25.2,<0.27`. loom writes:

```python
from tree_sitter import Language, Query, QueryCursor
class _Queries:
    def __init__(self, language: Language) -> None:
        self.top_level_func = Query(language, _QUERY_TOP_LEVEL_FUNC)
        ...
```

`QueryCursor(q).captures(node)` → `dict[str, list[Node]]`;
`QueryCursor(q).matches(node)` → `list[tuple[int, dict[str, list[Node]]]]`. Both verified on 0.26.0.
Pin loom to `tree-sitter>=0.25.2` and use the constructor form, which works on both.

### 3.4 Node identity: code-graph has no qualname, loom must build one

`api/graph.py:357-381` keys entities on `(name, path, src_start, src_end)`:

```python
        q = f"""MERGE (c:{label}:Searchable {{name: $name, path: $path, src_start: $src_start,
                               src_end: $src_end}})"""
```

and files on `(path, name, ext)` (`graph.py:515-517`). There is **no qualname and no body_hash** —
node identity is positional, so moving a function down ten lines mints a new node. That is fatal for
claims: a claim on `auth.py::login` must survive an unrelated edit above it.

loom instead threads a qualname prefix through the walk (§2.6), loom canonical convention
`relative/path.py::Class/method` (GATE-1 fix 2: `/` is Serena's real within-file separator,
`NAME_PATH_SEP` — the plan's dotted `Class.method` form does not exist in Serena; `::` is loom's own
path↔symbol joiner):

```python
NAME_SEP = "/"          # Serena's NAME_PATH_SEP; loom's within-file separator

def walk(node, file_rel, prefix, parent_id):
    for child in entity_children(node):
        name = child.child_by_field_name('name').text.decode()
        qual = f"{prefix}{NAME_SEP}{name}" if prefix else name
        nid  = short_hash(repo, f"{file_rel}::{qual}")
        emit_node(nid, path=file_rel, qualname=qual, kind=label(child),
                  body_hash=sha256(child.text), start=child.start_point.row,
                  end=child.end_point.row)
        emit_edge(parent_id, nid, "CONTAINS")
        walk(child, file_rel, qual, nid)
```

`src_start`/`src_end` become **non-identifying columns** (needed by the locator, not by the ID).
A method in a class gets `Class/method` (Serena-compatible, NOT Python's dotted `__qualname__`).
Note BUILD-SPEC's granularity rule: functions nested inside functions are not claimable nodes —
they roll up to their enclosing function — so `outer/inner` qualnames are never minted for closures.

Collision note: two same-named methods in one file (e.g. an overload guarded by `if TYPE_CHECKING`)
now collide on qualname where code-graph separated them by line span. Detect at insert and suffix
`#2`; log an event.

### 3.5 IMPORTS is File→File; keep it that way

code-graph emits `IMPORTS` between two `File` nodes (`source_analyzer.py:338`), while `CALLS` and
`DEFINES` are between entities. loom's `edges(src, dst, kind)` table is uniform, so file-level nodes
must exist as real rows in `nodes` (`kind='File'`, `qualname` = the relative path, no dotted
suffix). This is also what PLAN §1 wants for the "file granularity as fallback for non-code files"
case — the same row type serves both.

Consequence for `declare_plan`'s one-hop expansion: a File→File IMPORTS hop is *much* coarser than a
Function→Function CALLS hop; expanding over IMPORTS from a file node can pull in a whole package.
PLAN §7 already calls this out ("CALLS one hop, IMPORTS zero"). Recommendation: make **IMPORTS
radius 0 from day one**, not a v2 tuning knob — with File-granular imports, one hop is not a
precision trade-off, it is noise.

### 3.6 Ordering determinism

`create_hierarchy` uses `stack.pop()` (LIFO), so sibling entities are visited in reverse source
order. code-graph compensates by writing edges serially in original file order
(`source_analyzer.py:295-316`). loom should just make the walk source-ordered
(`stack.pop(0)`, or recurse over `node.children` directly) so re-index diffs are stable and the
events log is readable.

### 3.7 Resolver cache keying

`ts_resolver.py:218-251` caches the symbol table on `id(files)` and rebuilds when the dict identity
changes, guarded by a `threading.Lock` with double-check and last-write of the cache key. loom
indexes single-threaded per repo and persists to SQLite, so drop the lock and the `id()` trick; key
the rebuild on the repo path + index generation number instead. Keep the *shape* — build into fresh
local dicts, publish atomically — if loom ever indexes concurrently.

---

## 4. REJECT

### 4.1 The entire LSP / jedi resolution path — PLAN's implicit default, and wrong

`api/analyzers/analyzer.py:64-75` (`resolve` via `lsp.request_definition`),
`api/analyzers/source_analyzer.py:124-316` (`second_pass`: multilspy `SyncLanguageServer` startup
for java/python/csharp, `MultilspyConfig`, venv discovery, `sys.prefix` fallback), and the
`falkordb-multilspy` dependency. Reasons: it needs a language server per language, it is the
dominant index cost (the docstring at `:129-131` says symbol resolution dominates wall time), and
`ts_resolver` already replaces it. loom takes the tree-sitter resolver only.

### 4.2 `add_dependencies` — creates a venv and pip/poetry-installs the target repo

`api/analyzers/python/analyzer.py:73-99` runs `python3 -m venv venv`, `pip install poetry`,
`poetry install` / `pip install -r requirements.txt` **inside the analyzed repo**, then indexes
`site-packages`. Their own comment (`:74-77`) calls it "10s–10min of zero-value pip work". Absolutely
not: loom indexes only in-repo files. Keep the concept of `is_dependency` (`:136-137`, `"venv" in
file_path`) as a path-exclusion filter, generalised to `.venv`, `node_modules`, `site-packages`,
`.git`, `build`, `dist`.

### 4.3 Edge kinds outside loom's schema

`source_analyzer.py:305-316` also writes `EXTENDS`, `IMPLEMENTS`, `RETURNS`, `PARAMETERS`, and
`graph.derive_overrides` (`graph.py:602-634`) derives `OVERRIDES` via a Cypher variable-length
match. PLAN §4.1 fixes loom's edge kinds to CALLS / IMPORTS / CONTAINS. Do not build the
`type_resolution_keys` machinery (`tree_sitter_base.py:45-59, 69-107`) for parameters/return types —
it exists only to feed those edge kinds. Keep the *hook* (`resolve_symbol` dispatching on a key
string) if EXTENDS is ever wanted for v2 impact analysis; that is one `elif`.

### 4.4 The storage layer

Everything in `api/graph.py` is Cypher against FalkorDB (`MERGE (c:{label}:Searchable ...)`,
`MATCH (src),(dest) WHERE ID(src)=$src_id`, `ID()`-based integer node ids). loom is SQLite with
content-hash string ids (PLAN §1, §4.1). The only Cypher worth translating is `delete_files`
(§2.12). Also reject: `graph.py`'s reliance on server-assigned `ID()` values as the entity id
threaded through the whole analyzer (`entity.id = graph.add_entity(...)`) — loom computes ids
locally before insert, which is what makes the walk storage-independent and testable.

### 4.5 Everything else in the repo

`api/git_utils/` (git history graph, per-commit graphs), `api/code_coverage/` (lcov),
`api/llm.py` / `api/prompts.py` / `graphrag-sdk` (natural-language querying),
`api/migrations/per_branch.py`, `api/mcp/` (their own MCP server; loom has its own design in
PLAN §4.2 and specgate as the proven reference), `app/` (Next.js UI), `Dockerfile` /
`docker-compose.yml` (FalkorDB container). None of it is on loom's path.

### 4.6 There is no incremental re-index to lift

`analyze_sources` (`source_analyzer.py:346-359`) always `rglob`s the whole tree and re-indexes
everything. `analyze_files` (`:340-343`) takes an explicit file list but still rebuilds the resolver
symbol table over `self.files` and does a full `second_pass`. **No mtime or hash-based change
detection exists anywhere in the repo.** PLAN §5 M1 lists "incremental (changed files only, by mtime
plus hash)" as FalkorDB-derived — it is not; that is loom's own work, and §2.12 is the only reusable
piece.

Related trap when loom does build it: the resolver's symbol table is **project-wide**. Re-indexing
one changed file correctly requires either (a) rebuilding the table from persisted node rows rather
than from live `File` objects, or (b) accepting that CALLS edges *into* the changed file from
unchanged files may go stale. Recommended v1: on incremental re-index of file F, delete and rebuild
F's nodes/DEFINES, recompute outgoing CALLS from F, and additionally recompute incoming CALLS by
re-resolving only the files that have an IMPORTS edge to F. That bounds the work without the
project-wide rebuild.

### 4.7 File map — what to open, what to skip, if you do open the clone

| Path | Relevance |
|---|---|
| `api/analyzers/python/ts_resolver.py` | **Primary lift.** Queries + static resolution, 566 lines. |
| `api/analyzers/python/analyzer.py` | Entity types, name/docstring, `add_symbols` captures, import→file resolution. |
| `api/analyzers/tree_sitter_base.py` | Symbol-key dispatch, `resolve_type` / `resolve_method`. |
| `api/analyzers/analyzer.py` | Query memoisation, `find_parent`, `descendant_for_point_range` usage. |
| `api/analyzers/source_analyzer.py` | Two-pass pipeline, `create_hierarchy` walk, `link_imports`. |
| `api/entities/entity.py`, `api/entities/file.py` | Tiny entity/file containers; loom replaces with rows. |
| `api/graph.py` | FalkorDB Cypher. Only `delete_files` (`:523-539`) is worth translating. |
| `tests/analyzers/test_ts_python_resolver.py` | Fixture patterns for loom's own resolver tests. |
| `tests/test_py_analyzer.py` | Edge-snapshot `Counter` comparison idiom for re-index stability tests. |
| `app/`, `api/git_utils/`, `api/llm.py`, `api/mcp/`, `api/code_coverage/`, `api/migrations/` | Skip entirely — §4.5. |

---

## 5. CORRECTIONS to PLAN-v1.md

**C1 — §2 "FalkorDB code-graph (github.com/FalkorDB/code-graph and its analyzers)".**
There is no separate analyzers repository or submodule. The analyzers are `api/analyzers/` inside
the same repo, one package per language (`python/`, `java/`, `javascript/`, `kotlin/`, `csharp/`,
`c/` — the C one is fully commented out and `.c`/`.h` are disabled at
`source_analyzer.py:27-35`). Cite `api/analyzers/python/` in the manifest.

**C2 — §2 "tree-sitter capture queries for function and class definitions and call sites".**
The framing is half wrong in two ways.
(a) The **default** Python path is not query-driven at all: definitions are found by a node-type
stack walk (`create_hierarchy`, no query), and call *resolution* goes through jedi/LSP. Only the
call/parameter/base-class *capture* is query-driven (`add_symbols`, `analyzer.py:115-134`).
(b) The rich definition queries the plan is after live in `ts_resolver.py:88-122`, behind an
**opt-in env var** `CODE_GRAPH_PY_RESOLVER=tree_sitter` (`analyzer.py:22-23, 41-48`), and as shipped
they are **broken on `tree-sitter` 0.26** — `_Queries.__init__` calls `Language.query()`, removed in
0.26.0 (verified: present-but-deprecated on 0.25.2, `hasattr` False on 0.26.0), while the project
pins `tree-sitter>=0.25.2,<0.27`. So "take their capture queries" means: take the query *strings*
and the resolver's logic, and rewrite the query construction (ADAPT §3.3). The rest of the codebase
already uses the correct `Query(...)` + `QueryCursor` form (`analyzer.py:18-28`).

**C3 — §2 vs §4.1: the containment edge has two names.**
§2 says the schema is "File, Class, Function, DEFINES, CALLS, IMPORTS"; §4.1 says
`edges(src, dst, kind)` with `kind in CALLS, IMPORTS, CONTAINS`. Same relation, two names. Pick
one before M1 — recommend **`CONTAINS`** (§4.1 is the normative data model and `DEFINES` reads as
"definition site" in the LSP sense). Wherever this doc quotes code-graph's `DEFINES`, loom writes
`CONTAINS`.

**C4 — §4.1 `nodes(id, repo, path, qualname, kind, body_hash, updated)` is not derivable from
code-graph as-is.** code-graph nodes carry no qualname and no body_hash; identity is
`(name, path, src_start, src_end)` (`graph.py:364-365`). The qualname must be synthesised during the
walk (ADAPT §3.4), and `body_hash` is loom-only. Do not expect to lift a node-writer.

**C5 — §5 M1 "incremental (changed files only, by mtime plus hash), FalkorDB-derived queries".**
No incremental path exists upstream (REJECT §4.6). M1's incremental sub-task should be costed as
original work, and its acceptance criterion ("re-index of one changed file touches only its nodes")
needs the cross-file CALLS caveat in REJECT §4.6 written into the spec, or the criterion is
satisfiable while leaving stale inbound edges.

**C6 — §7 "per-edge-type radius (CALLS one hop, IMPORTS zero)" should not wait for scale.**
Because IMPORTS is File→File (ADAPT §3.5), one hop over IMPORTS at two users already pulls in every
symbol of every imported module. Set IMPORTS radius 0 in M2's `declare_plan`, not as a v2 config
knob.

**C7 — §4.3 locator is directly buildable.** PLAN says "Parse the target file with tree-sitter, find
the enclosing function or class for the edited range" without naming a mechanism. The mechanism is
`root_node.descendant_for_point_range(Point(r0,c0), Point(r1,c1))` +
`find_parent(node, ('function_definition','class_definition'))` (ADOPT §2.11). No new research
needed; this is ~15 lines.

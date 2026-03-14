# Skills System – Implementation Plan (skills_impl.md)

This document describes **how to implement** the Skills system defined in `skills_reqs.md` in the current Aye Chat codebase.

It is written as an engineering plan: components, APIs, data flows, edge cases, and where the code should hook into the existing prompt-building pipeline.

---

## 0. Goals (traceable to requirements)

Implement a repo-local skills feature where:

- Skills are `.md` files under the **first** `skills/` directory found by walking upward from the current working directory. (Req §2.1)
- The scan is **non-recursive** and only includes direct children `.md` files. (Req §2.2)
- Skill IDs are normalized `stem.strip().lower()` and matched case-insensitively. (Req §2.3)
- Skills can be applied via:
  - **Explicit invocation** (deterministic): `skill:foo`, `skills:foo,bar`, `skill foo`, `foo skill`, etc. (Req §3)
  - **Fuzzy (implicit) matching** only when the user mentions `skill`/`skills` but does not use explicit `skill:`/`skills:` syntax. (Req §4)
- Applied skill contents are appended to the **system prompt** with delimiters. (Req §6)
- Verbose-only observability: print applied skills and resolved skills directory, and debug-only logging for unknown explicit skills. (Req §8)

Non-goals remain out of scope (metadata, aliases, LLM inference, etc.). (Req §9)

---

## 1. Where to integrate in current code

### 1.1 Current prompt pipeline
The prompt sent to the LLM is built in:

- `controller/llm_invoker.py`:
  - `_build_system_prompt(conf, verbose)` returns the base system prompt (ground truth / default) + optional `AGENTS.md`.
  - `invoke_llm()` builds `system_prompt` once per request, then calls the model/API.

### 1.2 Skills insertion point
To meet Req §1 and §6 (skill contents appended to system prompt), the skills logic should run during system prompt construction.

Recommended approach:

- Change `_build_system_prompt(...)` to accept the **user prompt text** (the message), or introduce a new function:

  - `_build_system_prompt_with_skills(prompt: str, conf: Any, verbose: bool) -> str`

This keeps skills behavior as part of prompt construction and avoids altering the user prompt text.

**Important requirement:** invocation syntax MUST NOT be removed from the user prompt. (Req §3)

So: parsing happens on the side, but the original `prompt` string is still sent as the user message.

### 1.3 All LLM entry points confirmed

All three LLM invocation paths in the codebase route through `invoke_llm()` in `controller/llm_invoker.py`:

1. **Default prompt path** — `repl.py` calls `invoke_llm()`
2. **`with` command** — `command_handlers.py` `handle_with_command()` calls `invoke_llm()`
3. **`blog` command** — `command_handlers.py` `handle_blog_command()` calls `invoke_llm()`

Since `invoke_llm()` calls `_build_system_prompt()` internally, wiring skills into `_build_system_prompt()` (or its replacement `_build_system_prompt_with_skills()`) automatically covers all three paths.

**No changes to `command_handlers.py` or `repl.py` are required for skills support.**

### 1.4 Offline/local model path confirmed

`invoke_llm()` passes the built `system_prompt` to the offline plugin via:

```python
local_response = plugin_manager.handle_command("local_model_invoke", {
    ...
    "system_prompt": system_prompt,
    ...
})
```

The offline plugin (`plugins/offline_llm.py`) uses this directly:

```python
effective_system_prompt = system_prompt if system_prompt else SYSTEM_PROMPT
```

Therefore the skills-augmented system prompt flows through to offline models without any plugin changes.

---

## 2. New module design

Create a new module:

- `model/skills_system.py`

This module should be independent of UI and only depend on:

- `pathlib.Path`, `re`
- `aye.model.ignore_patterns.load_ignore_patterns`
- `rapidfuzz` for similarity scoring

### 2.1 Core data model

```python
@dataclass(frozen=True)
class Skill:
    skill_id: str          # normalized
    file_path: Path
    contents: str

@dataclass
class SkillsIndex:
    skills_dir: Path
    skills: dict[str, Skill]  # keyed by normalized skill_id
    dir_mtime: float          # skills_dir stat mtime at scan time
```

### 2.2 Resolver API

Expose a small API used by `llm_invoker`:

```python
class SkillsResolver:
    def __init__(self):
        self._cache: dict[Path, SkillsIndex] = {}

    def get_index(self, start_dir: Path) -> SkillsIndex | None:
        ...

    def resolve_applied_skills(self, prompt: str, index: SkillsIndex) -> list[str]:
        """Return ordered list of normalized skill_ids to apply (deduped)."""

    def render_skills_for_system_prompt(self, skill_ids: list[str], index: SkillsIndex) -> str:
        """Return concatenated skill blocks with delimiters."""
```

Caching is required (Req §5). The cache key should be the resolved `skills_dir` path.

---

## 3. Locating the `skills/` directory (Req §2.1)

### 3.1 Search algorithm

Input: `start_dir = Path.cwd().resolve()`

Algorithm:

1. For `d` in `[start_dir, d.parent, ..., filesystem root]`:
   - `candidate = d / "skills"`
   - If it exists and is a directory:
     - Verify it is **not ignored** by `.gitignore` / `.ayeignore` semantics.
     - If not ignored: return this directory as the active skills directory.
2. If none found: return `None`.

### 3.2 Respecting ignore patterns

The existing shared utility is confirmed at `aye.model.ignore_patterns`:

```python
from aye.model.ignore_patterns import load_ignore_patterns
# Returns: pathspec.PathSpec with .match_file() method
```

The function `load_ignore_patterns(root: Path)` walks upward from `root` collecting patterns from all `.gitignore` and `.ayeignore` files, plus `DEFAULT_IGNORE_SET`.

Implementation:

- For each `d` being tested:
  - `ignore_spec = load_ignore_patterns(d)`
  - Treat the directory path to match as `"skills/"` (trailing slash consistent with how other code checks directories).
  - If `ignore_spec.match_file("skills/")` is true, skip this candidate.

This matches the requirement to respect ignore patterns and skip ignored `skills/` directories. It follows the same approach used by `source_collector.py` and `write_validator.py`.

---

## 4. Scanning and loading skills (Req §2.2, §5)

### 4.1 Non-recursive scan

When a `skills_dir` is resolved:

- List entries: `skills_dir.iterdir()`
- Include only:
  - Direct children
  - Files with suffix `.md`
  - Exclude directories

Ignore subdirectories completely (e.g. `skills/foo/bar.md`).

### 4.2 Skill ID normalization

For each `.md` file:

- `stem = path.stem`
- `skill_id = stem.strip().lower()`

Ignore if:

- `skill_id == ""` after normalization

Store the skill even if the stem has "weird" characters on disk; matching and parsing uses normalized token forms.

### 4.3 File read

Read with:

- `read_text(encoding="utf-8")`

If a file cannot be read:

- Skip it (log in debug mode).

### 4.4 Cache strategy

Cache the `SkillsIndex` in memory keyed by `skills_dir`.

**Staleness check:** On each `get_index()` call, compare `skills_dir.stat().st_mtime` against the cached `dir_mtime`. If the directory mtime has changed (files added/removed/renamed), invalidate and re-scan. This is cheap (single `stat()` call) and allows mid-session skill additions without full FS watching.

```python
def get_index(self, start_dir: Path) -> SkillsIndex | None:
    skills_dir = self._find_skills_dir(start_dir)
    if skills_dir is None:
        return None

    cached = self._cache.get(skills_dir)
    if cached is not None:
        try:
            current_mtime = skills_dir.stat().st_mtime
            if current_mtime == cached.dir_mtime:
                return cached
        except OSError:
            pass  # directory disappeared; re-scan

    index = self._scan_skills(skills_dir)
    self._cache[skills_dir] = index
    return index
```

---

## 5. Parsing applied skills from user prompts

Two modes (Req §3 vs Req §4):

1. **Explicit invocation**: if explicit syntax is detected, apply *only* explicitly referenced skills; do not run fuzzy.
2. **Fuzzy inference**: only if prompt mentions `skill`/`skills` and does *not* contain explicit `skill:` or `skills:` syntax.

### 5.1 Token validity

Use the recommended single-token pattern:

- `SKILL_TOKEN_RE = r"[A-Za-z0-9_-]+"`

Normalization of extracted tokens:

- `token.strip().lower()`

Deduping:

- Maintain order; keep first occurrence only (Req §3.2, §7).

### 5.2 Explicit invocation detection (deterministic)

The explicit parsing should support both single and multiple forms.

**Detection order:** Check keyed forms first (§5.2.1), then bare word-order forms (§5.2.2).

#### 5.2.1 Explicit "keyed" forms

Detect `skill:` / `skills:` and `skill=` / `skills=` variants (tolerate whitespace):

- `skill\s*[:=]\s*(<token>)`
- `skills\s*[:=]\s*(<list>)`

Where `<list>` can be comma-separated and/or space-separated.

Example parsing rule:

- After `skills:` capture until end of line / end of string; then split by commas and whitespace.

#### 5.2.2 Explicit "word order" forms

Support:

- `skill <token>`
- `<token> skill`
- `skills <token1> <token2> ...`

**Verb exclusion list for disambiguation:**

To prevent overlap with fuzzy patterns, bare `<token> skill` and `skill <token>` forms must **not** match when the token immediately preceding `skill` is a known fuzzy-triggering verb. The following verbs trigger fuzzy mode instead of explicit mode:

```python
FUZZY_TRIGGER_VERBS = {"using", "apply", "use", "with", "enable", "activate", "try"}
```

Examples:

| Input | Mode | Reason |
|---|---|---|
| `modularization skill` | Explicit | No fuzzy-trigger verb before `skill` |
| `skill modularization` | Explicit | Direct `skill <token>` form |
| `using modularization skill` | Fuzzy | `using` is a fuzzy-trigger verb |
| `apply testing skill` | Fuzzy | `apply` is a fuzzy-trigger verb |
| `skill:modularization` | Explicit | Keyed form (always explicit) |

Implementation:

1. First, check for keyed forms (`skill:`, `skills:`, `skill=`, `skills=`). If found → explicit mode, return tokens.
2. Then, check for bare `skill <token>` or `<token> skill` patterns.
   - For `<word> skill`: if `<word>` is in `FUZZY_TRIGGER_VERBS`, skip (let fuzzy handle it).
   - Otherwise → explicit mode.
3. If no explicit pattern matched, fall through to fuzzy detection.

#### 5.2.3 Explicit parsing precedence

If any explicit pattern matches, **explicit mode wins**:

- Return only explicit tokens that exist in the scanned skill set (unknown ignored).
- Do **not** attempt fuzzy matching.

#### 5.2.4 Unknown explicit skill handling

- Unknown skills are ignored (Req §3.3).
- If debug mode is on: log which were ignored.
- Debug flag access pattern (consistent with codebase):

```python
from aye.model.auth import get_user_config

def _is_debug() -> bool:
    return get_user_config("debug", "off").lower() == "on"
```

### 5.3 Fuzzy matching (implicit mode)

Run fuzzy matching only when:

- Prompt contains the word boundary `\bskill\b` or `\bskills\b`
- And the prompt does **not** contain `skill:` or `skills:` (case-insensitive) (Req §4)

Candidate extraction (Req §4.1):

- Look for phrases like:
  - `using <X> skill`
  - `apply <X> skill`
  - `<X> skill`

Where `<X>` must match the single-token pattern.

Punctuation tolerance:

- Strip common punctuation around `<X>` token boundaries: `, . ; : ! ? ) ( ] [ { } " '`

#### 5.3.1 Scoring

Use the recommended scoring sequence (Req §4.2):

1. Exact match of normalized token against `skills.keys()`
2. Otherwise compute similarity against all known skill IDs.

Similarity implementation:

- Use `rapidfuzz.fuzz.ratio` normalized to `[0, 1]`.

Threshold:

- `>= 0.85` applies.

Ambiguity rule:

- Sort matches by score.
- If `(best - second_best) < 0.03`, treat as ambiguous and apply none.

Safety constraints (Req §4.3):

- Never auto-apply multiple skills from fuzzy matching.
- If no confident match: do nothing.

---

## 6. Applying skills to the system prompt (Req §6)

### 6.1 Render format

For each applied skill ID, append:

```
--- Applied Skill: <skill_id> ---

<contents>

--- End Skill ---
```

Order:

- Explicit: preserve user-specified order.
- Fuzzy: apply only the resolved one.

Deduping:

- Deduplicate before rendering (Req §3.2, §7).

### 6.2 Placement relative to AGENTS.md

Current `_build_system_prompt` appends AGENTS.md context to the base prompt.

Recommended ordering:

1. Base prompt (ground_truth or default `SYSTEM_PROMPT`)
2. AGENTS.md block (repo instructions)
3. Skills blocks

Rationale:

- AGENTS.md is "system context / repo instructions"; skills are user-selected behavior modifiers. Appending skills last makes it clear they are additional behavior constraints applied for the current request.

---

## 7. Observability (Req §8)

All output is verbose-only.

Access configuration flags using the established codebase pattern:

```python
from aye.model.auth import get_user_config

def _is_verbose():
    return get_user_config("verbose", "off").lower() == "on"

def _is_debug():
    return get_user_config("debug", "off").lower() == "on"
```

### 7.1 Verbose
When skills are applied:

- Print:
  - `Applied skills: modularization, testing`
  - `Skills directory: /path/to/repo/skills`

### 7.2 Debug-only
If explicit invocation references unknown skills:

- Print which were ignored:
  - `Ignored unknown skills: foo, bar`

Implementation hook:

- This logging should live in the resolver (or in llm_invoker) but must be guarded by `_is_verbose()` / `_is_debug()` helper calls.

---

## 8. Concrete integration steps (incremental)

### Step 1 — Add module
Add `model/skills_system.py` with:

- `find_skills_dir(start_dir: Path) -> Path | None`
- `scan_skills(skills_dir: Path) -> SkillsIndex`
- `parse_explicit(prompt: str) -> list[str] + diagnostics`
- `parse_fuzzy(prompt: str, known_ids: list[str]) -> list[str]`
- `resolve_applied_skills(prompt: str, index: SkillsIndex) -> (skill_ids, diagnostics)`
- `render_system_blocks(skill_ids, index) -> str`

### Step 2 — Wire into system prompt build
In `controller/llm_invoker.py`:

- Replace:
  - `system_prompt = _build_system_prompt(conf, verbose)`
- With:
  - `system_prompt = _build_system_prompt_with_skills(prompt, conf, verbose)`

Where `_build_system_prompt_with_skills`:

1. Calls existing `_build_system_prompt(conf, verbose)` to get base+AGENTS.
2. Builds/uses a `SkillsResolver` instance (can be module-global singleton in `llm_invoker.py` to keep cache across calls).
3. Finds index based on `Path.cwd()`.
4. Resolves applied skills based on `prompt`.
5. If any skills apply, appends rendered blocks.
6. Prints verbose/debug observability when applicable.

**All LLM entry points are covered:** `handle_with_command`, `handle_blog_command`, and the default prompt path all route through `invoke_llm()`, which calls the system prompt builder. No changes needed in `command_handlers.py` or `repl.py`.

**Offline models are covered:** `invoke_llm()` passes the built `system_prompt` to `plugin_manager.handle_command("local_model_invoke", {..., "system_prompt": system_prompt})`. The offline plugin uses `system_prompt` directly when provided.

### Step 3 — Ensure "do not remove invocation syntax"
No prompt rewriting is needed.

- Do **not** mutate `prompt`.
- Only mutate `system_prompt`.

---

## 9. Edge cases and decisions

- **Multiple `skills/` directories**: choose the first found walking upward (Req §2.1).
- **Skills directory ignored**: skip it and continue walking (Req §2.1).
- **Invalid file stems**:
  - empty after normalization: ignore
  - weird characters: keep on disk but matching/parsing operates on normalized token form (Req §2.3.1)
- **Explicit vs fuzzy overlap**:
  - If explicit patterns are detected anywhere, do not run fuzzy. (Req §3.3, §4)
  - Fuzzy-trigger verbs (`using`, `apply`, `use`, `with`, `enable`, `activate`, `try`) prevent bare `<token> skill` from being treated as explicit.
- **Mid-session skill additions**:
  - Handled via `mtime` check on `skills_dir` (see §4.4). When mtime changes, cache is invalidated and skills are re-scanned.
- **Performance**:
  - scan+load once per resolved skills_dir, then reuse cached in memory (Req §5).
  - Cache invalidation is a single `stat()` call per LLM invocation — negligible cost.

---

## 10. Files to add or modify

### New files

| File | Purpose |
|---|---|
| `model/skills_system.py` | Core skills module: discovery, scanning, parsing, rendering |

### Modified files

| File | Change Description |
|---|---|
| `controller/llm_invoker.py` | **Primary integration point.** Add `_build_system_prompt_with_skills()`, instantiate a module-level `SkillsResolver` singleton, wire skills into system prompt construction inside `invoke_llm()`. |

### No changes required

| File | Reason |
|---|---|
| `controller/command_handlers.py` | All LLM paths route through `invoke_llm()` |
| `controller/repl.py` | All LLM paths route through `invoke_llm()` |
| `plugins/offline_llm.py` | Uses `system_prompt` param from `invoke_llm()` |
| `model/ignore_patterns.py` | Existing API is sufficient (`load_ignore_patterns(root) → PathSpec`) |

---

## 11. Acceptance checklist (maps to Success Criteria)

- [ ] Adding `skills/foo.md` makes `foo` discoverable.
- [ ] `skill:foo` applies it.
- [ ] `skills:foo,bar` applies both (deduped, ordered).
- [ ] Natural phrasing can fuzzy match when appropriate.
- [ ] No skill is applied unexpectedly (ambiguity/threshold prevents it).
- [ ] Verbose output shows applied skills and directory.
- [ ] Skills added mid-session are picked up on next prompt.
- [ ] All three LLM paths (default, with, blog) include skills in system prompt.

---

End of implementation plan.

# Skills System – Requirements

## 1. Overview

The Skills system enables users to augment Aye Chat behavior by referencing skill documents stored in a repository-level `skills/` directory.

Each skill is defined as a plain text `.md` file.

- The file name (without extension) is the skill identifier.
- The file contents are appended to the system prompt when the skill is applied.
- No metadata, headers, aliases, or special formatting are required inside the file.

Example:

skills/
  modularization.md
  docs.md
  testing.md

Skill IDs in this case are:

- modularization
- docs
- testing


---

## 2. Directory Structure

### 2.1 Locating the `skills/` directory

- A `skills/` directory must be supported.
- Search starting from the current working directory and then walking upward through parent directories up to the filesystem root.
- The **first** `skills/` directory found becomes the active skills directory.
- The directory search and scanning must **respect `.gitignore` and `.ayeignore`** patterns (same semantics as other project file discovery). If a candidate `skills/` directory is ignored, it must be skipped.

### 2.2 Scanning behavior

- Scanning is **non-recursive**.
- Only `.md` files that are **direct children** of the chosen `skills/` directory are treated as skills.
- Files in subdirectories (e.g. `skills/foo/bar.md`) are ignored.

### 2.3 Skill ID definition and normalization

- Skill ID source: `Path(file).stem` (file name without `.md`).
- Skill ID normalization:
  - `skill_id = stem.strip().lower()`
- Matching must be **case-insensitive** (after normalization).

#### 2.3.1 Skill ID validity

To ensure deterministic parsing and matching, skill IDs are treated as **single tokens**.

- Recommended allowed character set for parsing: `[a-z0-9_-]+`
- Skill file stems that normalize to an empty string must be ignored.
- If a file stem contains characters outside the allowed set (e.g. spaces), it may still exist on disk, but:
  - explicit invocation is expected to reference the normalized token form
  - fuzzy matching should only consider the normalized token form

Example:

skills/Modularization.md → skill ID = modularization


---

## 3. Explicit Skill Invocation (Deterministic)

The system must support explicit invocation syntax in user prompts.

**Important:** Invocation syntax is treated as user-visible text and **MUST NOT be removed** from the prompt. (It may be parsed for control behavior, but the original prompt is still sent to the LLM.)

### 3.1 Single Skill

Supported forms:

skill:modularization  
skill = modularization  
skill modularization 
modularization skill  

Whitespace around separators should be tolerated.

### 3.2 Multiple Skills

Supported forms:

skills:modularization, docs, testing  
skills modularization docs testing  
skills=modularization,docs  

Rules:

- Multiple skills may be comma-separated or space-separated.
- Order must be preserved.
- Duplicate skills should be deduplicated (keeping the first occurrence).
- Invalid skill names should be ignored.

### 3.3 Behavior

If explicit syntax is used:

- Only the explicitly referenced skills are applied.
- No fuzzy inference should override explicit declarations.
- If a referenced skill does not exist, it should be ignored (optionally logged in verbose mode).


---

## 4. Fuzzy Skill Matching (Implicit Mode)

Fuzzy matching should be applied only when:

- The user mentions the word `skill` or `skills`
- But does not use explicit `skill:` or `skills:` syntax

Example:

Refactor this repo using modularization skill.  
Apply documentation skill here.  
Use testing skill for this change.

### 4.1 Candidate extraction rules

- Extract the candidate skill name from phrases like:
  - `using <X> skill`
  - `apply <X> skill`
  - `<X> skill`
- `<X>` must be a **single token** matching the recommended pattern: `[A-Za-z0-9_-]+`
- Matching is performed only against file names (no aliases or keywords).
- Matching must be case-insensitive.

Notes:

- Surrounding punctuation should be tolerated by stripping common punctuation from token boundaries (e.g. `docs, skill` should treat `<X>` as `docs`).
- Quoted multi-word skill names are not supported in the initial version.

### 4.2 Scoring

Recommended scoring:

- Exact match → apply immediately
- Case-insensitive exact match → apply
- Fuzzy similarity above threshold (e.g., ≥ 0.85 normalized similarity) → apply
- Otherwise → do not apply

If multiple matches exceed threshold:

- Select the highest scoring match
- If scores are too close (ambiguous), do not apply any skill.

**Ambiguity rule (numeric):**

- Treat as ambiguous if `(best_score - second_best_score) < 0.03`.

### 4.3 Safety

- If no confident match exists → do nothing
- Never auto-apply multiple skills from fuzzy matching
- Never override explicit invocation


---

## 5. Skill Loading

At startup (or first invocation):

- Locate and scan `skills/` directory (per Section 2)
- Load all eligible `.md` files (direct children only)
- Store:
  - `skill_id` (normalized)
  - `file_path`
  - `file_contents`

Skills should be cached for performance.  
Optional: reload on file system change.


---

## 6. Skill Application Behavior

When one or more skills are applied:

- Append their contents to the system prompt.
- Order must follow user-specified order.
- If fuzzy-matched, only append the resolved skill.

Format in system prompt:

--- Applied Skill: modularization ---

<contents of modularization.md>

--- End Skill ---

This ensures:

- Clear prompt boundaries
- Easier debugging
- Transparency


---

## 7. Conflict Handling

If multiple skills are applied:

- They are appended in sequence.
- No automatic conflict resolution is required.
- The model handles instruction merging.

If the same skill is referenced multiple times:

- Deduplicate before appending.


---

## 8. Observability

Observability output must be **verbose-only**.

When skills are applied (and only if verbose mode is enabled):

- Display to user:

Applied skills: modularization, testing
Skills directory: <resolved path to the active skills/ directory>

Additionally (debug-only):

- If explicit invocation references unknown skills, log which ones were ignored.

This improves transparency and trust while keeping default output clean.


---

## 9. Non-Goals (Out of Scope for Initial Version)

- No skill metadata (aliases, keywords, priority)
- No LLM-based skill inference
- No skill dependency resolution
- No automatic skill suggestion (can be added later)
- No UI for skill management


---

## 10. Future Extensions (Optional)

- Skill suggestion engine (non-intrusive)
- Skill conflict detection
- Skill categories
- Repo-local + global skills
- User-level skills directory


---

## 11. Design Principles

- Deterministic over magical
- Simple file drop → working skill
- Explicit syntax is primary path
- Fuzzy matching is assistive, not authoritative
- No hidden behavior


---

## 12. Success Criteria

- Users can create a skill by adding a `.md` file.
- Users can activate it with `skill:<name>`.
- Multiple skills can be combined.
- Fuzzy matching works for natural phrasing.
- No unintended skill application occurs.


---

End of requirements.

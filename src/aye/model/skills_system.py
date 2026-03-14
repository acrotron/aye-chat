"""Skills system: discovery, scanning, parsing, and rendering of repo-local skills.

Skills are .md files under the first ``skills/`` directory found by walking
upward from the current working directory.  They can be invoked explicitly
(e.g. ``skill:foo``) or matched fuzzily when the user mentions "skill" in
natural language.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set

from rapidfuzz.fuzz import ratio as _fuzz_ratio

from aye.model.ignore_patterns import load_ignore_patterns

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SKILL_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]+")

FUZZY_TRIGGER_VERBS: frozenset = frozenset({
    "using", "apply", "use", "with", "enable", "activate", "try",
})

FUZZY_SIMILARITY_THRESHOLD = 0.85
FUZZY_AMBIGUITY_MARGIN = 0.03

_PUNCT_CHARS = ",.;:!?)(][}{\"'"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Skill:
    """A single discovered skill."""
    skill_id: str       # normalized (lowercase, stripped)
    file_path: Path
    contents: str


@dataclass
class SkillsIndex:
    """Cached index of all skills under a skills directory."""
    skills_dir: Path
    skills: Dict[str, Skill]   # keyed by normalized skill_id
    dir_mtime: float           # skills_dir stat mtime at scan time


@dataclass
class SkillResolutionResult:
    """Result of resolving which skills to apply for a given prompt."""
    skill_ids: List[str]       # known skill IDs to apply (ordered, deduped)
    unknown_ids: List[str]     # explicit tokens that matched no known skill


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------

class SkillsResolver:
    """Discovers, caches, parses and renders repo-local skills."""

    def __init__(self) -> None:
        self._cache: Dict[Path, SkillsIndex] = {}

    # -- Public API --------------------------------------------------------

    def get_index(self, start_dir: Path) -> Optional[SkillsIndex]:
        """Return the skills index for *start_dir*, or ``None`` if no
        ``skills/`` directory is found.  Results are cached and invalidated
        when the directory mtime changes."""
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
                pass  # directory disappeared or inaccessible; re-scan

        index = self._scan_skills(skills_dir)
        self._cache[skills_dir] = index
        return index

    def resolve_applied_skills(
        self, prompt: str, index: SkillsIndex,
    ) -> SkillResolutionResult:
        """Determine which skills should be applied for *prompt*.

        Detection order:
        1. Explicit keyed forms  (``skill:foo``, ``skills=foo,bar``)
        2. Explicit bare forms   (``skill foo``, ``foo skill``)
           — falls through to fuzzy if all bare tokens are unknown
        3. Fuzzy matching        (only when ``skill``/``skills`` appears
           without explicit syntax)
        """
        known_ids = set(index.skills.keys())

        # 1. Explicit keyed forms
        explicit = self._parse_explicit_keyed(prompt)
        if explicit is not None:
            return self._filter_known(explicit, known_ids)

        # 2. Explicit bare forms — only commit if at least one token is known
        explicit = self._parse_explicit_bare(prompt)
        if explicit is not None:
            result = self._filter_known(explicit, known_ids)
            if result.skill_ids:
                return result
            # All bare tokens unknown → fall through to fuzzy

        # 3. Fuzzy matching
        fuzzy = self._parse_fuzzy(prompt, known_ids)
        return SkillResolutionResult(skill_ids=fuzzy, unknown_ids=[])

    def render_skills_for_system_prompt(
        self, skill_ids: List[str], index: SkillsIndex,
    ) -> str:
        """Return formatted skill blocks ready to append to the system prompt."""
        blocks: List[str] = []
        for sid in skill_ids:
            skill = index.skills.get(sid)
            if skill is None:
                continue
            blocks.append(
                f"--- Applied Skill: {sid} ---\n\n"
                f"{skill.contents}\n\n"
                f"--- End Skill ---"
            )
        return "\n\n".join(blocks)

    # -- Directory discovery -----------------------------------------------

    @staticmethod
    def _find_skills_dir(start_dir: Path) -> Optional[Path]:
        """Walk up from *start_dir* and return the first non-ignored
        ``skills/`` directory, or ``None``."""
        current = start_dir.resolve()
        while True:
            candidate = current / "skills"
            if candidate.is_dir():
                ignore_spec = load_ignore_patterns(current)
                if not ignore_spec.match_file("skills/"):
                    return candidate
            if current.parent == current:
                break
            current = current.parent
        return None

    # -- Scanning ----------------------------------------------------------

    @staticmethod
    def _scan_skills(skills_dir: Path) -> SkillsIndex:
        """Non-recursively scan *skills_dir* for ``.md`` files."""
        skills: Dict[str, Skill] = {}
        try:
            dir_mtime = skills_dir.stat().st_mtime
        except OSError:
            return SkillsIndex(skills_dir=skills_dir, skills={}, dir_mtime=0.0)

        try:
            entries = list(skills_dir.iterdir())
        except OSError:
            return SkillsIndex(skills_dir=skills_dir, skills={}, dir_mtime=dir_mtime)

        for entry in entries:
            if not entry.is_file():
                continue
            if entry.suffix.lower() != ".md":
                continue
            skill_id = entry.stem.strip().lower()
            if not skill_id:
                continue
            try:
                contents = entry.read_text(encoding="utf-8")
            except Exception:
                continue
            skills[skill_id] = Skill(
                skill_id=skill_id, file_path=entry, contents=contents,
            )

        return SkillsIndex(
            skills_dir=skills_dir, skills=skills, dir_mtime=dir_mtime,
        )

    # -- Explicit parsing: keyed forms ------------------------------------

    @staticmethod
    def _parse_explicit_keyed(prompt: str) -> Optional[List[str]]:
        """Detect ``skill:``, ``skills:``, ``skill=``, ``skills=`` forms.

        Returns a list of normalized tokens if any keyed invocation is
        found, ``None`` otherwise.  The capture group is limited to
        token-valid characters (plus commas and whitespace separators)
        so it stops at sentence punctuation.
        """
        pattern = re.compile(
            r'\bskills?\s*[:=]\s*([A-Za-z0-9_,\s-]+)', re.IGNORECASE,
        )
        matches = pattern.findall(prompt)
        if not matches:
            return None

        tokens: List[str] = []
        seen: Set[str] = set()
        for raw in matches:
            for part in re.split(r'[,\s]+', raw.strip()):
                part = part.strip()
                if not SKILL_TOKEN_RE.fullmatch(part):
                    continue
                normalized = part.lower()
                if normalized not in seen:
                    seen.add(normalized)
                    tokens.append(normalized)

        return tokens if tokens else None

    # -- Explicit parsing: bare word-order forms --------------------------

    @staticmethod
    def _parse_explicit_bare(prompt: str) -> Optional[List[str]]:
        """Detect bare forms: ``skill <token>``, ``<token> skill``,
        ``skills <t1> <t2> ...``.

        Skips matches where a fuzzy-trigger verb is in a relevant position.
        Returns a list of normalized tokens if any bare invocation is found,
        ``None`` otherwise.
        """
        tokens: List[str] = []
        seen: Set[str] = set()

        # --- "skill <token>" (singular only) ---
        for m in re.finditer(
            r'\bskill\s+([A-Za-z0-9_-]+)', prompt, re.IGNORECASE,
        ):
            # Guard against keyed-form remnants
            span_text = prompt[m.start():m.end()]
            if ':' in span_text or '=' in span_text:
                continue
            normalized = m.group(1).strip().lower()
            if normalized not in FUZZY_TRIGGER_VERBS and normalized not in seen:
                seen.add(normalized)
                tokens.append(normalized)

        # --- "skills <token1> <token2> ..." (plural, anywhere) ---
        for m in re.finditer(
            r'\bskills\s+([A-Za-z0-9_,\s-]+)',
            prompt,
            re.IGNORECASE,
        ):
            # Guard against keyed-form remnants
            prefix = prompt[m.start():m.start() + len('skills') + 1]
            if ':' in prefix or '=' in prefix:
                continue
            raw = m.group(1).strip()
            for part in re.split(r'[,\s]+', raw):
                part = part.strip()
                if not part or not SKILL_TOKEN_RE.fullmatch(part):
                    continue
                normalized = part.lower()
                if normalized not in FUZZY_TRIGGER_VERBS and normalized not in seen:
                    seen.add(normalized)
                    tokens.append(normalized)

        # --- "<token> skill" (singular only) ---
        for m in re.finditer(
            r'\b([A-Za-z0-9_-]+)\s+skill\b', prompt, re.IGNORECASE,
        ):
            token = m.group(1).strip().lower()
            # Skip if the token itself is a fuzzy-trigger verb
            if token in FUZZY_TRIGGER_VERBS:
                continue
            # Check the word immediately preceding the token
            preceding_text = prompt[:m.start()].rstrip()
            if preceding_text:
                prev_word = preceding_text.split()[-1].lower().strip(_PUNCT_CHARS)
                if prev_word in FUZZY_TRIGGER_VERBS:
                    continue
            if token not in seen:
                seen.add(token)
                tokens.append(token)

        return tokens if tokens else None

    # -- Fuzzy parsing -----------------------------------------------------

    @staticmethod
    def _parse_fuzzy(prompt: str, known_ids: Set[str]) -> List[str]:
        """Run fuzzy matching when the prompt mentions *skill*/*skills*
        without explicit syntax.  Returns at most one skill ID."""
        # Must mention "skill" or "skills" as a standalone word
        if not re.search(r'\bskills?\b', prompt, re.IGNORECASE):
            return []

        # Must not contain keyed syntax (shouldn't reach here, but guard)
        if re.search(r'\bskills?\s*[:=]', prompt, re.IGNORECASE):
            return []

        if not known_ids:
            return []

        # Extract candidates: word immediately before "skill(s)"
        candidates: List[str] = []
        for m in re.finditer(
            r'\b([A-Za-z0-9_-]+)\s+skills?\b', prompt, re.IGNORECASE,
        ):
            raw = m.group(1).strip(_PUNCT_CHARS).strip().lower()
            if raw and SKILL_TOKEN_RE.fullmatch(raw):
                candidates.append(raw)

        if not candidates:
            return []

        known_list = sorted(known_ids)

        for candidate in candidates:
            # Exact match shortcut
            if candidate in known_ids:
                return [candidate]

            # Compute similarity against every known skill
            scores = [
                (sid, _fuzz_ratio(candidate, sid) / 100.0)
                for sid in known_list
            ]
            scores.sort(key=lambda x: x[1], reverse=True)

            if not scores:
                continue

            best_id, best_score = scores[0]
            if best_score < FUZZY_SIMILARITY_THRESHOLD:
                continue

            # Ambiguity check
            if len(scores) > 1:
                _, second_score = scores[1]
                if (best_score - second_score) < FUZZY_AMBIGUITY_MARGIN:
                    continue  # ambiguous — apply nothing

            return [best_id]

        return []

    # -- Helpers -----------------------------------------------------------

    @staticmethod
    def _filter_known(
        tokens: List[str], known_ids: Set[str],
    ) -> SkillResolutionResult:
        """Filter explicit tokens to only known skill IDs.
        Preserves order and deduplicates."""
        skill_ids: List[str] = []
        unknown_ids: List[str] = []
        seen: Set[str] = set()
        for token in tokens:
            if token in seen:
                continue
            seen.add(token)
            if token in known_ids:
                skill_ids.append(token)
            else:
                unknown_ids.append(token)
        return SkillResolutionResult(skill_ids=skill_ids, unknown_ids=unknown_ids)

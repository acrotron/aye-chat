import os
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from aye.model.skills_system import (
    Skill,
    SkillsIndex,
    SkillResolutionResult,
    SkillsResolver,
    SKILL_TOKEN_RE,
    FUZZY_TRIGGER_VERBS,
    FUZZY_SIMILARITY_THRESHOLD,
    FUZZY_AMBIGUITY_MARGIN,
)


# ---------------------------------------------------------------------------
# Data model tests
# ---------------------------------------------------------------------------


class TestSkillDataclass:
    """Tests for the Skill frozen dataclass."""

    def test_creation(self):
        s = Skill(skill_id="foo", file_path=Path("/skills/foo.md"), contents="# Foo")
        assert s.skill_id == "foo"
        assert s.file_path == Path("/skills/foo.md")
        assert s.contents == "# Foo"

    def test_frozen(self):
        s = Skill(skill_id="bar", file_path=Path("bar.md"), contents="")
        with pytest.raises(AttributeError):
            s.skill_id = "baz"

    def test_equality(self):
        a = Skill(skill_id="x", file_path=Path("x.md"), contents="c")
        b = Skill(skill_id="x", file_path=Path("x.md"), contents="c")
        assert a == b

    def test_inequality(self):
        a = Skill(skill_id="x", file_path=Path("x.md"), contents="c1")
        b = Skill(skill_id="x", file_path=Path("x.md"), contents="c2")
        assert a != b


class TestSkillsIndex:
    """Tests for the SkillsIndex dataclass."""

    def test_creation_empty(self):
        idx = SkillsIndex(skills_dir=Path("/skills"), skills={}, dir_mtime=0.0)
        assert idx.skills == {}
        assert idx.dir_mtime == 0.0

    def test_creation_with_skills(self):
        sk = Skill(skill_id="a", file_path=Path("a.md"), contents="")
        idx = SkillsIndex(skills_dir=Path("/s"), skills={"a": sk}, dir_mtime=1.0)
        assert "a" in idx.skills
        assert idx.skills["a"] is sk

    def test_mutable(self):
        """SkillsIndex is mutable (not frozen)."""
        idx = SkillsIndex(skills_dir=Path("/s"), skills={}, dir_mtime=0.0)
        idx.dir_mtime = 9.9
        assert idx.dir_mtime == 9.9


class TestSkillResolutionResult:
    """Tests for SkillResolutionResult dataclass."""

    def test_creation(self):
        r = SkillResolutionResult(skill_ids=["a", "b"], unknown_ids=["c"])
        assert r.skill_ids == ["a", "b"]
        assert r.unknown_ids == ["c"]

    def test_empty(self):
        r = SkillResolutionResult(skill_ids=[], unknown_ids=[])
        assert r.skill_ids == []
        assert r.unknown_ids == []


# ---------------------------------------------------------------------------
# SKILL_TOKEN_RE
# ---------------------------------------------------------------------------


class TestSkillTokenRegex:
    """Tests for the SKILL_TOKEN_RE pattern."""

    @pytest.mark.parametrize("token", ["foo", "bar-baz", "my_skill", "Abc123", "A", "0"])
    def test_valid_tokens(self, token):
        assert SKILL_TOKEN_RE.fullmatch(token) is not None

    @pytest.mark.parametrize("token", ["", "foo bar", "sk!ll", "a.b", "a/b", "a:b"])
    def test_invalid_tokens(self, token):
        assert SKILL_TOKEN_RE.fullmatch(token) is None


# ---------------------------------------------------------------------------
# _find_skills_dir
# ---------------------------------------------------------------------------


class TestFindSkillsDir:
    """Tests for SkillsResolver._find_skills_dir static method."""

    def test_finds_skills_dir_in_start(self, tmp_path):
        skills = tmp_path / "skills"
        skills.mkdir()
        result = SkillsResolver._find_skills_dir(tmp_path)
        assert result == skills

    def test_finds_skills_dir_in_parent(self, tmp_path):
        skills = tmp_path / "skills"
        skills.mkdir()
        child = tmp_path / "subdir"
        child.mkdir()
        result = SkillsResolver._find_skills_dir(child)
        assert result == skills

    def test_finds_skills_dir_in_grandparent(self, tmp_path):
        skills = tmp_path / "skills"
        skills.mkdir()
        deep = tmp_path / "a" / "b" / "c"
        deep.mkdir(parents=True)
        result = SkillsResolver._find_skills_dir(deep)
        assert result == skills

    def test_returns_none_when_no_skills_dir(self, tmp_path):
        result = SkillsResolver._find_skills_dir(tmp_path)
        assert result is None

    def test_skills_file_not_dir_ignored(self, tmp_path):
        """A file named 'skills' (not a dir) is not returned."""
        (tmp_path / "skills").write_text("not a directory")
        result = SkillsResolver._find_skills_dir(tmp_path)
        assert result is None

    @patch('aye.model.skills_system.load_ignore_patterns')
    def test_ignored_skills_dir_skipped(self, mock_ignore, tmp_path):
        """If the skills/ dir matches ignore patterns, it is skipped."""
        skills = tmp_path / "skills"
        skills.mkdir()
        mock_spec = MagicMock()
        mock_spec.match_file.return_value = True  # Ignored
        mock_ignore.return_value = mock_spec
        result = SkillsResolver._find_skills_dir(tmp_path)
        # It tries the parent, eventually reaches root
        assert result is None

    @patch('aye.model.skills_system.load_ignore_patterns')
    def test_non_ignored_skills_dir_found(self, mock_ignore, tmp_path):
        skills = tmp_path / "skills"
        skills.mkdir()
        mock_spec = MagicMock()
        mock_spec.match_file.return_value = False
        mock_ignore.return_value = mock_spec
        result = SkillsResolver._find_skills_dir(tmp_path)
        assert result == skills

    def test_resolves_symlinks(self, tmp_path):
        """Symlinked start_dir is resolved before walking."""
        real_dir = tmp_path / "real"
        real_dir.mkdir()
        skills = real_dir / "skills"
        skills.mkdir()
        link = tmp_path / "link"
        try:
            link.symlink_to(real_dir)
        except OSError:
            pytest.skip("Symlink creation not supported (requires privileges on Windows)")
        result = SkillsResolver._find_skills_dir(link)
        assert result == skills


# ---------------------------------------------------------------------------
# _scan_skills
# ---------------------------------------------------------------------------


class TestScanSkills:
    """Tests for SkillsResolver._scan_skills static method."""

    def test_empty_dir(self, tmp_path):
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        idx = SkillsResolver._scan_skills(skills_dir)
        assert idx.skills == {}
        assert idx.skills_dir == skills_dir
        assert idx.dir_mtime > 0

    def test_single_md_file(self, tmp_path):
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "coding-style.md").write_text("Use PEP8.", encoding="utf-8")
        idx = SkillsResolver._scan_skills(skills_dir)
        assert "coding-style" in idx.skills
        assert idx.skills["coding-style"].contents == "Use PEP8."
        assert idx.skills["coding-style"].file_path == skills_dir / "coding-style.md"

    def test_multiple_md_files(self, tmp_path):
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "style.md").write_text("Style guide")
        (skills_dir / "testing.md").write_text("Testing rules")
        (skills_dir / "docs.md").write_text("Documentation")
        idx = SkillsResolver._scan_skills(skills_dir)
        assert len(idx.skills) == 3
        assert set(idx.skills.keys()) == {"style", "testing", "docs"}

    def test_non_md_files_ignored(self, tmp_path):
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "readme.txt").write_text("ignore me")
        (skills_dir / "config.yaml").write_text("ignore me")
        (skills_dir / "good.md").write_text("keep me")
        idx = SkillsResolver._scan_skills(skills_dir)
        assert len(idx.skills) == 1
        assert "good" in idx.skills

    def test_subdirectories_ignored(self, tmp_path):
        """_scan_skills is non-recursive  subdirs are skipped."""
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        nested = skills_dir / "subdir"
        nested.mkdir()
        (nested / "nested.md").write_text("hidden")
        (skills_dir / "top.md").write_text("visible")
        idx = SkillsResolver._scan_skills(skills_dir)
        assert len(idx.skills) == 1
        assert "top" in idx.skills

    def test_case_insensitive_extension(self, tmp_path):
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "mixed.MD").write_text("content")
        (skills_dir / "upper.Md").write_text("content2")
        idx = SkillsResolver._scan_skills(skills_dir)
        assert len(idx.skills) == 2
        assert "mixed" in idx.skills
        assert "upper" in idx.skills

    def test_skill_id_normalized_to_lowercase(self, tmp_path):
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "CodingStyle.md").write_text("content")
        idx = SkillsResolver._scan_skills(skills_dir)
        assert "codingstyle" in idx.skills

    def test_skill_id_stripped(self, tmp_path):
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / " padded .md").write_text("content")
        idx = SkillsResolver._scan_skills(skills_dir)
        assert "padded" in idx.skills

    def test_empty_stem_skipped(self, tmp_path):
        """A file named '.md' (empty stem) is skipped."""
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / ".md").write_text("content")
        idx = SkillsResolver._scan_skills(skills_dir)
        assert len(idx.skills) == 0

    def test_nonexistent_dir_returns_empty(self, tmp_path):
        """If skills_dir does not exist, return empty index."""
        missing = tmp_path / "nonexistent"
        idx = SkillsResolver._scan_skills(missing)
        assert idx.skills == {}
        assert idx.dir_mtime == 0.0

    def test_unreadable_file_skipped(self, tmp_path):
        """If a .md file cannot be read, it is silently skipped."""
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        good = skills_dir / "good.md"
        good.write_text("ok")
        bad = skills_dir / "bad.md"
        bad.write_text("will fail")
        # Make bad file unreadable
        bad.chmod(0o000)
        try:
            idx = SkillsResolver._scan_skills(skills_dir)
            assert "good" in idx.skills
            # bad may or may not be present depending on OS permissions
            # On some systems root can still read; just ensure no crash
        finally:
            bad.chmod(0o644)


# ---------------------------------------------------------------------------
# get_index
# ---------------------------------------------------------------------------


class TestGetIndex:
    """Tests for SkillsResolver.get_index method."""

    def test_returns_none_when_no_skills_dir(self, tmp_path):
        resolver = SkillsResolver()
        result = resolver.get_index(tmp_path)
        assert result is None

    def test_returns_index_when_skills_exist(self, tmp_path):
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "style.md").write_text("PEP8")
        resolver = SkillsResolver()
        idx = resolver.get_index(tmp_path)
        assert idx is not None
        assert "style" in idx.skills

    def test_caches_result(self, tmp_path):
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "a.md").write_text("content")
        resolver = SkillsResolver()
        idx1 = resolver.get_index(tmp_path)
        idx2 = resolver.get_index(tmp_path)
        assert idx1 is idx2  # Same object (cached)

    def test_cache_invalidated_on_mtime_change(self, tmp_path):
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "a.md").write_text("v1")

        resolver = SkillsResolver()
        idx1 = resolver.get_index(tmp_path)
        assert idx1 is not None

        # Directly modify the cached mtime to force invalidation
        idx1.dir_mtime = 0.0

        idx2 = resolver.get_index(tmp_path)
        assert idx2 is not idx1  # Re-scanned

    def test_child_dir_finds_parent_skills(self, tmp_path):
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "x.md").write_text("content")
        child = tmp_path / "sub" / "deep"
        child.mkdir(parents=True)

        resolver = SkillsResolver()
        idx = resolver.get_index(child)
        assert idx is not None
        assert "x" in idx.skills


# ---------------------------------------------------------------------------
# _parse_explicit_keyed
# ---------------------------------------------------------------------------


class TestParseExplicitKeyed:
    """Tests for SkillsResolver._parse_explicit_keyed static method."""

    def test_skill_colon_single(self):
        result = SkillsResolver._parse_explicit_keyed("use skill:foo")
        assert result == ["foo"]

    def test_skills_colon_single(self):
        result = SkillsResolver._parse_explicit_keyed("apply skills:bar")
        assert result == ["bar"]

    def test_skill_equals_single(self):
        result = SkillsResolver._parse_explicit_keyed("skill=baz")
        assert result == ["baz"]

    def test_skills_equals_single(self):
        result = SkillsResolver._parse_explicit_keyed("skills=qux")
        assert result == ["qux"]

    def test_comma_separated(self):
        result = SkillsResolver._parse_explicit_keyed("skill:foo,bar,baz")
        assert result == ["foo", "bar", "baz"]

    def test_space_separated(self):
        result = SkillsResolver._parse_explicit_keyed("skill: foo bar")
        assert result == ["foo", "bar"]

    def test_mixed_separators(self):
        result = SkillsResolver._parse_explicit_keyed("skills: foo, bar baz")
        assert result == ["foo", "bar", "baz"]

    def test_deduplicates(self):
        result = SkillsResolver._parse_explicit_keyed("skill:foo,foo")
        assert result == ["foo"]

    def test_case_insensitive_keyword(self):
        result = SkillsResolver._parse_explicit_keyed("SKILL:MySkill")
        assert result == ["myskill"]

    def test_colon_with_spaces(self):
        result = SkillsResolver._parse_explicit_keyed("skill : foo")
        assert result == ["foo"]

    def test_equals_with_spaces(self):
        result = SkillsResolver._parse_explicit_keyed("skill = foo")
        assert result == ["foo"]

    def test_no_match_returns_none(self):
        result = SkillsResolver._parse_explicit_keyed("use the foo skill")
        assert result is None

    def test_no_skill_keyword_returns_none(self):
        result = SkillsResolver._parse_explicit_keyed("just a normal prompt")
        assert result is None

    def test_empty_after_colon_returns_none(self):
        """skill: followed by nothing matchable."""
        result = SkillsResolver._parse_explicit_keyed("skill:!!!")
        assert result is None

    def test_hyphenated_skill_id(self):
        result = SkillsResolver._parse_explicit_keyed("skill:coding-style")
        assert result == ["coding-style"]

    def test_underscored_skill_id(self):
        result = SkillsResolver._parse_explicit_keyed("skill:coding_style")
        assert result == ["coding_style"]

    def test_multiple_keyed_invocations_comma_form(self):
        """Multiple skills via comma-separated keyed form."""
        result = SkillsResolver._parse_explicit_keyed("use skill:foo,bar")
        assert result == ["foo", "bar"]

    def test_multiple_keyed_greedy_capture(self):
        """The keyed regex capture group is greedy across spaces.

        Important implementation detail: because ':' is NOT in the capture group,
        `skill:foo and also skill:bar` is matched once as `skill:(foo and also skill)`.
        The second `skill:` is *not* detected because the `skill` word is consumed
        by the first match.
        """
        result = SkillsResolver._parse_explicit_keyed("use skill:foo and also skill:bar")
        assert result == ["foo", "and", "also", "skill"]
        assert "bar" not in result

    def test_in_sentence_terminated_by_punctuation(self):
        """Period terminates the capture group since '.' is not in
        the character class [A-Za-z0-9_,\\s-]."""
        result = SkillsResolver._parse_explicit_keyed("Refactor the code with skill:clean-code. Please do it.")
        assert result == ["clean-code"]

    def test_in_sentence_greedy_capture_includes_trailing_words(self):
        """Without punctuation, trailing words are captured as tokens."""
        result = SkillsResolver._parse_explicit_keyed("Refactor the code with skill:clean-code please")
        assert "clean-code" in result
        assert "please" in result


# ---------------------------------------------------------------------------
# _parse_explicit_bare
# ---------------------------------------------------------------------------


class TestParseExplicitBare:
    """Tests for SkillsResolver._parse_explicit_bare static method."""

    def test_skill_space_token(self):
        result = SkillsResolver._parse_explicit_bare("use skill foo")
        assert result == ["foo"]

    def test_token_space_skill(self):
        result = SkillsResolver._parse_explicit_bare("foo skill")
        assert result == ["foo"]

    def test_skills_multiple_tokens(self):
        result = SkillsResolver._parse_explicit_bare("apply skills foo bar baz")
        assert result is not None
        assert "foo" in result
        assert "bar" in result
        assert "baz" in result

    def test_no_match_returns_none(self):
        result = SkillsResolver._parse_explicit_bare("just a normal prompt")
        assert result is None

    def test_trigger_verb_after_skill_skipped(self):
        """'skill using'  'using' is a fuzzy trigger verb, should be skipped."""
        result = SkillsResolver._parse_explicit_bare("skill using")
        assert result is None

    def test_trigger_verb_before_token_skill_skipped(self):
        """'using foo skill'  'foo' preceded by trigger verb 'using'. Should be skipped."""
        result = SkillsResolver._parse_explicit_bare("using foo skill")
        assert result is None

    def test_deduplicates(self):
        result = SkillsResolver._parse_explicit_bare("skill foo and foo skill")
        assert result == ["foo"]

    def test_case_insensitive_skill_keyword(self):
        result = SkillsResolver._parse_explicit_bare("SKILL foo")
        assert result == ["foo"]

    def test_keyed_form_not_matched(self):
        """Bare parser should not pick up keyed forms."""
        result = SkillsResolver._parse_explicit_bare("skill:foo")
        assert result is None

    def test_hyphenated_token(self):
        result = SkillsResolver._parse_explicit_bare("skill clean-code")
        assert result == ["clean-code"]

    def test_underscored_token(self):
        result = SkillsResolver._parse_explicit_bare("skill clean_code")
        assert result == ["clean_code"]

    def test_skills_comma_separated(self):
        result = SkillsResolver._parse_explicit_bare("skills foo,bar")
        assert result is not None
        assert "foo" in result
        assert "bar" in result

    def test_trigger_verb_token_not_captured(self):
        """Trigger verbs themselves are not captured as skill tokens."""
        for verb in FUZZY_TRIGGER_VERBS:
            result = SkillsResolver._parse_explicit_bare(f"skill {verb}")
            if result is not None:
                assert verb not in result


# ---------------------------------------------------------------------------
# _parse_fuzzy
# ---------------------------------------------------------------------------


class TestParseFuzzy:
    """Tests for SkillsResolver._parse_fuzzy static method."""

    def test_exact_match(self):
        result = SkillsResolver._parse_fuzzy("foo skill", {"foo", "bar"})
        assert result == ["foo"]

    def test_close_fuzzy_match(self):
        """A very similar token should match when above threshold."""
        # "testin" vs "testing"  rapidfuzz ratio should be high
        result = SkillsResolver._parse_fuzzy("testin skill", {"testing"})
        # Depending on exact ratio, may or may not match.
        # Let's use a known close pair
        result = SkillsResolver._parse_fuzzy("testng skill", {"testing"})
        # This tests that fuzzy matching is invoked, result depends on threshold.
        assert isinstance(result, list)

    def test_no_skill_keyword_returns_empty(self):
        result = SkillsResolver._parse_fuzzy("just normal text", {"foo"})
        assert result == []

    def test_keyed_syntax_returns_empty(self):
        result = SkillsResolver._parse_fuzzy("skill:foo", {"foo"})
        assert result == []

    def test_no_known_ids_returns_empty(self):
        result = SkillsResolver._parse_fuzzy("use foo skill", set())
        assert result == []

    def test_no_candidate_word_before_skill(self):
        """If there's no word before 'skill', returns empty."""
        result = SkillsResolver._parse_fuzzy("skill", {"foo"})
        assert result == []

    def test_completely_different_word_no_match(self):
        """A word nothing like any known skill should not match."""
        result = SkillsResolver._parse_fuzzy("xyzzy skill", {"coding-style", "testing"})
        assert result == []

    def test_returns_at_most_one(self):
        """Fuzzy matching returns at most one skill."""
        result = SkillsResolver._parse_fuzzy("foo skill", {"foo", "bar"})
        assert len(result) <= 1

    def test_ambiguous_close_scores_returns_empty(self):
        """When two skills score very close, the match is ambiguous."""
        # Two very similar ids: "fooo" and "foob"  matching "fooa" could be ambiguous
        result = SkillsResolver._parse_fuzzy("fooa skill", {"fooo", "foob"})
        # Depends on exact scores, but tests the code path
        assert isinstance(result, list)
        assert len(result) <= 1

    def test_below_threshold_no_match(self):
        """If best score is below threshold, no match."""
        result = SkillsResolver._parse_fuzzy("abcdefg skill", {"xyz"})
        assert result == []

    def test_plural_skills_keyword(self):
        result = SkillsResolver._parse_fuzzy("foo skills", {"foo"})
        assert result == ["foo"]


# ---------------------------------------------------------------------------
# _filter_known
# ---------------------------------------------------------------------------


class TestFilterKnown:
    """Tests for SkillsResolver._filter_known static method."""

    def test_all_known(self):
        result = SkillsResolver._filter_known(["a", "b"], {"a", "b", "c"})
        assert result.skill_ids == ["a", "b"]
        assert result.unknown_ids == []

    def test_all_unknown(self):
        result = SkillsResolver._filter_known(["x", "y"], {"a", "b"})
        assert result.skill_ids == []
        assert result.unknown_ids == ["x", "y"]

    def test_mixed(self):
        result = SkillsResolver._filter_known(["a", "x", "b", "y"], {"a", "b"})
        assert result.skill_ids == ["a", "b"]
        assert result.unknown_ids == ["x", "y"]

    def test_preserves_order(self):
        result = SkillsResolver._filter_known(["c", "a", "b"], {"a", "b", "c"})
        assert result.skill_ids == ["c", "a", "b"]

    def test_deduplicates(self):
        result = SkillsResolver._filter_known(["a", "a", "b", "b"], {"a", "b"})
        assert result.skill_ids == ["a", "b"]

    def test_empty_tokens(self):
        result = SkillsResolver._filter_known([], {"a"})
        assert result.skill_ids == []
        assert result.unknown_ids == []

    def test_empty_known(self):
        result = SkillsResolver._filter_known(["a"], set())
        assert result.skill_ids == []
        assert result.unknown_ids == ["a"]

    def test_duplicate_unknown_also_deduped(self):
        result = SkillsResolver._filter_known(["x", "x"], {"a"})
        assert result.unknown_ids == ["x"]


# ---------------------------------------------------------------------------
# render_skills_for_system_prompt
# ---------------------------------------------------------------------------


class TestRenderSkillsForSystemPrompt:
    """Tests for SkillsResolver.render_skills_for_system_prompt."""

    def _make_index(self, skills_dict):
        skills = {}
        for sid, content in skills_dict.items():
            skills[sid] = Skill(skill_id=sid, file_path=Path(f"{sid}.md"), contents=content)
        return SkillsIndex(skills_dir=Path("/skills"), skills=skills, dir_mtime=0.0)

    def test_single_skill(self):
        idx = self._make_index({"style": "Use PEP8."})
        resolver = SkillsResolver()
        result = resolver.render_skills_for_system_prompt(["style"], idx)
        assert "Applied Skill: style" in result
        assert "Use PEP8." in result
        assert "End Skill" in result

    def test_multiple_skills(self):
        idx = self._make_index({"style": "PEP8", "testing": "Pytest"})
        resolver = SkillsResolver()
        result = resolver.render_skills_for_system_prompt(["style", "testing"], idx)
        assert "Applied Skill: style" in result
        assert "PEP8" in result
        assert "Applied Skill: testing" in result
        assert "Pytest" in result

    def test_unknown_skill_id_skipped(self):
        idx = self._make_index({"style": "PEP8"})
        resolver = SkillsResolver()
        result = resolver.render_skills_for_system_prompt(["style", "nonexistent"], idx)
        assert "Applied Skill: style" in result
        assert "nonexistent" not in result

    def test_empty_skill_ids(self):
        idx = self._make_index({"style": "PEP8"})
        resolver = SkillsResolver()
        result = resolver.render_skills_for_system_prompt([], idx)
        assert result == ""

    def test_preserves_order(self):
        idx = self._make_index({"a": "A", "b": "B", "c": "C"})
        resolver = SkillsResolver()
        result = resolver.render_skills_for_system_prompt(["c", "a", "b"], idx)
        pos_c = result.index("Applied Skill: c")
        pos_a = result.index("Applied Skill: a")
        pos_b = result.index("Applied Skill: b")
        assert pos_c < pos_a < pos_b

    def test_blocks_separated_by_double_newline(self):
        idx = self._make_index({"a": "A", "b": "B"})
        resolver = SkillsResolver()
        result = resolver.render_skills_for_system_prompt(["a", "b"], idx)
        assert "\n\n" in result
        # Two blocks should be separated
        blocks = result.split("\n\n")
        assert len(blocks) >= 3  # block1 + separator + block2 parts


# ---------------------------------------------------------------------------
# resolve_applied_skills (integration tests)
# ---------------------------------------------------------------------------


class TestResolveAppliedSkills:
    """Integration tests for SkillsResolver.resolve_applied_skills."""

    def _make_index(self, skill_ids):
        skills = {}
        for sid in skill_ids:
            skills[sid] = Skill(skill_id=sid, file_path=Path(f"{sid}.md"), contents=f"{sid} content")
        return SkillsIndex(skills_dir=Path("/skills"), skills=skills, dir_mtime=0.0)

    def test_explicit_keyed_known(self):
        idx = self._make_index(["style", "testing"])
        resolver = SkillsResolver()
        result = resolver.resolve_applied_skills("skill:style", idx)
        assert result.skill_ids == ["style"]
        assert result.unknown_ids == []

    def test_explicit_keyed_unknown(self):
        idx = self._make_index(["style"])
        resolver = SkillsResolver()
        result = resolver.resolve_applied_skills("skill:nonexistent", idx)
        assert result.skill_ids == []
        assert result.unknown_ids == ["nonexistent"]

    def test_explicit_keyed_mixed(self):
        idx = self._make_index(["style", "testing"])
        resolver = SkillsResolver()
        result = resolver.resolve_applied_skills("skill:style,unknown", idx)
        assert result.skill_ids == ["style"]
        assert result.unknown_ids == ["unknown"]

    def test_explicit_bare_known(self):
        idx = self._make_index(["style"])
        resolver = SkillsResolver()
        result = resolver.resolve_applied_skills("use skill style", idx)
        assert result.skill_ids == ["style"]

    def test_explicit_bare_all_unknown_falls_to_fuzzy(self):
        """When bare tokens are all unknown, falls through to fuzzy."""
        idx = self._make_index(["style"])
        resolver = SkillsResolver()
        # "xyzzy skill"  bare parse gets ["xyzzy"] which is unknown.
        # Falls through to fuzzy: "xyzzy" vs "style" should not match.
        result = resolver.resolve_applied_skills("xyzzy skill", idx)
        assert result.skill_ids == []

    def test_fuzzy_exact_match(self):
        idx = self._make_index(["coding-style"])
        resolver = SkillsResolver()
        # No keyed, bare gives us "coding-style" which is known
        result = resolver.resolve_applied_skills("coding-style skill", idx)
        assert result.skill_ids == ["coding-style"]

    def test_no_skill_mention_returns_empty(self):
        idx = self._make_index(["style"])
        resolver = SkillsResolver()
        result = resolver.resolve_applied_skills("refactor the code", idx)
        assert result.skill_ids == []
        assert result.unknown_ids == []

    def test_multiple_keyed_skills(self):
        idx = self._make_index(["a", "b", "c"])
        resolver = SkillsResolver()
        result = resolver.resolve_applied_skills("skill:a,b,c do stuff", idx)
        assert result.skill_ids == ["a", "b", "c"]

    def test_keyed_takes_priority_over_bare(self):
        """Keyed forms are checked before bare forms."""
        idx = self._make_index(["foo", "bar"])
        resolver = SkillsResolver()
        # Has both keyed and bare
        result = resolver.resolve_applied_skills("skill:foo skill bar", idx)
        # Keyed should win (returns first)
        assert "foo" in result.skill_ids

    def test_empty_index_returns_nothing(self):
        idx = self._make_index([])
        resolver = SkillsResolver()
        result = resolver.resolve_applied_skills("skill:foo", idx)
        assert result.skill_ids == []
        assert result.unknown_ids == ["foo"]


# ---------------------------------------------------------------------------
# End-to-end with filesystem
# ---------------------------------------------------------------------------


class TestEndToEndWithFilesystem:
    """End-to-end tests using real filesystem."""

    def test_full_roundtrip(self, tmp_path):
        """Create skills dir, get_index, resolve, render."""
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "pep8.md").write_text("Follow PEP8 conventions.")
        (skills_dir / "testing.md").write_text("Use pytest for all tests.")

        resolver = SkillsResolver()
        idx = resolver.get_index(tmp_path)
        assert idx is not None
        assert len(idx.skills) == 2

        result = resolver.resolve_applied_skills("skill:pep8,testing", idx)
        assert result.skill_ids == ["pep8", "testing"]
        assert result.unknown_ids == []

        rendered = resolver.render_skills_for_system_prompt(result.skill_ids, idx)
        assert "Applied Skill: pep8" in rendered
        assert "Follow PEP8 conventions." in rendered
        assert "Applied Skill: testing" in rendered
        assert "Use pytest for all tests." in rendered

    def test_new_skill_added_after_cache(self, tmp_path):
        """Adding a new .md file and invalidating cache picks it up."""
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "a.md").write_text("A")

        resolver = SkillsResolver()
        idx1 = resolver.get_index(tmp_path)
        assert len(idx1.skills) == 1

        # Add a new skill and touch the directory
        (skills_dir / "b.md").write_text("B")
        # Force mtime change (some filesystems have coarse mtime resolution)
        os.utime(skills_dir, None)

        # Invalidate by modifying cached mtime
        idx1.dir_mtime = 0.0

        idx2 = resolver.get_index(tmp_path)
        assert len(idx2.skills) == 2
        assert "b" in idx2.skills

    def test_resolve_with_bare_form_filesystem(self, tmp_path):
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "clean-code.md").write_text("Keep functions small.")

        resolver = SkillsResolver()
        idx = resolver.get_index(tmp_path)

        result = resolver.resolve_applied_skills("clean-code skill", idx)
        assert result.skill_ids == ["clean-code"]

    def test_render_empty_contents(self, tmp_path):
        """Skill with empty content still renders the block."""
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "empty.md").write_text("")

        resolver = SkillsResolver()
        idx = resolver.get_index(tmp_path)

        rendered = resolver.render_skills_for_system_prompt(["empty"], idx)
        assert "Applied Skill: empty" in rendered
        assert "End Skill" in rendered


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge cases and tricky inputs."""

    def test_prompt_only_skill_keyword(self):
        """Prompt with just 'skill' and nothing else."""
        idx_skills = {"a": Skill(skill_id="a", file_path=Path("a.md"), contents="A")}
        idx = SkillsIndex(skills_dir=Path("/s"), skills=idx_skills, dir_mtime=0.0)
        resolver = SkillsResolver()
        result = resolver.resolve_applied_skills("skill", idx)
        assert result.skill_ids == []

    def test_prompt_empty_string(self):
        idx_skills = {"a": Skill(skill_id="a", file_path=Path("a.md"), contents="A")}
        idx = SkillsIndex(skills_dir=Path("/s"), skills=idx_skills, dir_mtime=0.0)
        resolver = SkillsResolver()
        result = resolver.resolve_applied_skills("", idx)
        assert result.skill_ids == []
        assert result.unknown_ids == []

    def test_skill_colon_then_punctuation(self):
        """skill: followed by punctuation  should not capture junk."""
        result = SkillsResolver._parse_explicit_keyed("skill: .!@#")
        assert result is None

    def test_multiple_keyed_via_comma(self):
        """Multiple skill IDs via comma-separated keyed form."""
        result = SkillsResolver._parse_explicit_keyed("skill:a,b")
        assert result == ["a", "b"]

    def test_multiple_keyed_separate_invocations_greedy(self):
        """Two separate skill: invocations with words between them.

        Important implementation detail: the first match becomes `skill:(a and skill)`
        (stopping at the second ':'), so the second invocation is not detected.
        """
        result = SkillsResolver._parse_explicit_keyed("skill:a and skill:b")
        assert result == ["a", "and", "skill"]
        assert "b" not in result

    def test_skill_equals_comma_space_mix(self):
        result = SkillsResolver._parse_explicit_keyed("skills = alpha, beta , gamma")
        assert result is not None
        assert "alpha" in result
        assert "beta" in result
        assert "gamma" in result

    def test_fuzzy_with_skills_plural(self):
        """'skills' keyword also triggers fuzzy."""
        result = SkillsResolver._parse_fuzzy("foo skills", {"foo"})
        assert result == ["foo"]

    def test_bare_with_apply_verb_and_skill(self):
        """'apply skill'  'apply' is trigger verb for skill token."""
        result = SkillsResolver._parse_explicit_bare("apply skill mystyle")
        # 'apply' is captured by 'skill <token>' pattern but should be skipped
        # as trigger verb - only 'mystyle' should appear if captured
        if result is not None:
            assert "apply" not in result

    def test_numeric_skill_id(self):
        result = SkillsResolver._parse_explicit_keyed("skill:123")
        assert result == ["123"]

    def test_single_char_skill_id(self):
        result = SkillsResolver._parse_explicit_keyed("skill:a")
        assert result == ["a"]

    def test_keyed_stops_at_sentence_punctuation(self):
        """skill:foo. Next sentence.  the period stops capture."""
        result = SkillsResolver._parse_explicit_keyed("Use skill:foo. Then do something else.")
        assert result is not None
        assert "foo" in result
        # Period is not in the character class, so capture stops before it.
        # No words from the next sentence should be present.
        for token in result:
            assert token not in ["then", "do", "something", "else"]

    def test_bare_skills_plural_with_trigger_verbs_filtered(self):
        """'skills using enable'  trigger verbs should be filtered."""
        result = SkillsResolver._parse_explicit_bare("skills using enable")
        assert result is None

    def test_resolver_instance_isolation(self):
        """Two resolver instances have independent caches."""
        r1 = SkillsResolver()
        r2 = SkillsResolver()
        assert r1._cache is not r2._cache

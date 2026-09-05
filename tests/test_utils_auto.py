"""Tests for aye.utils date conversion helpers."""

from datetime import date, datetime

import pytest

from aye.model.utils import (
    DEFAULT_INPUT_FORMATS,
    _run_examples,
    convert_date,
    parse_date,
)


class TestConvertDateStrings:
    def test_iso_date_to_european(self):
        assert convert_date("2025-01-31", "%d/%m/%Y") == "31/01/2025"

    def test_iso_datetime_to_date_only(self):
        assert convert_date("2025-01-31T14:30:00", "%Y-%m-%d") == "2025-01-31"

    def test_iso_datetime_with_space(self):
        assert convert_date("2025-01-31 14:30:00", "%H:%M") == "14:30"

    def test_iso_datetime_without_seconds(self):
        assert convert_date("2025-01-31 14:30", "%Y-%m-%dT%H:%M:%S") == "2025-01-31T14:30:00"

    def test_snapshot_timestamp_format(self):
        """The snapshot batch-id timestamp must be understood by default."""
        assert convert_date("20250131T143000", "%Y-%m-%d %H:%M") == "2025-01-31 14:30"

    def test_european_string_to_iso(self):
        assert convert_date("31/01/2025", "%Y-%m-%d") == "2025-01-31"

    def test_dotted_european_string(self):
        assert convert_date("31.01.2025", "%Y-%m-%d") == "2025-01-31"

    def test_dashed_day_first_string(self):
        assert convert_date("31-01-2025", "%Y-%m-%d") == "2025-01-31"

    def test_slash_iso_string(self):
        assert convert_date("2025/01/31", "%Y-%m-%d") == "2025-01-31"

    def test_long_month_name_string(self):
        assert convert_date("January 31, 2025", "%Y-%m-%d") == "2025-01-31"

    def test_short_month_name_string(self):
        assert convert_date("Jan 31 2025", "%Y-%m-%d") == "2025-01-31"

    def test_surrounding_whitespace_is_stripped(self):
        assert convert_date("  2025-01-31  ", "%d/%m/%Y") == "31/01/2025"


class TestConvertDateObjects:
    def test_date_object_to_long_form(self):
        assert convert_date(date(2025, 1, 31), "%Y-%m-%d") == "2025-01-31"

    def test_datetime_object_to_iso(self):
        value = datetime(2025, 1, 31, 14, 30, 0)
        assert convert_date(value, "%Y-%m-%dT%H:%M:%S") == "2025-01-31T14:30:00"

    def test_date_object_is_widened_to_midnight(self):
        assert convert_date(date(2025, 1, 31), "%H:%M:%S") == "00:00:00"


class TestSnapshotRoundTrip:
    def test_round_trip_through_snapshot_format(self):
        """Reformatting a snapshot timestamp and back is lossless."""
        original = "20250131T143000"
        readable = convert_date(original, "%Y-%m-%d %H:%M:%S")
        assert convert_date(readable, "%Y%m%dT%H%M%S") == original


class TestCustomInputFormats:
    def test_custom_format_is_used(self):
        assert (
            convert_date("31.01.2025", "%Y-%m-%d", input_formats=["%d.%m.%Y"])
            == "2025-01-31"
        )

    def test_custom_formats_replace_the_defaults(self):
        """A default-supported layout must fail when it is not in the custom list."""
        with pytest.raises(ValueError):
            convert_date("2025-01-31", "%Y-%m-%d", input_formats=["%d.%m.%Y"])

    def test_custom_formats_accept_any_iterable(self):
        assert (
            convert_date("2025|01|31", "%Y-%m-%d", input_formats=("%Y|%m|%d",))
            == "2025-01-31"
        )


class TestParseDate:
    def test_returns_datetime_for_snapshot_timestamp(self):
        assert parse_date("20250131T143000") == datetime(2025, 1, 31, 14, 30)

    def test_returns_datetime_for_iso_date(self):
        assert parse_date("2025-01-31") == datetime(2025, 1, 31, 0, 0)

    def test_date_is_widened_to_midnight(self):
        assert parse_date(date(2025, 1, 31)) == datetime(2025, 1, 31, 0, 0)

    def test_datetime_passes_through_unchanged(self):
        value = datetime(2025, 1, 31, 14, 30, 15)
        assert parse_date(value) is value

    def test_honours_custom_input_formats(self):
        assert parse_date("31.01.2025", input_formats=["%d.%m.%Y"]) == datetime(2025, 1, 31)


class TestErrorHandling:
    def test_empty_string_raises_value_error(self):
        with pytest.raises(ValueError, match="empty date string"):
            convert_date("", "%Y-%m-%d")

    def test_whitespace_only_string_raises_value_error(self):
        with pytest.raises(ValueError, match="empty date string"):
            convert_date("   ", "%Y-%m-%d")

    def test_unparseable_string_raises_value_error(self):
        with pytest.raises(ValueError) as exc:
            convert_date("not a date", "%Y-%m-%d")
        message = str(exc.value)
        assert "not a date" in message
        assert "tried" in message.lower()

    def test_empty_output_format_raises_value_error(self):
        with pytest.raises(ValueError, match="output_format"):
            convert_date("2025-01-31", "")

    def test_output_format_is_validated_before_parsing(self):
        """The output_format guard fires even when the value is also invalid."""
        with pytest.raises(ValueError, match="output_format"):
            convert_date("garbage", "")

    @pytest.mark.parametrize("bad", [12345, None, ["2025-01-31"], 3.14])
    def test_unsupported_types_raise_type_error(self, bad):
        with pytest.raises(TypeError):
            convert_date(bad, "%Y-%m-%d")

    @pytest.mark.parametrize("bad", [12345, None, {"y": 2025}])
    def test_parse_date_rejects_unsupported_types(self, bad):
        with pytest.raises(TypeError):
            parse_date(bad)

    def test_type_error_names_the_offending_type(self):
        with pytest.raises(TypeError, match="int"):
            parse_date(12345)


class TestDefaultInputFormats:
    def test_is_a_non_empty_sequence_of_strings(self):
        assert len(DEFAULT_INPUT_FORMATS) > 0
        assert all(isinstance(fmt, str) for fmt in DEFAULT_INPUT_FORMATS)

    def test_contains_the_snapshot_timestamp_format(self):
        assert "%Y%m%dT%H%M%S" in DEFAULT_INPUT_FORMATS

    def test_contains_iso_date_format(self):
        assert "%Y-%m-%d" in DEFAULT_INPUT_FORMATS

    def test_every_default_format_is_usable(self):
        """Each default must be a valid strftime/strptime round trip."""
        reference = datetime(2025, 1, 31, 14, 30, 0)
        for fmt in DEFAULT_INPUT_FORMATS:
            rendered = reference.strftime(fmt)
            # Must parse back through the public API without raising.
            assert isinstance(parse_date(rendered, input_formats=[fmt]), datetime)

    def test_day_first_precedes_month_first(self):
        """Ambiguous slash dates resolve day-first, matching the declared order."""
        formats = list(DEFAULT_INPUT_FORMATS)
        assert formats.index("%d/%m/%Y") < formats.index("%m/%d/%Y")
        # 01/02/2025 -> 1 February, not 2 January.
        assert convert_date("01/02/2025", "%Y-%m-%d") == "2025-02-01"


class TestRunExamples:
    def test_runs_without_raising(self, capsys):
        _run_examples()
        captured = capsys.readouterr()
        assert "convert_date examples" in captured.out

    def test_prints_the_documented_values(self, capsys):
        _run_examples()
        out = capsys.readouterr().out
        assert "31/01/2025" in out
        assert "2025-01-31 14:30" in out
        assert "January 31, 2025" in out
        assert "2025-01-31T14:30:00" in out

    def test_reports_the_unparseable_example_as_a_value_error(self, capsys):
        _run_examples()
        out = capsys.readouterr().out
        assert "ValueError:" in out

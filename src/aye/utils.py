"""Small general-purpose helpers shared across Aye Chat.

Currently this module provides date conversion utilities. The default input
formats include the snapshot timestamp format used by the snapshot backends
(``%Y%m%dT%H%M%S``, e.g. ``20250131T143000``) so batch ids can be reformatted
for display without extra parsing code at the call site.
"""

from datetime import date, datetime
from typing import Iterable, Optional, Sequence, Union

# Ordered list of formats tried when parsing a string date. The first match
# wins, so the most specific / most common formats come first.
DEFAULT_INPUT_FORMATS: Sequence[str] = (
    "%Y-%m-%dT%H:%M:%S",    # ISO-8601 datetime
    "%Y-%m-%d %H:%M:%S",    # ISO-ish datetime with a space
    "%Y-%m-%d %H:%M",       # ISO-ish datetime, no seconds
    "%Y%m%dT%H%M%S",        # Aye snapshot timestamp (see snapshot backends)
    "%Y-%m-%d",             # ISO-8601 date
    "%Y/%m/%d",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%d-%m-%Y",
    "%d.%m.%Y",
    "%b %d %Y",             # Jan 31 2025
    "%B %d, %Y",            # January 31, 2025
)

DateLike = Union[str, date, datetime]


def parse_date(
    value: DateLike,
    input_formats: Optional[Iterable[str]] = None,
) -> datetime:
    """Parse *value* into a ``datetime``.

    Args:
        value: A ``datetime``, a ``date``, or a date string.
        input_formats: Candidate ``strptime`` formats tried in order. Defaults
            to :data:`DEFAULT_INPUT_FORMATS`. Ignored for non-string input.

    Returns:
        The parsed ``datetime``. A ``date`` is widened to midnight.

    Raises:
        TypeError: If *value* is not a string, ``date``, or ``datetime``.
        ValueError: If *value* is an empty string, or matches no input format.
    """
    if isinstance(value, datetime):
        return value

    # NOTE: datetime is a subclass of date, so this check must come second.
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)

    if not isinstance(value, str):
        raise TypeError(
            f"expected str, date, or datetime, got {type(value).__name__}"
        )

    text = value.strip()
    if not text:
        raise ValueError("cannot parse an empty date string")

    formats = tuple(input_formats) if input_formats else tuple(DEFAULT_INPUT_FORMATS)

    for fmt in formats:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue

    tried = ", ".join(formats)
    raise ValueError(f"could not parse date {text!r}; tried formats: {tried}")


def convert_date(
    value: DateLike,
    output_format: str,
    input_formats: Optional[Iterable[str]] = None,
) -> str:
    """Convert a date into a formatted string.

    Args:
        value: A ``datetime``, a ``date``, or a date string to convert.
        output_format: A ``strftime`` format for the result, e.g. ``"%d/%m/%Y"``.
        input_formats: Candidate ``strptime`` formats tried in order when
            *value* is a string. Defaults to :data:`DEFAULT_INPUT_FORMATS`.

    Returns:
        The date rendered with *output_format*.

    Raises:
        TypeError: If *value* has an unsupported type.
        ValueError: If *output_format* is empty, or *value* cannot be parsed.

    Examples:
        >>> convert_date("2025-01-31", "%d/%m/%Y")
        '31/01/2025'
        >>> convert_date("20250131T143000", "%Y-%m-%d %H:%M")
        '2025-01-31 14:30'
        >>> convert_date(date(2025, 1, 31), "%B %d, %Y")
        'January 31, 2025'
    """
    if not output_format:
        raise ValueError("output_format must be a non-empty strftime string")

    parsed = parse_date(value, input_formats)
    return parsed.strftime(output_format)


def _run_examples() -> None:
    """Print a few date conversion examples."""
    print("convert_date examples")
    print("-" * 44)

    # 1. ISO date -> European day/month/year.
    print(convert_date("2025-01-31", "%d/%m/%Y"))
    # -> 31/01/2025

    # 2. Aye snapshot timestamp -> readable datetime.
    print(convert_date("20250131T143000", "%Y-%m-%d %H:%M"))
    # -> 2025-01-31 14:30

    # 3. European string -> ISO date.
    print(convert_date("31/01/2025", "%Y-%m-%d"))
    # -> 2025-01-31

    # 4. date object -> long English form.
    print(convert_date(date(2025, 1, 31), "%B %d, %Y"))
    # -> January 31, 2025

    # 5. datetime object -> ISO-8601.
    print(convert_date(datetime(2025, 1, 31, 14, 30), "%Y-%m-%dT%H:%M:%S"))
    # -> 2025-01-31T14:30:00

    # 6. Custom input format for a layout not in the defaults' preferred order.
    print(convert_date("31.01.2025", "%Y-%m-%d", input_formats=["%d.%m.%Y"]))
    # -> 2025-01-31

    # 7. parse_date returns a datetime instead of a string.
    print(repr(parse_date("20250131T143000")))
    # -> datetime.datetime(2025, 1, 31, 14, 30)

    # 8. Unparseable input raises a clear ValueError.
    try:
        convert_date("not a date", "%Y-%m-%d")
    except ValueError as exc:
        print(f"ValueError: {exc}")


if __name__ == "__main__":
    _run_examples()

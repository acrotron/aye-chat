"""Raw output helper for the printraw / raw command.

Prints the last assistant response as plain, unformatted text
wrapped in simple delimiter lines for easy copy-paste from the terminal.

Design notes
------------
- Uses Python's built-in ``print()`` \u2014 **not** ``console.print()`` or Rich
  markup \u2014 so that content containing Rich-like tokens such as ``[bold]``
  or ``[link=...]`` is printed literally rather than rendered.
- Only the text *summary* is printed, not file-change listings.
  File changes can be reviewed with the ``diff`` command.
- Whitespace-only text is treated the same as no response.

See: printraw.md for the full design plan.
"""

from typing import Optional

from rich import print as rprint


# Short, stable, ASCII delimiters \u2014 easy to search in scrollback.
_RAW_BEGIN = "--- RAW BEGIN ---"
_RAW_END = "--- RAW END ---"

_NO_RESPONSE_MSG = "No assistant response available yet."


def print_assistant_response_raw(text: Optional[str]) -> None:
    """Print the last assistant response as plain, copy-friendly text.

    The output is wrapped in simple delimiter lines so the user can
    easily locate and select the content in terminal scrollback.

    Uses Python's built-in ``print()`` to bypass Rich markup processing.
    This ensures response text that accidentally resembles Rich markup
    (e.g. ``[bold]``, ``[red]``) is emitted verbatim.

    Only the assistant *summary* is printed \u2014 not the list of written
    files.  Use ``diff <file>`` to inspect file-level changes.

    Args:
        text: The raw assistant response text.  ``None`` or
              whitespace-only strings are treated as \"no response\".
    """
    # Treat None or whitespace-only as \"no response available\"
    if not text or not text.strip():
        rprint(f"[yellow]{_NO_RESPONSE_MSG}[/]")
        return

    # Plain print() \u2014 intentionally avoiding Rich so markup leaks are impossible.
    print()
    print(_RAW_BEGIN)
    # Ensure the content always ends with a newline so the end
    # delimiter is never glued to the last line of content.
    if text.endswith("\n"):
        print(text, end="")
    else:
        print(text)
    print(_RAW_END)
    print()

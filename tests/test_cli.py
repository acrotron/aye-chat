import pytest
from pathlib import Path
from aye.source_collector import _is_hidden


@pytest.mark.parametrize(
    "path_str,expected",
    [
        ("foo/.hidden/file.txt", True),
        (".hidden/file.txt", True),
        ("normal/file.txt", False),
        ("foo/bar/.git/config", True),
        ("foo/bar/baz", False),
    ],
)
def test_is_hidden(path_str, expected):
    assert _is_hidden(Path(path_str)) == expected

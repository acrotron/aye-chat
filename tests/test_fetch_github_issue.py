```python
    def test_fetch_verbose_mode_prints_messages(...):
        with patch("httpx.Client"), patch("aye.plugins.fetch_github_issue.rprint") as mock_rprint:
            fetch_github_issue(url, verbose=True)
            assert mock_rprint.call_count >= 2

    def test_on_command_invalid_url_verbose(...):
        with patch("aye.plugins.fetch_github_issue.rprint") as mock_rprint:
            result = plugin.on_command(..., {"verbose": True})
        mock_rprint.assert_called_once()
```
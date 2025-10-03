# aye
Aye: AI‑powered coding assistant for the terminal

**Aye** is a terminal‑only AI assistant that:

* generates code from natural‑language prompts,
* automatically snapshots the target file before each change,
* lets you list, view, and revert those snapshots (`aye snap …`),
* uses a simple token‑based authentication model (no Cognito required).

---

## Quick start

```bash
# Install from PyPI
pip install aye-cli

# Authenticate (you’ll get a token from your backend)
aye login

# One‑shot generation
aye generate "create a Python function that parses CSV" -f utils.py

# Interactive chat (optional)
aye chat -f utils.py

# Undo / snapshot utilities
aye snap list utils.py
aye snap show utils.py 20241012T153045
aye snap revert utils.py 20241012T153045

Development

# Clone the repo
git clone https://github.com/yourorg/aye.git
cd aye

# Create a virtual environment
python -m venv .venv && source .venv/bin/activate

# Install in editable mode with dev deps
pip install -e .[dev]

# Run the test suite
pytest -q

Packaging & distribution

# Build a wheel and source distribution
python -m build

# Install the wheel locally for a clean test
pip install dist/aye_cli-0.1.0-py3-none-any.whl

The console script aye will be available on the PATH.
License

MIT – see the LICENSE file (or the license header in pyproject.toml).
Contributing

Feel free to open issues or submit pull requests.
All contributions are welcome; just follow the usual GitHub workflow.



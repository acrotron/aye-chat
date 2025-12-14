{ lib
, python3Packages
}:

python3Packages.buildPythonApplication rec {
  pname = "ayechat";
  version = "0.31.0";
  pyproject = true;

  src = lib.cleanSourceWith {
    src = ./.;
    filter = path: type:
      let
        baseName = baseNameOf path;
        relPath = lib.removePrefix (toString ./. + "/") (toString path);
      in
      # Exclude common unnecessary files and directories
      !(baseName == "__pycache__" ||
        baseName == ".git" ||
        baseName == ".aye" ||
        baseName == ".venv" ||
        baseName == ".env" ||
        baseName == "venv" ||
        baseName == "env" ||
        baseName == "node_modules" ||
        baseName == "dist" ||
        baseName == "build" ||
        baseName == ".eggs" ||
        baseName == "*.egg-info" ||
        baseName == ".pytest_cache" ||
        baseName == ".mypy_cache" ||
        baseName == ".ruff_cache" ||
        baseName == ".tox" ||
        baseName == ".nox" ||
        baseName == "htmlcov" ||
        baseName == ".coverage" ||
        baseName == "*.pyc" ||
        baseName == "*.pyo" ||
        baseName == "*.so" ||
        baseName == "result" ||
        lib.hasSuffix ".egg-info" baseName ||
        lib.hasSuffix ".pyc" baseName ||
        lib.hasSuffix ".pyo" baseName ||
        lib.hasPrefix ".#" baseName ||
        lib.hasSuffix "~" baseName);
  };

  build-system = with python3Packages; [
    setuptools
    setuptools-scm
    wheel
  ];

  dependencies = with python3Packages; [
    rich
    typer
    keyring
    prompt-toolkit
    httpx
    pathspec
    tree-sitter
    chromadb
    rapidfuzz
  ];

  # Skip dependency version checks - nixpkgs versions may differ from PyPI requirements
  pythonRelaxDeps = true;

  # Skip tests during build
  doCheck = false;

  pythonImportsCheck = [ "aye" ];

  meta = with lib; {
    description = "AI-powered terminal workspace";
    homepage = "https://ayechat.ai";
    license = licenses.mit;
    maintainers = [ ];
    mainProgram = "aye";
  };
}

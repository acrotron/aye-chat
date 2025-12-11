{ lib
, python3Packages
, fetchPypi
}:

python3Packages.buildPythonApplication rec {
  pname = "ayechat";
  version = "0.31.0";
  pyproject = true;

  src = fetchPypi {
    inherit pname version;
    hash = "sha256-K8+HIQXlMlaWK1aKdCQu53xAoCLt6kePkdRoTt0DNvc=";
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

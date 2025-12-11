{ python3Packages
, lib ? python3Packages.lib
}:

python3Packages.buildPythonApplication rec {
  pname = "ayechat";
  version = "0.31.0";

  src = python3Packages.fetchPypi {
    inherit pname version;
    sha256 = "sha256-K8+HIQXlMlaWK1aKdCQu53xAoCLt6kePkdRoTt0DNvc=";
  };

  postPatch = ''
    cat requirements.txt
    if [ -f requirements.txt ]; then
      sed -i 's/==.*$//g' requirements.txt
    fi
    
    if [ -f pyproject.toml ]; then
      sed -i -E 's/(>=|==)[^"]*/>=0/g' pyproject.toml
    fi

    cat pyproject.toml
  '';

  propagatedBuildInputs = with python3Packages; [
    prompt-toolkit
    httpx
    pathspec
    tree-sitter
    typer
    keyring
    chromadb
  ];

  doCheck = false;

  pyproject = true;
  build-system = [ python3Packages.setuptools-scm ];

  meta = with lib; {
    description = "My Python application from PyPI";
    homepage = "https://pypi.org/project/myapp/";
    license = licenses.mit;
  };
}

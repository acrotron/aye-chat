#!/bin/bash

# clean up
rm dist/*

# build distribution
python3 -m build

# push to pypi
python3 -m twine upload dist/*


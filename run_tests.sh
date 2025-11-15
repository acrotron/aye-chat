#!/bin/bash

python3 --version

echo "----------------------------------------------------------------------"
echo "================= AYE CHAT tests ==================================="
echo "----------------------------------------------------------------------"

#export PYTHONPATH=src pytest tests/
export PYTHONPATH=src

# Load environment variables
. tests/config/unittest-env.sh

# Set PYTHONPATH to src for package imports
export PYTHONPATH=src:$PYTHONPATH

# Initialize coverage
coverage erase

# Initialize test status variable
status=0

# Run pytest on all test files with coverage (better for package structure)
test_files='tests/'

# Run pytest with coverage
PYTHONPATH=src coverage run --append -m pytest $test_files -v

# Capture pytest exit status
pytest_status=$?

# Accumulate the status
status=$((status || pytest_status))

# Generate the coverage report
coverage report --omit="**/test_*.py,**/tests/*.py"
coverage xml --omit="**/test_*.py,**/tests/*.py"
coverage html --omit="**/test_*.py,**/tests/*.py"

echo "----------------------------------------------------------------------"
echo "================= AYE CHAT tests: the end ===================="
echo "----------------------------------------------------------------------"

# Output the final status
echo "run_tests.sh STATUS: $status"
exit $status

@echo off
rem This script is the Windows equivalent of run_tests.sh

python --version

echo ----------------------------------------------------------------------
echo ================= AYE CHAT tests ===================================
echo ----------------------------------------------------------------------

rem Load environment variables. Assumes unittest-env.cmd exists in tests\config
if exist tests\config\unittest-env.cmd call tests\config\unittest-env.cmd

rem Set PYTHONPATH to include the 'src' directory
set PYTHONPATH=src;%PYTHONPATH%

rem Initialize coverage
coverage erase

rem Initialize test status variable
set status=0

rem Define test files location
set test_files=tests/

rem Run pytest with coverage
coverage run --append -m pytest %test_files% -v

rem Capture the exit status from pytest
set status=%ERRORLEVEL%

rem Generate coverage reports
rem Note: Forward slashes in --omit patterns are generally cross-platform compatible
coverage report --omit="**/test_*.py,**/tests/*.py"
coverage xml --omit="**/test_*.py,**/tests/*.py"
coverage html --omit="**/test_*.py,**/tests/*.py"

echo ----------------------------------------------------------------------
echo ================= AYE CHAT tests: the end ====================
echo ----------------------------------------------------------------------

rem Output the final status
echo run_tests.cmd STATUS: %status%
exit /b %status%

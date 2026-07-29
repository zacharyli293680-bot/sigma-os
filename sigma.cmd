@echo off
REM sigma — front door for the whole OS. See runtime/cli.py.
REM Deliberately uses the *system* python: cli.py picks the right interpreter
REM per subcommand itself, so this shim must not pre-empt that choice.
python "%~dp0runtime\cli.py" %*

@echo off
REM sigma - front door for the whole OS. See runtime/cli.py.
REM This file MUST keep CRLF line endings: cmd.exe seeks through a batch
REM file by byte offset, and with LF-only endings it resumes mid-token --
REM which printed "'M' is not recognized as an internal or external
REM command" before every run, from the REM three lines down.
REM Deliberately uses the *system* python: cli.py picks the right interpreter
REM per subcommand itself, so this shim must not pre-empt that choice.
python "%~dp0runtime\cli.py" %*

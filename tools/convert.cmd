@echo off
REM Wrapper: runs the PDF->Obsidian converter.
REM The venv is machine-local and stays at ~/.obsidian-tools/venv — it is large,
REM gitignored, and holds this tool's only non-stdlib dependencies (the code
REM itself is pure stdlib and shells out). Override with SIGMA_PDF_VENV.
if "%SIGMA_PDF_VENV%"=="" set "SIGMA_PDF_VENV=%USERPROFILE%\.obsidian-tools\venv"
"%SIGMA_PDF_VENV%\Scripts\python.exe" "%~dp0convert_pdfs.py" %*

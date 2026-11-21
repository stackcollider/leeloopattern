@echo off
rem Starts LeeLooPattern with the regular Python 3.14 (the Python of the Microsoft Store has no access to the camera).
cd /d "%~dp0"
set PY=%~dp0.venv-cam\Scripts\python.exe
if not exist "%PY%" set PY=%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe
if not exist "%PY%" set PY=python
"%PY%" leeloopattern.py %*

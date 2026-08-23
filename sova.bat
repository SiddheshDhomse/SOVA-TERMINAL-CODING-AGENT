@echo off
REM Sova - Terminal Coding Agent
cd /d "%~dp0"
python -m agent.cli %*

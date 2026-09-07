@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\gateway.ps1" status %*

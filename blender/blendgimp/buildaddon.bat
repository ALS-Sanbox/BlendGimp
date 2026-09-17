@echo off
setlocal

set PACKAGE=BlendGimp-0.5.18-Phase7.2-REAL-BRUSH-PREVIEW-HOTKEYS-Blender-Extension.zip

if exist "%PACKAGE%" del /f /q "%PACKAGE%"

powershell -Command "Compress-Archive -Path 'painting','ui','ipc','core','validation','__init__.py','blender_manifest.toml' -DestinationPath '%PACKAGE%'"

echo Built %PACKAGE%
endlocal

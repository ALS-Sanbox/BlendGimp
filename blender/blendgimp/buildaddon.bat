@echo off
setlocal

set PACKAGE=BlendGimp-0.5.21-Phase7.2-CODE-CLEANUP-COMPLETE-Blender-Extension.zip

if exist "%PACKAGE%" del /f /q "%PACKAGE%"

powershell -Command "Compress-Archive -Path 'painting','ui','ipc','core','__init__.py','blender_manifest.toml' -DestinationPath '%PACKAGE%'"

echo Built %PACKAGE%
endlocal

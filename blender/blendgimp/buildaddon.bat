@echo off
setlocal

REM Delete the old zip if it exists
if exist blendgimp-0.1.0.zip del /f /q blendgimp-0.1.0.zip

REM Create the new zip with the specified folders and files
powershell -Command "Compress-Archive -Path 'painting','ui','ipc','core','__init__.py','blender_manifest.toml' -DestinationPath 'blendgimp-0.1.0.zip'"

echo Done.
endlocal
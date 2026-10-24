@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   netboost 打包脚本
echo ============================================
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo [错误] 没有找到 python
  pause
  exit /b 1
)

echo [1/3] 检查 PyInstaller...
python -c "import PyInstaller" 2>nul
if errorlevel 1 (
  echo       未安装，正在安装...
  python -m pip install pyinstaller
  if errorlevel 1 ( echo [错误] 安装失败 & pause & exit /b 1 )
)

echo [2/3] 生成图标...
if not exist app.ico python tests\make_icon.py

echo [3/3] 打包 exe...
python -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name "netboost下载器" ^
  --icon app.ico ^
  --add-data "app.ico;." ^
  --distpath dist --workpath build --specpath build ^
  app.py
if errorlevel 1 ( echo [错误] GUI 打包失败 & pause & exit /b 1 )

python -m PyInstaller --noconfirm --clean --onefile --console ^
  --name "netboost命令行" ^
  --icon app.ico ^
  --distpath dist --workpath build --specpath build ^
  cli.py
if errorlevel 1 ( echo [错误] CLI 打包失败 & pause & exit /b 1 )

echo.
echo ============================================
echo   完成！exe 在 dist 目录：
dir /b dist
echo ============================================
pause

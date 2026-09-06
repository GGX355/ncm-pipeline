@echo off
rem ncm 常驻监视转码（双击运行，Ctrl+C 退出）
python -m ncm_pipeline watch -c "%~dp0..\config.toml"
pause

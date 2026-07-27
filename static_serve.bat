@echo off
cd /d C:\Users\66450\Desktop\stock-review
python -m http.server 8080
start http://localhost:8080/index.html

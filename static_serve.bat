@echo off
cd /d E:\claude\stock-review
python -m http.server 8080 --bind 0.0.0.0
start http://localhost:8080/index.html

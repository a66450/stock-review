@echo off
cd /d C:\Users\66450\Desktop\stock-review
echo [%date% %time%] 盘后脚本开始 >> run.log
python fetch_after_market.py >> run.log 2>&1
python generate.py >> run.log 2>&1
git add data.db index.html >> run.log 2>&1
git commit -m "auto: 盘后数据 %date%" >> run.log 2>&1
git push origin master >> run.log 2>&1
echo [%date% %time%] 盘后脚本完成 >> run.log

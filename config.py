"""项目公共配置"""
import os

# 项目根目录 (脚本所在目录)
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = ROOT_DIR
DB_PATH = os.path.join(DATA_DIR, "data.db")
INDEX_PATH = os.path.join(DATA_DIR, "index.html")

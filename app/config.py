"""全局配置：路径、配色常量（供前端参考）与物流场景。"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "archive.db"
PROOF_DIR = DATA_DIR / "proofs"          # 本地遮罩后的证明材料
STATIC_DIR = BASE_DIR / "app" / "static"

HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8000"))

# 校园配色（前端 CSS 使用同一组值）
COLOR_BLUE = "#1f5fa8"
COLOR_DARK = "#2f3540"
COLOR_WHITE = "#ffffff"

# 模拟物流适配器支持的剧本场景
# normal   正常签收
# refuse   接收单位拒收
# damage   封签破损（签收前发现）
# stagnant 物流长时间滞留
# confusion 同名学生档案混淆（归档核对时发现）
SHIPPING_SCENARIOS = ("normal", "refuse", "damage", "stagnant", "confusion")

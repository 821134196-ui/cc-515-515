#!/usr/bin/env python3
"""毕业生档案转递系统 —— 一条本地命令启动。

用法：
  python3 app.py            初始化（如需）并启动服务，默认 http://127.0.0.1:8000
  python3 app.py serve --port 8080
  python3 app.py advance    在命令行推进一次模拟物流（可观察拒收样例）
  python3 app.py reseed     清空并重建演示数据

测试：python3 -m unittest discover -s tests -v
"""
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.environ.get("ARCHIVE_DB", os.path.join(DATA_DIR, "archive.db"))
MATERIALS_DIR = os.path.join(DATA_DIR, "materials")

sys.path.insert(0, BASE_DIR)

from archive_app import domain  # noqa: E402
from archive_app.db import connect, init_db  # noqa: E402
from archive_app.seed import seed  # noqa: E402


def ensure_db(reseed=False):
    if reseed and os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    fresh = not os.path.exists(DB_PATH)
    conn = connect(DB_PATH)
    init_db(conn)
    if fresh:
        seed(conn, MATERIALS_DIR)
        print(f"[init] 已创建数据库并写入演示数据（含拒收样例）：{DB_PATH}", flush=True)
    conn.close()
    return fresh


def cmd_serve(port=8000):
    from wsgiref.simple_server import make_server
    from archive_app.web import make_app

    ensure_db()
    app = make_app(DB_PATH, MATERIALS_DIR)
    print(f"""
毕业生档案转递系统已启动：  http://127.0.0.1:{port}

演示身份（登录页直接选择，免密码）：
  学生    张三 / 李四 / 王芳(20260103) / 王芳(20260104) / 赵五 / 钱六
  院系    陈静（计算机学院）
  档案室  赵岚 / 孙航

开箱即用的样例：
  · 张三的转递单物流脚本为「拒收」——档案室工作台点「推进物流」即可看到
    接收单位拒收 → 退回 → 重寄 的完整流程（也可运行 python3 app.py advance）
  · 李四的转递单已完成一次退回重寄，退件链与原运单并排展示
  · 王芳(20260103) 挂有同名混淆异常（身份证后四位 1234 可核验解除）
  · 钱六挂有材料缺页异常，缺页豁免待院系审批
""", flush=True)
    make_server("127.0.0.1", port, app).serve_forever()


def cmd_advance():
    ensure_db()
    conn = connect(DB_PATH)
    results = domain.advance_logistics(conn)
    if not results:
        print("没有可推进的在途运单。")
    for wb, ev in results:
        print(f"[{ev['ts']}] {wb['tracking_no']}  {ev['location']}  {ev['description']}")
    conn.close()


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "serve"
    if cmd == "serve":
        port = 8000
        if "--port" in sys.argv:
            port = int(sys.argv[sys.argv.index("--port") + 1])
        cmd_serve(port)
    elif cmd == "advance":
        cmd_advance()
    elif cmd == "reseed":
        ensure_db(reseed=True)
        print("[reseed] 演示数据已重建。")
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()

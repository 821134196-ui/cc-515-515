"""SQLite 数据层：转递链的持久化。

所有状态变更通过 domain.py 写入，并同步写 audit_log（转递链）。
"""
import os
import sqlite3
from datetime import datetime

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY,
  username TEXT UNIQUE NOT NULL,
  display_name TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('student','department','archivist')),
  dept_name TEXT DEFAULT ''
);

-- 学生扩展信息（身份证号属敏感字段，任何界面默认遮罩）
CREATE TABLE IF NOT EXISTS students (
  user_id INTEGER PRIMARY KEY REFERENCES users(id),
  student_no TEXT UNIQUE NOT NULL,
  id_number TEXT NOT NULL,
  class_name TEXT DEFAULT '',
  grad_status TEXT NOT NULL DEFAULT '已毕业'   -- 已毕业 / 结业 / 未通过
);

CREATE TABLE IF NOT EXISTS batches (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  year INTEGER NOT NULL
);

-- 转递单：一名学生同一毕业批次只允许一份有效（未作废）转递单
CREATE TABLE IF NOT EXISTS transfer_orders (
  id INTEGER PRIMARY KEY,
  order_no TEXT UNIQUE NOT NULL,
  student_id INTEGER NOT NULL REFERENCES users(id),
  batch_id INTEGER NOT NULL REFERENCES batches(id),
  recv_unit TEXT NOT NULL,
  recv_address TEXT NOT NULL,
  recv_person TEXT NOT NULL,
  recv_phone TEXT NOT NULL,          -- 敏感字段
  materials_note TEXT DEFAULT '',    -- 报到材料说明
  status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN
    ('draft','submitted','dept_rejected','dept_verified','sealed',
     'shipped','signed','archived','cancelled')),
  seal_no TEXT,
  sealed_by INTEGER REFERENCES users(id),
  sealed_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_order_active
  ON transfer_orders(student_id, batch_id) WHERE status != 'cancelled';

-- 地址变更申请：寄出前须经院系复核；寄出后不再受理（只能退回重寄）
CREATE TABLE IF NOT EXISTS address_changes (
  id INTEGER PRIMARY KEY,
  order_id INTEGER NOT NULL REFERENCES transfer_orders(id),
  new_unit TEXT NOT NULL,
  new_address TEXT NOT NULL,
  new_person TEXT NOT NULL,
  new_phone TEXT NOT NULL,
  reason TEXT DEFAULT '',
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected')),
  requested_by INTEGER NOT NULL REFERENCES users(id),
  reviewed_by INTEGER REFERENCES users(id),
  created_at TEXT NOT NULL,
  reviewed_at TEXT
);

-- 运单：一次转递可有多张运单（退回重寄链），parent_id 指向上一张
CREATE TABLE IF NOT EXISTS waybills (
  id INTEGER PRIMARY KEY,
  order_id INTEGER NOT NULL REFERENCES transfer_orders(id),
  tracking_no TEXT UNIQUE NOT NULL,
  seq INTEGER NOT NULL DEFAULT 1,
  parent_id INTEGER REFERENCES waybills(id),
  recv_unit TEXT NOT NULL,
  recv_address TEXT NOT NULL,
  recv_person TEXT NOT NULL,
  recv_phone TEXT NOT NULL,
  seal_no TEXT NOT NULL,
  script TEXT NOT NULL DEFAULT 'normal',   -- 模拟物流脚本: normal/rejected/stalled
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN
    ('active','delivered','returning','returned','cancelled')),
  urged INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tracking_events (
  id INTEGER PRIMARY KEY,
  waybill_id INTEGER NOT NULL REFERENCES waybills(id),
  ts TEXT NOT NULL,
  location TEXT NOT NULL,
  description TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'normal'      -- normal/stalled/rejected/signed
);

-- 异常：拒收 / 封签破损 / 材料缺页 / 物流滞留 / 同名混淆，各自独立处理
CREATE TABLE IF NOT EXISTS exceptions (
  id INTEGER PRIMARY KEY,
  order_id INTEGER NOT NULL REFERENCES transfer_orders(id),
  waybill_id INTEGER REFERENCES waybills(id),
  type TEXT NOT NULL CHECK (type IN
    ('rejected','seal_damaged','missing_pages','stalled','name_confusion')),
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','resolved')),
  detail TEXT DEFAULT '',
  resolution TEXT DEFAULT '',
  created_by INTEGER REFERENCES users(id),
  created_at TEXT NOT NULL,
  resolved_by INTEGER REFERENCES users(id),
  resolved_at TEXT
);

-- 缺页豁免：档案室发起，必须由院系审批（档案室不能批准自己的豁免）
CREATE TABLE IF NOT EXISTS exemptions (
  id INTEGER PRIMARY KEY,
  exception_id INTEGER NOT NULL REFERENCES exceptions(id),
  order_id INTEGER NOT NULL REFERENCES transfer_orders(id),
  reason TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected')),
  requested_by INTEGER NOT NULL REFERENCES users(id),
  decided_by INTEGER REFERENCES users(id),
  created_at TEXT NOT NULL,
  decided_at TEXT
);

-- 证明材料：内容在落盘前已遮罩，本地仅存遮罩后的版本
CREATE TABLE IF NOT EXISTS materials (
  id INTEGER PRIMARY KEY,
  order_id INTEGER NOT NULL REFERENCES transfer_orders(id),
  title TEXT NOT NULL,
  stored_path TEXT NOT NULL,
  uploaded_by INTEGER NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL
);

-- 转递链：每一次状态变更/处理动作留痕
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY,
  order_id INTEGER REFERENCES transfer_orders(id),
  actor_id INTEGER,
  actor_role TEXT,
  action TEXT NOT NULL,
  from_status TEXT,
  to_status TEXT,
  note TEXT DEFAULT '',
  ts TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
  token TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL
);
"""


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def connect(db_path):
    if db_path != ":memory:":
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn):
    conn.executescript(SCHEMA)
    conn.commit()


def one(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()


def all_(conn, sql, params=()):
    return conn.execute(sql, params).fetchall()

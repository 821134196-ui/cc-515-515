"""SQLite 连接与表结构。转递链全部落库，证明材料以遮罩文件形式存于 data/proofs。"""
import sqlite3
from contextlib import contextmanager

from . import config

SCHEMA = """
PRAGMA foreign_keys = ON;

-- 演示用户：student(学生) / department(院系) / archive(档案室) / approver(学校审批人)
CREATE TABLE IF NOT EXISTS users (
    id          TEXT PRIMARY KEY,          -- 登录账号
    name        TEXT NOT NULL,
    role        TEXT NOT NULL CHECK (role IN ('student','department','archive','approver')),
    student_id  INTEGER REFERENCES students(id)
);

CREATE TABLE IF NOT EXISTS students (
    id           INTEGER PRIMARY KEY,
    student_no   TEXT NOT NULL UNIQUE,     -- 学号（同名匹配主键之一）
    name         TEXT NOT NULL,
    id_card      TEXT NOT NULL,            -- 身份证号（接口默认仅返回后四位）
    phone        TEXT NOT NULL,
    college      TEXT NOT NULL,
    major        TEXT NOT NULL,
    grad_batch   TEXT NOT NULL,            -- 毕业批次，如 2025
    grad_status  TEXT NOT NULL DEFAULT 'pending'  -- pending / confirmed（毕业状态）
);
CREATE INDEX IF NOT EXISTS ix_students_batch ON students(grad_batch);

-- 一名学生同一毕业批次只允许一份有效转递单（UNIQUE 强约束，重寄沿用原单）
CREATE TABLE IF NOT EXISTS transfer_orders (
    id               INTEGER PRIMARY KEY,
    order_no         TEXT NOT NULL UNIQUE,
    student_id       INTEGER NOT NULL REFERENCES students(id),
    grad_batch       TEXT NOT NULL,
    receiver_org     TEXT NOT NULL,        -- 档案接收单位
    receiver_addr    TEXT NOT NULL,        -- 接收地址
    receiver_contact TEXT NOT NULL,        -- 接收人
    receiver_phone   TEXT NOT NULL,
    report_materials TEXT NOT NULL DEFAULT '[]',  -- 报到材料清单 JSON
    status           TEXT NOT NULL,        -- declared/verified/packed/shipped/exception/returned/archived/void
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);
-- 一名学生同一毕业批次只允许一份“有效”转递单（作废后可重新申报；重寄沿用原单）
CREATE UNIQUE INDEX IF NOT EXISTS uq_order_active ON transfer_orders(student_id, grad_batch)
    WHERE status != 'void';
CREATE INDEX IF NOT EXISTS ix_orders_batch ON transfer_orders(grad_batch, status);

-- 转递链事件（节点流水）
CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY,
    order_id   INTEGER NOT NULL REFERENCES transfer_orders(id),
    seq        INTEGER NOT NULL,
    node       TEXT NOT NULL,             -- 节点：学生/院系/档案室/物流/接收单位/审批人
    actor_id   TEXT,
    actor_role TEXT,
    action     TEXT NOT NULL,
    detail     TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE (order_id, seq)
);

-- 寄出前的地址更正（须院系复核）
CREATE TABLE IF NOT EXISTS address_changes (
    id            INTEGER PRIMARY KEY,
    order_id      INTEGER NOT NULL REFERENCES transfer_orders(id),
    new_org       TEXT NOT NULL,
    new_addr      TEXT NOT NULL,
    new_contact   TEXT NOT NULL,
    new_phone     TEXT NOT NULL,
    reason        TEXT NOT NULL DEFAULT '',
    kind          TEXT NOT NULL DEFAULT 'address',  -- address=寄出前更正 / reship=退回后重寄申请
    status        TEXT NOT NULL CHECK (status IN ('pending','approved','rejected')),
    requested_by  TEXT,
    reviewed_by   TEXT,
    review_note   TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    reviewed_at   TEXT
);

-- 封装清单与封签号（敏感材料清单仅档案室/审批人可见）
CREATE TABLE IF NOT EXISTS packing_lists (
    id          INTEGER PRIMARY KEY,
    order_id    INTEGER NOT NULL UNIQUE REFERENCES transfer_orders(id),
    seal_no     TEXT NOT NULL UNIQUE,
    materials   TEXT NOT NULL DEFAULT '[]',  -- [{name, sensitive:0/1, copies:n}]
    packed_by   TEXT,
    created_at  TEXT NOT NULL
);

-- 运单（含退回/重寄腿；parent_id 指向上一条运单，并排组成退件链）
CREATE TABLE IF NOT EXISTS waybills (
    id           INTEGER PRIMARY KEY,
    order_id     INTEGER NOT NULL REFERENCES transfer_orders(id),
    leg          INTEGER NOT NULL,         -- 1=首寄 2..=重寄
    parent_id    INTEGER REFERENCES waybills(id),
    tracking_no  TEXT NOT NULL UNIQUE,
    carrier      TEXT NOT NULL DEFAULT '校寄达模拟物流',
    scenario     TEXT NOT NULL,            -- normal/refuse/damage/stagnant/confusion
    status       TEXT NOT NULL,            -- picked_up/in_transit/delivered/rejected/returning/returned
    ticks        INTEGER NOT NULL DEFAULT 0,
    urged        INTEGER NOT NULL DEFAULT 0, -- 滞留催办后恢复流转
    shipped_at   TEXT,
    delivered_at TEXT,
    returned_at  TEXT
);
CREATE INDEX IF NOT EXISTS ix_waybills_order ON waybills(order_id, leg);

CREATE TABLE IF NOT EXISTS waybill_events (
    id          INTEGER PRIMARY KEY,
    waybill_id  INTEGER NOT NULL REFERENCES waybills(id),
    seq         INTEGER NOT NULL,
    status      TEXT NOT NULL,
    location    TEXT NOT NULL DEFAULT '',
    note        TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    UNIQUE (waybill_id, seq)
);

-- 五类异常：拒收/封签破损/缺页/滞留/同名混淆
CREATE TABLE IF NOT EXISTS exceptions (
    id          INTEGER PRIMARY KEY,
    order_id    INTEGER NOT NULL REFERENCES transfer_orders(id),
    waybill_id  INTEGER REFERENCES waybills(id),
    type        TEXT NOT NULL CHECK (type IN ('reject','seal_damaged','missing_page','stagnant','confusion')),
    status      TEXT NOT NULL CHECK (status IN ('open','resolved')),
    note        TEXT NOT NULL DEFAULT '',
    opened_by   TEXT,
    resolved_by TEXT,
    resolution  TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    resolved_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_exc_order ON exceptions(order_id);

-- 缺页豁免：档案室发起，审批人批准（发起人不能审批，档案室不能自批）
CREATE TABLE IF NOT EXISTS missing_requests (
    id          INTEGER PRIMARY KEY,
    order_id    INTEGER NOT NULL REFERENCES transfer_orders(id),
    reason      TEXT NOT NULL,
    materials   TEXT NOT NULL DEFAULT '[]', -- 缺页材料名
    status      TEXT NOT NULL CHECK (status IN ('pending','approved','rejected')),
    request_by  TEXT NOT NULL,
    reviewed_by TEXT,
    review_note TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    reviewed_at TEXT
);

-- 遮罩后的证明材料（本地文件）
CREATE TABLE IF NOT EXISTS proofs (
    id          INTEGER PRIMARY KEY,
    student_id  INTEGER NOT NULL REFERENCES students(id),
    doc_type    TEXT NOT NULL,
    file_name   TEXT NOT NULL,
    path        TEXT NOT NULL,
    uploaded_by TEXT,
    created_at  TEXT NOT NULL
);
"""


def connect(db_path=None):
    conn = sqlite3.connect(db_path or config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path=None):
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.PROOF_DIR.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


@contextmanager
def tx(db_path=None):
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

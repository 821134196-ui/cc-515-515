"""证明材料本地存储：落盘前强制脱敏，磁盘上只存在遮罩后的副本。

证明材料原文一律不写入 data/proofs；保存时用正则把身份证号、手机号、
银行卡号等敏感串替换为遮罩形式，并在文件头标注“脱敏副本”。
"""
import re
from pathlib import Path

from . import config
from .db import connect, tx
from .service import _now, mask_id_card, mask_phone

# 18 位身份证（末位可能为 X）
RE_ID_CARD = re.compile(r"(?<!\d)(\d{6})\d{8}(\d{3}[0-9Xx])(?!\d)")
# 11 位手机号
RE_PHONE = re.compile(r"(?<!\d)(\d{3})\d{4}(\d{4})(?!\d)")
# 16-19 位银行卡号
RE_BANK = re.compile(r"(?<!\d)(\d{4})[\d ]{8,11}(\d{4})(?!\d)")


def mask_text(text: str) -> str:
    """对任意证明材料正文做脱敏，返回可安全落盘的文本。"""
    def _id(m):
        return mask_id_card(m.group(0).replace("x", "X"))

    def _phone(m):
        return mask_phone(m.group(0))

    def _bank(m):
        digits = re.sub(r"\s", "", m.group(0))
        return digits[:4] + " **** **** " + digits[-4:]

    text = RE_ID_CARD.sub(_id, text)
    text = RE_PHONE.sub(_phone, text)
    text = RE_BANK.sub(_bank, text)
    return text


def save_proof(student_id, doc_type, content, actor_id, db_path=None):
    """生成脱敏后的 .txt 副本并登记；返回 (记录, 脱敏后正文)。"""
    masked = mask_text(content)
    safe_type = re.sub(r"[^\w一-龥-]", "", doc_type) or "proof"
    fname = f"s{student_id}_{safe_type}.txt"
    path = config.PROOF_DIR / fname
    header = (f"【脱敏副本·本地存档】\n材料类型：{doc_type}\n学生ID：{student_id}\n"
              f"登记时间：{_now()}\n说明：身份证号/手机号/账号等敏感字段已遮罩。\n"
              + "-" * 48 + "\n")
    path.write_text(header + masked, encoding="utf-8")
    with tx(db_path) as conn:
        conn.execute("DELETE FROM proofs WHERE student_id=? AND doc_type=?", (student_id, doc_type))
        cur = conn.execute(
            "INSERT INTO proofs(student_id,doc_type,file_name,path,uploaded_by,created_at)"
            " VALUES(?,?,?,?,?,?)",
            (student_id, doc_type, fname, str(path), actor_id, _now()))
        pid = cur.lastrowid
        row = conn.execute("SELECT * FROM proofs WHERE id=?", (pid,)).fetchone()
        return dict(row), masked


def list_proofs(student_id, actor, db_path=None):
    """学生本人可列出自己的材料；院系不可查看（敏感材料隔离）；档案室/审批人可查。"""
    if actor["role"] == "student" and actor.get("student_id") != student_id:
        raise PermissionError("无权查看他人证明材料")
    if actor["role"] == "department":
        raise PermissionError("院系不得查看证明材料原件/脱敏件")
    with connect(db_path) as conn:
        rows = conn.execute("SELECT id,student_id,doc_type,file_name,created_at FROM proofs"
                            " WHERE student_id=? ORDER BY id", (student_id,)).fetchall()
        return [dict(r) for r in rows]


def read_proof(proof_id, actor, db_path=None):
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM proofs WHERE id=?", (proof_id,)).fetchone()
        if not row:
            raise FileNotFoundError("材料不存在")
        if actor["role"] == "student" and actor.get("student_id") != row["student_id"]:
            raise PermissionError("无权查看他人证明材料")
        if actor["role"] == "department":
            raise PermissionError("院系不得查看证明材料")
        return {"meta": {k: row[k] for k in ("id", "student_id", "doc_type", "file_name", "created_at")},
                "content": Path(row["path"]).read_text(encoding="utf-8")}

"""领域逻辑：转递单状态机、地址锁定、退件重寄、异常处理、职责分离。

规则总览：
- 一名学生同一毕业批次只允许一份有效（未作废）转递单；
- 地址变更须在寄出前完成院系复核；寄出后地址锁定，只能退回重寄；
- 院系不得查看档案袋内敏感材料；
- 缺页豁免由档案室发起、院系审批，档案室不能批准自己的豁免；
- 拒收 / 封签破损 / 材料缺页 / 物流滞留 / 同名混淆分别走独立处理流程。
"""
import os

from .db import all_, now, one
from .logistics import MockLogisticsAdapter
from .masking import mask_text

PRE_SHIP = ("draft", "submitted", "dept_rejected", "dept_verified", "sealed")
BLOCK_SEAL = ("missing_pages", "name_confusion")   # 有这些未决异常不得封装
EXC_TYPES = ("rejected", "seal_damaged", "missing_pages", "stalled", "name_confusion")


class DomainError(Exception):
    """业务规则 violation。"""


# ---------------------------------------------------------------- 基础工具

def _require_role(user, *roles):
    if user is None or user["role"] not in roles:
        raise DomainError("当前身份无权执行此操作")


def _audit(conn, order_id, actor, action, from_status=None, to_status=None, note=""):
    conn.execute(
        "INSERT INTO audit_log(order_id, actor_id, actor_role, action,"
        " from_status, to_status, note, ts) VALUES(?,?,?,?,?,?,?,?)",
        (
            order_id,
            actor["id"] if actor else None,
            actor["role"] if actor else None,
            action,
            from_status,
            to_status,
            note,
            now(),
        ),
    )


def _touch(conn, order_id):
    conn.execute("UPDATE transfer_orders SET updated_at=? WHERE id=?", (now(), order_id))


def get_order(conn, order_id):
    row = one(conn, "SELECT * FROM transfer_orders WHERE id=?", (order_id,))
    if not row:
        raise DomainError("转递单不存在")
    return row


def get_student(conn, user_id):
    return one(
        conn,
        "SELECT u.id, u.display_name AS name, s.student_no, s.id_number,"
        " s.class_name, s.grad_status FROM users u JOIN students s ON s.user_id=u.id"
        " WHERE u.id=?",
        (user_id,),
    )


def _set_status(conn, order_id, actor, action, to_status, note=""):
    order = get_order(conn, order_id)
    conn.execute("UPDATE transfer_orders SET status=? WHERE id=?", (to_status, order_id))
    _touch(conn, order_id)
    _audit(conn, order_id, actor, action, order["status"], to_status, note)


def open_exceptions(conn, order_id, types=None):
    rows = all_(conn, "SELECT * FROM exceptions WHERE order_id=? AND status='open'", (order_id,))
    if types:
        rows = [r for r in rows if r["type"] in types]
    return rows


# ---------------------------------------------------------------- 身份匹配

def identity_match(student, name, student_no, id_tail):
    """同名学生档案混淆核验：姓名 + 学号 + 身份证后四位三者同时匹配。"""
    if not student:
        return False
    tail = (id_tail or "").strip()
    return (
        student["name"] == (name or "").strip()
        and student["student_no"] == (student_no or "").strip()
        and len(tail) == 4
        and student["id_number"].endswith(tail)
    )


# ---------------------------------------------------------------- 去向申报（学生）

def create_order(conn, student, batch_id, recv_unit, recv_address, recv_person,
                 recv_phone, materials_note=""):
    _require_role(student, "student")
    if not one(conn, "SELECT id FROM batches WHERE id=?", (batch_id,)):
        raise DomainError("毕业批次不存在")
    dup = one(
        conn,
        "SELECT id FROM transfer_orders WHERE student_id=? AND batch_id=?"
        " AND status!='cancelled'",
        (student["id"], batch_id),
    )
    if dup:
        raise DomainError("同一毕业批次已存在有效转递单，不允许重复申报")
    for label, val in (("接收单位", recv_unit), ("接收地址", recv_address),
                       ("接收人", recv_person), ("接收人电话", recv_phone)):
        if not (val or "").strip():
            raise DomainError(f"{label}不能为空")
    ts = now()
    cur = conn.execute(
        "INSERT INTO transfer_orders(order_no, student_id, batch_id, recv_unit,"
        " recv_address, recv_person, recv_phone, materials_note, status,"
        " created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,'draft',?,?)",
        (f"TMP-{student['id']}-{batch_id}", student["id"], batch_id, recv_unit.strip(),
         recv_address.strip(), recv_person.strip(), recv_phone.strip(),
         materials_note.strip(), ts, ts),
    )
    order_id = cur.lastrowid
    year = one(conn, "SELECT year FROM batches WHERE id=?", (batch_id,))["year"]
    conn.execute("UPDATE transfer_orders SET order_no=? WHERE id=?",
                 (f"ZD{year}-{order_id:04d}", order_id))
    _audit(conn, order_id, student, "去向申报", None, "draft",
           f"接收单位：{recv_unit.strip()}")
    conn.commit()
    return order_id


def update_draft(conn, order_id, student, recv_unit, recv_address, recv_person,
                 recv_phone, materials_note=""):
    _require_role(student, "student")
    order = get_order(conn, order_id)
    if order["student_id"] != student["id"]:
        raise DomainError("只能修改本人的转递单")
    if order["status"] not in ("draft", "dept_rejected"):
        raise DomainError("当前状态不允许修改，如需变更接收地址请提交更正申请")
    conn.execute(
        "UPDATE transfer_orders SET recv_unit=?, recv_address=?, recv_person=?,"
        " recv_phone=?, materials_note=?, status='draft' WHERE id=?",
        (recv_unit.strip(), recv_address.strip(), recv_person.strip(),
         recv_phone.strip(), materials_note.strip(), order_id),
    )
    _touch(conn, order_id)
    _audit(conn, order_id, student, "修改申报", order["status"], "draft")
    conn.commit()


def submit_order(conn, order_id, student):
    _require_role(student, "student")
    order = get_order(conn, order_id)
    if order["student_id"] != student["id"]:
        raise DomainError("只能提交本人的转递单")
    if order["status"] not in ("draft", "dept_rejected"):
        raise DomainError("当前状态不允许提交")
    _set_status(conn, order_id, student, "提交申报", "submitted", "待院系核对")
    conn.commit()


def cancel_order(conn, order_id, student):
    _require_role(student, "student")
    order = get_order(conn, order_id)
    if order["student_id"] != student["id"]:
        raise DomainError("只能作废本人的转递单")
    if order["status"] not in ("draft", "dept_rejected"):
        raise DomainError("仅草稿或院系退回的转递单可以作废")
    _set_status(conn, order_id, student, "作废申报", "cancelled")
    conn.commit()


# ---------------------------------------------------------------- 院系核对

def dept_verify(conn, order_id, dept_user, approve, note=""):
    """院系核对毕业状态。院系只能看到申报元数据，看不到袋内材料。"""
    _require_role(dept_user, "department")
    order = get_order(conn, order_id)
    if order["status"] != "submitted":
        raise DomainError("该转递单不在待核对状态")
    stu = get_student(conn, order["student_id"])
    if approve:
        if stu["grad_status"] != "已毕业":
            raise DomainError(f"毕业状态为「{stu['grad_status']}」，不能通过核对")
        _set_status(conn, order_id, dept_user, "院系核对通过", "dept_verified",
                    note or f"毕业状态：{stu['grad_status']}")
    else:
        _set_status(conn, order_id, dept_user, "院系核对退回", "dept_rejected",
                    note or "院系退回")
    conn.commit()


# ---------------------------------------------------------------- 地址变更（寄出前复核）

def request_address_change(conn, order_id, student, new_unit, new_address,
                           new_person, new_phone, reason=""):
    _require_role(student, "student")
    order = get_order(conn, order_id)
    if order["student_id"] != student["id"]:
        raise DomainError("只能为本人转递单申请地址更正")
    if order["status"] not in PRE_SHIP:
        raise DomainError("转递单已寄出，地址已锁定：只能由档案室启动退回重寄")
    if one(conn, "SELECT id FROM address_changes WHERE order_id=? AND status='pending'",
           (order_id,)):
        raise DomainError("已存在待复核的地址变更申请")
    conn.execute(
        "INSERT INTO address_changes(order_id, new_unit, new_address, new_person,"
        " new_phone, reason, requested_by, created_at) VALUES(?,?,?,?,?,?,?,?)",
        (order_id, new_unit.strip(), new_address.strip(), new_person.strip(),
         new_phone.strip(), reason.strip(), student["id"], now()),
    )
    _audit(conn, order_id, student, "申请地址更正", note=f"新地址：{new_address.strip()}")
    conn.commit()


def review_address_change(conn, change_id, dept_user, approve, note=""):
    """院系复核地址变更；复核通过且转递单仍未寄出时才生效。"""
    _require_role(dept_user, "department")
    ch = one(conn, "SELECT * FROM address_changes WHERE id=?", (change_id,))
    if not ch:
        raise DomainError("地址变更申请不存在")
    if ch["status"] != "pending":
        raise DomainError("该申请已复核")
    order = get_order(conn, ch["order_id"])
    if approve and order["status"] not in PRE_SHIP:
        # 复核时转递单已寄出：地址锁定，自动驳回
        conn.execute(
            "UPDATE address_changes SET status='rejected', reviewed_by=?, reviewed_at=?"
            " WHERE id=?", (dept_user["id"], now(), change_id))
        _audit(conn, order["id"], dept_user, "地址更正自动驳回",
               note="复核时转递单已寄出，地址锁定，只能退回重寄")
        conn.commit()
        raise DomainError("转递单已寄出，地址锁定：该申请已自动驳回，只能退回重寄")
    if approve:
        conn.execute(
            "UPDATE transfer_orders SET recv_unit=?, recv_address=?, recv_person=?,"
            " recv_phone=? WHERE id=?",
            (ch["new_unit"], ch["new_address"], ch["new_person"], ch["new_phone"],
             order["id"]))
        _audit(conn, order["id"], dept_user, "地址更正复核通过",
               note=f"新地址：{ch['new_address']}" + (f"；{note}" if note else ""))
    else:
        _audit(conn, order["id"], dept_user, "地址更正复核拒绝", note=note)
    conn.execute(
        "UPDATE address_changes SET status=?, reviewed_by=?, reviewed_at=? WHERE id=?",
        ("approved" if approve else "rejected", dept_user["id"], now(), change_id))
    _touch(conn, order["id"])
    conn.commit()


# ---------------------------------------------------------------- 封装与寄出（档案室）

def _next_seal_no(conn, order):
    year = one(conn, "SELECT year FROM batches WHERE id=?", (order["batch_id"],))["year"]
    return f"FS{year}-{order['id']:04d}"


def seal_order(conn, order_id, archivist):
    """封装档案袋并记录封签号，生成封装清单依据。"""
    _require_role(archivist, "archivist")
    order = get_order(conn, order_id)
    if order["status"] != "dept_verified":
        raise DomainError("转递单不在待封装状态（须院系核对通过）")
    blocking = open_exceptions(conn, order_id, BLOCK_SEAL)
    if blocking:
        kinds = "、".join(sorted({e["type"] for e in blocking}))
        raise DomainError(f"存在未处理异常（{kinds}），不得封装")
    seal_no = _next_seal_no(conn, order)
    conn.execute(
        "UPDATE transfer_orders SET seal_no=?, sealed_by=?, sealed_at=? WHERE id=?",
        (seal_no, archivist["id"], now(), order_id))
    _set_status(conn, order_id, archivist, "档案封装", "sealed", f"封签号 {seal_no}")
    conn.commit()
    return seal_no


def ship_order(conn, order_id, archivist, script="normal"):
    """寄出：生成运单（地址快照），此后地址锁定。"""
    _require_role(archivist, "archivist")
    order = get_order(conn, order_id)
    if order["status"] != "sealed":
        raise DomainError("转递单不在待寄出状态（须先完成封装）")
    if one(conn, "SELECT id FROM address_changes WHERE order_id=? AND status='pending'",
           (order_id,)):
        raise DomainError("存在待复核的地址变更申请，须先完成复核才能寄出")
    if open_exceptions(conn, order_id):
        raise DomainError("存在未处理异常，不得寄出")
    ts = now()
    cur = conn.execute(
        "INSERT INTO waybills(order_id, tracking_no, seq, parent_id, recv_unit,"
        " recv_address, recv_person, recv_phone, seal_no, status, created_at)"
        " VALUES(?,?,1,NULL,?,?,?,?,?,'active',?)",
        (order_id, f"PENDING-{order_id}", order["recv_unit"], order["recv_address"],
         order["recv_person"], order["recv_phone"], order["seal_no"], ts),
    )
    wb_id = cur.lastrowid
    adapter = MockLogisticsAdapter(conn)
    tracking_no = adapter.create_shipment(wb_id, script)
    _set_status(conn, order_id, archivist, "寄出", "shipped",
                f"运单号 {tracking_no}，封签号 {order['seal_no']}")
    conn.commit()
    return wb_id


# ---------------------------------------------------------------- 物流推进与签收

def _apply_event(conn, waybill, event):
    """物流事件回写业务状态。"""
    kind = event["kind"]
    order_id = waybill["order_id"]
    if kind == "signed":
        conn.execute("UPDATE waybills SET status='delivered' WHERE id=?", (waybill["id"],))
        _set_status(conn, order_id, None, "物流签收", "signed",
                    f"运单 {waybill['tracking_no']} 已签收")
    elif kind == "rejected":
        conn.execute("UPDATE waybills SET status='returning' WHERE id=?", (waybill["id"],))
        _flag_exception(conn, order_id, None, "rejected",
                        event["description"], waybill_id=waybill["id"])
        _audit(conn, order_id, None, "接收单位拒收", note=event["description"])
    elif kind == "stalled":
        _flag_exception(conn, order_id, None, "stalled",
                        event["description"], waybill_id=waybill["id"])
        _audit(conn, order_id, None, "物流滞留", note=event["description"])


def advance_logistics(conn, waybill_id=None):
    """推进物流（模拟适配器）。返回 [(waybill, event), ...]。"""
    adapter = MockLogisticsAdapter(conn)
    if waybill_id:
        waybills = [one(conn, "SELECT * FROM waybills WHERE id=?", (waybill_id,))]
    else:
        waybills = all_(conn, "SELECT * FROM waybills WHERE status='active' ORDER BY id")
    results = []
    for wb in [w for w in waybills if w]:
        event = adapter.advance(wb)
        if event:
            _apply_event(conn, wb, event)
            results.append((wb, event))
    conn.commit()
    return results


def urge_shipment(conn, waybill_id, archivist):
    """物流滞留处理：档案室催办后运单恢复推进。"""
    _require_role(archivist, "archivist")
    wb = one(conn, "SELECT * FROM waybills WHERE id=?", (waybill_id,))
    if not wb:
        raise DomainError("运单不存在")
    exc = one(conn, "SELECT * FROM exceptions WHERE waybill_id=? AND type='stalled'"
              " AND status='open'", (waybill_id,))
    if not exc:
        raise DomainError("该运单没有未处理的滞留异常")
    conn.execute("UPDATE waybills SET urged=1 WHERE id=?", (waybill_id,))
    _resolve_exception(conn, exc["id"], archivist, "已向物流商催办，运单恢复运输")
    _audit(conn, wb["order_id"], archivist, "滞留催办",
           note=f"运单 {wb['tracking_no']}")
    conn.commit()


# ---------------------------------------------------------------- 退件与重寄

def initiate_return(conn, waybill_id, archivist, reason=""):
    """寄出后如需变更，只能启动退回重寄。"""
    _require_role(archivist, "archivist")
    wb = one(conn, "SELECT * FROM waybills WHERE id=?", (waybill_id,))
    if not wb:
        raise DomainError("运单不存在")
    if wb["status"] != "active":
        raise DomainError("仅运输中的运单可以启动退回")
    conn.execute("UPDATE waybills SET status='returning' WHERE id=?", (waybill_id,))
    _audit(conn, wb["order_id"], archivist, "启动退回",
           note=reason or f"运单 {wb['tracking_no']} 退回")
    conn.commit()


def confirm_returned(conn, waybill_id, archivist):
    _require_role(archivist, "archivist")
    wb = one(conn, "SELECT * FROM waybills WHERE id=?", (waybill_id,))
    if not wb:
        raise DomainError("运单不存在")
    if wb["status"] != "returning":
        raise DomainError("运单不在退回流程中")
    conn.execute("UPDATE waybills SET status='returned' WHERE id=?", (waybill_id,))
    ts = now()
    conn.execute(
        "INSERT INTO tracking_events(waybill_id, ts, location, description, kind)"
        " VALUES(?,?,?,?,?)",
        (waybill_id, ts, "校园集散中心", "退件已返校，档案袋入库待重寄", "normal"))
    _audit(conn, wb["order_id"], archivist, "退件入库",
           note=f"运单 {wb['tracking_no']}")
    conn.commit()


def resend(conn, order_id, archivist, new_unit, new_address, new_person, new_phone,
           script="normal"):
    """退回重寄：生成新运单并与原运单链接（退件链）。"""
    _require_role(archivist, "archivist")
    order = get_order(conn, order_id)
    if order["status"] != "shipped":
        raise DomainError("仅已寄出的转递单可以退回重寄")
    wbs = all_(conn, "SELECT * FROM waybills WHERE order_id=? ORDER BY seq", (order_id,))
    if not wbs or wbs[-1]["status"] != "returned":
        raise DomainError("须等上一运单退件入库后才能重寄")
    prev = wbs[-1]
    for label, val in (("接收单位", new_unit), ("接收地址", new_address),
                       ("接收人", new_person), ("接收人电话", new_phone)):
        if not (val or "").strip():
            raise DomainError(f"{label}不能为空")
    # 更正后的接收信息写回转递单（寄出后唯一的地址变更途径）
    conn.execute(
        "UPDATE transfer_orders SET recv_unit=?, recv_address=?, recv_person=?,"
        " recv_phone=? WHERE id=?",
        (new_unit.strip(), new_address.strip(), new_person.strip(), new_phone.strip(),
         order_id))
    cur = conn.execute(
        "INSERT INTO waybills(order_id, tracking_no, seq, parent_id, recv_unit,"
        " recv_address, recv_person, recv_phone, seal_no, status, created_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,'active',?)",
        (order_id, f"PENDING-{order_id}-{prev['seq'] + 1}", prev["seq"] + 1, prev["id"],
         new_unit.strip(), new_address.strip(), new_person.strip(), new_phone.strip(),
         order["seal_no"], now()),
    )
    wb_id = cur.lastrowid
    tracking_no = MockLogisticsAdapter(conn).create_shipment(wb_id, script)
    # 拒收/破损异常随重寄闭环
    for exc in open_exceptions(conn, order_id, ("rejected", "seal_damaged")):
        _resolve_exception(conn, exc["id"], archivist,
                           f"退回重寄，新运单 {tracking_no}")
    _touch(conn, order_id)
    _audit(conn, order_id, archivist, "退回重寄",
           note=f"原运单 {prev['tracking_no']} → 新运单 {tracking_no}；"
                f"新地址：{new_address.strip()}")
    conn.commit()
    return wb_id


# ---------------------------------------------------------------- 异常处理

def _flag_exception(conn, order_id, actor, type_, detail, waybill_id=None):
    if type_ not in EXC_TYPES:
        raise DomainError(f"未知异常类型: {type_}")
    cur = conn.execute(
        "INSERT INTO exceptions(order_id, waybill_id, type, detail, created_by,"
        " created_at) VALUES(?,?,?,?,?,?)",
        (order_id, waybill_id, type_, detail, actor["id"] if actor else None, now()))
    return cur.lastrowid


def flag_exception(conn, order_id, actor, type_, detail, waybill_id=None):
    _require_role(actor, "archivist", "department")
    get_order(conn, order_id)
    exc_id = _flag_exception(conn, order_id, actor, type_, detail, waybill_id)
    _audit(conn, order_id, actor, "登记异常", note=f"{type_}：{detail}")
    conn.commit()
    return exc_id


def _resolve_exception(conn, exc_id, actor, resolution):
    conn.execute(
        "UPDATE exceptions SET status='resolved', resolution=?, resolved_by=?,"
        " resolved_at=? WHERE id=?",
        (resolution, actor["id"] if actor else None, now(), exc_id))


def resolve_exception(conn, exc_id, archivist, resolution):
    _require_role(archivist, "archivist")
    exc = one(conn, "SELECT * FROM exceptions WHERE id=?", (exc_id,))
    if not exc:
        raise DomainError("异常单不存在")
    if exc["status"] != "open":
        raise DomainError("异常已处理")
    if exc["type"] == "missing_pages":
        raise DomainError("材料缺页须通过补齐材料或缺页豁免闭环")
    if exc["type"] == "name_confusion":
        raise DomainError("同名混淆须通过身份核验闭环")
    if exc["type"] == "stalled":
        raise DomainError("物流滞留须通过催办闭环")
    _resolve_exception(conn, exc_id, archivist, resolution)
    _audit(conn, exc["order_id"], archivist, "异常处理", note=resolution)
    conn.commit()


def supplement_missing_pages(conn, exc_id, archivist, note=""):
    """材料缺页：学生补齐材料后由档案室核验闭环。"""
    _require_role(archivist, "archivist")
    exc = one(conn, "SELECT * FROM exceptions WHERE id=?", (exc_id,))
    if not exc or exc["type"] != "missing_pages" or exc["status"] != "open":
        raise DomainError("缺页异常不存在或已处理")
    _resolve_exception(conn, exc_id, archivist, note or "缺失页已补齐并核验")
    _audit(conn, exc["order_id"], archivist, "缺页补齐", note=note or "缺失页已补齐并核验")
    conn.commit()


def request_exemption(conn, exc_id, archivist, reason):
    """缺页豁免申请：档案室发起，院系审批。"""
    _require_role(archivist, "archivist")
    exc = one(conn, "SELECT * FROM exceptions WHERE id=?", (exc_id,))
    if not exc or exc["type"] != "missing_pages" or exc["status"] != "open":
        raise DomainError("缺页异常不存在或已处理")
    if not (reason or "").strip():
        raise DomainError("豁免理由不能为空")
    if one(conn, "SELECT id FROM exemptions WHERE exception_id=? AND status='pending'",
           (exc_id,)):
        raise DomainError("已存在待审批的豁免申请")
    cur = conn.execute(
        "INSERT INTO exemptions(exception_id, order_id, reason, requested_by,"
        " created_at) VALUES(?,?,?,?,?)",
        (exc_id, exc["order_id"], reason.strip(), archivist["id"], now()))
    _audit(conn, exc["order_id"], archivist, "申请缺页豁免", note=reason.strip())
    conn.commit()
    return cur.lastrowid


def decide_exemption(conn, exemption_id, user, approve, note=""):
    """审批缺页豁免。职责分离：只能由院系审批，档案室（包括非申请人）无权批准。"""
    _require_role(user, "department")
    ex = one(conn, "SELECT * FROM exemptions WHERE id=?", (exemption_id,))
    if not ex:
        raise DomainError("豁免申请不存在")
    if ex["status"] != "pending":
        raise DomainError("该豁免申请已审批")
    if ex["requested_by"] == user["id"]:
        raise DomainError("不能审批自己发起的豁免申请")
    conn.execute(
        "UPDATE exemptions SET status=?, decided_by=?, decided_at=? WHERE id=?",
        ("approved" if approve else "rejected", user["id"], now(), exemption_id))
    if approve:
        exc = one(conn, "SELECT * FROM exceptions WHERE id=?", (ex["exception_id"],))
        _resolve_exception(conn, ex["exception_id"], user,
                           f"缺页豁免批准：{ex['reason']}")
        _audit(conn, ex["order_id"], user, "缺页豁免批准",
               note=note or ex["reason"])
    else:
        _audit(conn, ex["order_id"], user, "缺页豁免拒绝", note=note)
    conn.commit()


def resolve_name_confusion(conn, exc_id, archivist, student_no, id_tail):
    """同名学生档案混淆：档案室核验 学号+身份证后四位 后解除。"""
    _require_role(archivist, "archivist")
    exc = one(conn, "SELECT * FROM exceptions WHERE id=?", (exc_id,))
    if not exc or exc["type"] != "name_confusion" or exc["status"] != "open":
        raise DomainError("同名混淆异常不存在或已处理")
    order = get_order(conn, exc["order_id"])
    stu = get_student(conn, order["student_id"])
    if not identity_match(stu, stu["name"], student_no, id_tail):
        _audit(conn, order["id"], archivist, "身份核验失败",
               note=f"录入学号 {student_no}")
        conn.commit()
        raise DomainError("身份核验失败：学号或身份证后四位与档案不符")
    _resolve_exception(conn, exc_id, archivist,
                       f"身份核验通过（学号 {student_no}），解除混淆挂起")
    _audit(conn, order["id"], archivist, "身份核验通过",
           note=f"学号 {student_no}，解除同名混淆")
    conn.commit()


# ---------------------------------------------------------------- 封签破损：重新封装

def reseal(conn, order_id, archivist):
    """封签破损：旧封签作废，重新封装并记录新封签号。"""
    _require_role(archivist, "archivist")
    order = get_order(conn, order_id)
    exc = one(conn, "SELECT * FROM exceptions WHERE order_id=? AND type='seal_damaged'"
              " AND status='open'", (order_id,))
    if not exc:
        raise DomainError("没有未处理的封签破损异常")
    old = order["seal_no"] or "无"
    base = _next_seal_no(conn, order)
    n = 1
    new_seal = f"{base}-R{n}"
    while one(conn, "SELECT id FROM transfer_orders WHERE seal_no=?", (new_seal,)):
        n += 1
        new_seal = f"{base}-R{n}"
    conn.execute("UPDATE transfer_orders SET seal_no=? WHERE id=?", (new_seal, order_id))
    _resolve_exception(conn, exc["id"], archivist,
                       f"重新封装：{old} 作废，新封签号 {new_seal}")
    _touch(conn, order_id)
    _audit(conn, order_id, archivist, "重新封装",
           note=f"封签号 {old} → {new_seal}")
    conn.commit()
    return new_seal


# ---------------------------------------------------------------- 签收归档

def archive_order(conn, order_id, archivist):
    _require_role(archivist, "archivist")
    order = get_order(conn, order_id)
    if order["status"] != "signed":
        raise DomainError("须待接收单位签收后才能归档")
    _set_status(conn, order_id, archivist, "签收归档", "archived",
                f"封签号 {order['seal_no']}，转递链闭环")
    conn.commit()


# ---------------------------------------------------------------- 证明材料（遮罩落盘）

def upload_material(conn, order_id, user, title, content, materials_dir):
    """证明材料本地保存，落盘内容一律遮罩（原始内容从不写入磁盘）。"""
    _require_role(user, "student", "archivist")
    order = get_order(conn, order_id)
    if user["role"] == "student" and order["student_id"] != user["id"]:
        raise DomainError("只能为本人转递单上传材料")
    if not (title or "").strip():
        raise DomainError("材料名称不能为空")
    masked = mask_text(content or "")
    os.makedirs(materials_dir, exist_ok=True)
    cur = conn.execute(
        "INSERT INTO materials(order_id, title, stored_path, uploaded_by, created_at)"
        " VALUES(?,?,?,?,?)",
        (order_id, title.strip(), "", user["id"], now()))
    mid = cur.lastrowid
    path = os.path.join(materials_dir, f"material_{mid:06d}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(masked)
    conn.execute("UPDATE materials SET stored_path=? WHERE id=?", (path, mid))
    _audit(conn, order_id, user, "上传证明材料", note=title.strip())
    conn.commit()
    return mid


def list_materials_for_view(conn, order_id, user):
    """职责分离：院系不得查看档案袋内敏感材料。"""
    order = get_order(conn, order_id)
    if user["role"] == "department":
        raise DomainError("院系不得查看档案袋内的敏感材料")
    if user["role"] == "student" and order["student_id"] != user["id"]:
        raise DomainError("只能查看本人转递单的材料")
    return all_(conn, "SELECT * FROM materials WHERE order_id=? ORDER BY id", (order_id,))


def read_material(conn, material_id, user):
    m = one(conn, "SELECT * FROM materials WHERE id=?", (material_id,))
    if not m:
        raise DomainError("材料不存在")
    list_materials_for_view(conn, m["order_id"], user)  # 复用权限检查
    with open(m["stored_path"], "r", encoding="utf-8") as f:
        return m, f.read()

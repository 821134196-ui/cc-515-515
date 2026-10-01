"""档案转递核心业务：状态机、职责分离与五类异常处置。

所有公开方法接收 actor（含 role 与可选 student_id），违规一律 PermissionError；
状态不允许的动作抛 ServiceError。Controller 捕获后转 HTTP 403/409。
"""
import json
from datetime import datetime, timedelta

from . import shipping
from .db import connect, tx

BASE_TS = datetime(2025, 6, 20, 9, 0, 0)

ORDER_STATUS_CN = {
    "declared": "去向已申报",
    "verified": "院系已核对",
    "packed": "已封装",
    "shipped": "已寄出",
    "exception": "异常处理中",
    "returned": "已退回待重寄",
    "archived": "已签收归档",
    "void": "已作废",
}
NODE_CN = {
    "student": "学生", "department": "院系", "archive": "档案室",
    "shipping": "物流", "receiver": "接收单位", "approver": "学校审批人",
}
EXC_CN = {"reject": "接收单位拒收", "seal_damaged": "封签破损",
          "missing_page": "材料缺页", "stagnant": "物流滞留", "confusion": "同名档案混淆"}


class ServiceError(Exception):
    pass


def _now(conn=None):
    """确定性时钟：测试可用 advance_clock 重置；默认取真实当前时间。"""
    return _clock().strftime("%Y-%m-%d %H:%M:%S")


def _clock():
    return datetime.now()


# ---------------------------------------------------------------- 脱敏
def mask_id_card(no: str) -> str:
    return no[:6] + "********" + no[-4:] if len(no) >= 10 else "****" + no[-2:]


def mask_phone(p: str) -> str:
    return p[:3] + "****" + p[-4:] if len(p) >= 7 else "****"


def _sensitive_allowed(actor):
    """敏感材料/完整证件号仅档案室与学校审批人可见。院系、学生本人均不可见袋内清单。"""
    return actor["role"] in ("archive", "approver")


# ---------------------------------------------------------------- 工具
def _event(conn, order_id, node, actor, action, detail=None):
    seq = conn.execute("SELECT COALESCE(MAX(seq),0)+1 s FROM events WHERE order_id=?",
                       (order_id,)).fetchone()["s"]
    conn.execute(
        "INSERT INTO events(order_id,seq,node,actor_id,actor_role,action,detail,created_at)"
        " VALUES(?,?,?,?,?,?,?,?)",
        (order_id, seq, node, actor.get("id") if actor else None,
         actor.get("role") if actor else None, action,
         json.dumps(detail or {}, ensure_ascii=False), _now(conn)))


def _set_status(conn, oid, status):
    conn.execute("UPDATE transfer_orders SET status=?, updated_at=? WHERE id=?",
                 (status, _now(conn), oid))


def _get_order(conn, oid):
    row = conn.execute("SELECT * FROM transfer_orders WHERE id=?", (oid,)).fetchone()
    if not row:
        raise ServiceError("转递单不存在")
    return row


def _order_dict(conn, row, actor, with_chain=True):
    d = dict(row)
    stu = conn.execute("SELECT * FROM students WHERE id=?", (row["student_id"],)).fetchone()
    d["student"] = {
        "id": stu["id"], "student_no": stu["student_no"], "name": stu["name"],
        "college": stu["college"], "major": stu["major"], "grad_batch": stu["grad_batch"],
        "grad_status": stu["grad_status"],
        "id_card_tail": stu["id_card"][-4:],
        "phone_masked": mask_phone(stu["phone"]),
    }
    # 完整证件号只对档案室/审批人开放（同名混淆核对时审批可用）
    d["student"]["id_card_full"] = stu["id_card"] if _sensitive_allowed(actor) else None
    d["status_cn"] = ORDER_STATUS_CN[row["status"]]
    d["receiver_phone_masked"] = mask_phone(row["receiver_phone"])
    # 接收电话为学生本人申报信息：本人编辑时可见原值；其他角色只见遮罩
    d["receiver_phone_self"] = row["receiver_phone"] if (
        actor["role"] == "student" and actor.get("student_id") == row["student_id"]
    ) or _sensitive_allowed(actor) else None
    d["report_materials"] = json.loads(row["report_materials"])
    # 学生只能看自己的单；院系/档案室/审批人按业务范围看全部（演示不做院系数据隔离）
    if actor["role"] == "student" and actor.get("student_id") != row["student_id"]:
        raise PermissionError("无权查看他人转递单")
    if with_chain:
        d["events"] = _chain(conn, row["id"])
        d["waybills"] = _waybill_chain(conn, row["id"], actor)
        d["packing"] = _packing(conn, row["id"], actor)
        d["address_changes"] = [dict(r) for r in conn.execute(
            "SELECT id,kind,new_org,new_addr,new_contact,status,reason,review_note,created_at,reviewed_at"
            " FROM address_changes WHERE order_id=? ORDER BY id", (row["id"],)).fetchall()]
        d["waivers"] = [dict(r) for r in conn.execute(
            "SELECT id,reason,materials,status,request_by,reviewed_by,review_note,created_at,reviewed_at"
            " FROM missing_requests WHERE order_id=? ORDER BY id", (row["id"],)).fetchall()]
        d["exceptions"] = [dict(r) for r in conn.execute(
            "SELECT * FROM exceptions WHERE order_id=? ORDER BY id", (row["id"],)).fetchall()]
        d["open_exceptions"] = [e for e in d["exceptions"] if e["status"] == "open"]
    else:
        # 列表视图：只带精简运单链与封签号（供档案室批次密集表使用）
        d["waybills"] = [{"tracking_no": r["tracking_no"], "leg": r["leg"], "status": r["status"]}
                         for r in conn.execute(
                             "SELECT tracking_no,leg,status FROM waybills WHERE order_id=? ORDER BY leg",
                             (row["id"],)).fetchall()]
        d["packing"] = {"seal_no": conn.execute(
            "SELECT seal_no FROM packing_lists WHERE order_id=?", (row["id"],)).fetchone()["seal_no"]} \
            if conn.execute("SELECT 1 FROM packing_lists WHERE order_id=?", (row["id"],)).fetchone() else None
        d["open_exceptions"] = [dict(r) for r in conn.execute(
            "SELECT id,order_id,waybill_id,type,status,note FROM exceptions"
            " WHERE order_id=? AND status='open' ORDER BY id", (row["id"],)).fetchall()]
    return d


def _chain(conn, oid):
    rows = conn.execute("SELECT * FROM events WHERE order_id=? ORDER BY seq", (oid,)).fetchall()
    out = []
    for r in rows:
        out.append({"seq": r["seq"], "node": r["node"], "node_cn": NODE_CN.get(r["node"], r["node"]),
                    "actor_role": r["actor_role"], "action": r["action"],
                    "detail": json.loads(r["detail"]), "created_at": r["created_at"]})
    return out


def _packing(conn, oid, actor):
    r = conn.execute("SELECT * FROM packing_lists WHERE order_id=?", (oid,)).fetchone()
    if not r:
        return None
    mats = json.loads(r["materials"])
    if not _sensitive_allowed(actor):
        # 院系/学生只能看到封签号与材料份数，看不到袋内敏感材料名称
        mats = [{"name": "（已封装材料，详情仅档案室可见）", "sensitive": 1,
                 "copies": sum(m.get("copies", 1) for m in mats)}]
    return {"id": r["id"], "seal_no": r["seal_no"], "materials": mats,
            "packed_by": r["packed_by"], "created_at": r["created_at"]}


def _waybill_chain(conn, oid, actor):
    rows = conn.execute("SELECT * FROM waybills WHERE order_id=? ORDER BY leg", (oid,)).fetchall()
    out = []
    for r in rows:
        evs = conn.execute("SELECT * FROM waybill_events WHERE waybill_id=? ORDER BY seq",
                           (r["id"],)).fetchall()
        out.append({"id": r["id"], "leg": r["leg"], "parent_id": r["parent_id"],
                    "tracking_no": r["tracking_no"], "carrier": r["carrier"],
                    "scenario": r["scenario"], "status": r["status"],
                    "status_cn": shipping.STATUS_CN.get(r["status"], r["status"]),
                    "shipped_at": r["shipped_at"], "delivered_at": r["delivered_at"],
                    "returned_at": r["returned_at"],
                    "events": [{"seq": e["seq"], "status": e["status"],
                                "status_cn": shipping.STATUS_CN.get(e["status"], e["status"]),
                                "location": e["location"], "note": e["note"],
                                "created_at": e["created_at"]} for e in evs]})
    return out


# ---------------------------------------------------------------- 1. 去向申报
def declare(actor, payload, db_path=None):
    if actor["role"] != "student":
        raise PermissionError("仅学生本人可申报去向")
    required = ("receiver_org", "receiver_addr", "receiver_contact", "receiver_phone")
    for k in required:
        if not str(payload.get(k, "")).strip():
            raise ServiceError(f"缺少必填项：{k}")
    with tx(db_path) as conn:
        sid = actor["student_id"]
        batch = conn.execute("SELECT grad_batch FROM students WHERE id=?", (sid,)).fetchone()["grad_batch"]
        # 同一毕业批次只允许一份有效转递单
        dup = conn.execute(
            "SELECT id FROM transfer_orders WHERE student_id=? AND grad_batch=? AND status!='void'",
            (sid, batch)).fetchone()
        if dup:
            raise ServiceError("同一毕业批次已存在有效转递单，不能重复申报")
        ts = _now(conn)
        materials = payload.get("report_materials") or ["报到证", "毕业证书复印件", "学位证书复印件"]
        cur = conn.execute(
            "INSERT INTO transfer_orders(order_no,student_id,grad_batch,receiver_org,receiver_addr,"
            "receiver_contact,receiver_phone,report_materials,status,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (f"ZD{batch}{sid:04d}", sid, batch, payload["receiver_org"].strip(),
             payload["receiver_addr"].strip(), payload["receiver_contact"].strip(),
             payload["receiver_phone"].strip(), json.dumps(materials, ensure_ascii=False),
             "declared", ts, ts))
        oid = cur.lastrowid
        _event(conn, oid, "student", actor, "去向申报",
               {"receiver_org": payload["receiver_org"], "receiver_addr": payload["receiver_addr"]})
        return _order_now(conn, oid, actor)


# ---------------------------------------------------------------- 2. 院系核对
def verify_graduation(actor, order_id, confirmed, note="", db_path=None):
    if actor["role"] != "department":
        raise PermissionError("仅院系可核对毕业状态")
    with tx(db_path) as conn:
        o = _get_order(conn, order_id)
        if o["status"] != "declared":
            raise ServiceError("仅“去向已申报”状态可核对")
        stu = conn.execute("SELECT * FROM students WHERE id=?", (o["student_id"],)).fetchone()
        if confirmed:
            conn.execute("UPDATE students SET grad_status='confirmed' WHERE id=?", (stu["id"],))
            _set_status(conn, order_id, "verified")
            _event(conn, order_id, "department", actor, "毕业状态核对通过", {"note": note})
        else:
            # 核对不通过：作废该单（释放唯一单号），学生可重新申报
            conn.execute("UPDATE students SET grad_status='pending' WHERE id=?", (stu["id"],))
            _set_status(conn, order_id, "void")
            conn.execute("UPDATE transfer_orders SET order_no=order_no||'-VOID'||id WHERE id=?",
                         (order_id,))
            _event(conn, order_id, "department", actor, "毕业状态核对不通过，转递单作废", {"note": note})
        return _order_now(conn, order_id, actor)


# ---------------------------------------------------------------- 3. 寄出前地址更正（须复核）
def request_address_change(actor, order_id, payload, db_path=None):
    if actor["role"] != "student":
        raise PermissionError("仅学生可申请更正接收地址")
    with tx(db_path) as conn:
        o = _get_order(conn, order_id)
        if actor.get("student_id") != o["student_id"]:
            raise PermissionError("只能更正本人转递单")
        # 地址变更必须在寄出前完成；封装后地址即锁定，已寄出只能走退回重寄
        if o["status"] in ("packed", "shipped", "exception"):
            raise ServiceError("档案已封装/寄出，地址已锁定；只能在退回后申请重寄")
        if o["status"] in ("archived", "void"):
            raise ServiceError("当前状态不允许地址更正")
        pend = conn.execute("SELECT id FROM address_changes WHERE order_id=? AND kind='address' AND status='pending'",
                            (order_id,)).fetchone()
        if pend:
            raise ServiceError("已有一条地址更正申请待复核")
        cur = conn.execute(
            "INSERT INTO address_changes(order_id,new_org,new_addr,new_contact,new_phone,reason,kind,"
            "status,requested_by,created_at) VALUES(?,?,?,?,?,?, 'address','pending',?,?)",
            (order_id, payload.get("receiver_org", o["receiver_org"]).strip(),
             payload["receiver_addr"].strip(), payload.get("receiver_contact", o["receiver_contact"]).strip(),
             payload.get("receiver_phone", o["receiver_phone"]).strip(),
             payload.get("reason", ""), actor["id"], _now(conn)))
        _event(conn, order_id, "student", actor, "申请更正接收地址（待院系复核）",
               {"change_id": cur.lastrowid, "new_addr": payload["receiver_addr"]})
        return _order_now(conn, order_id, actor)


def review_address_change(actor, change_id, approve, note="", db_path=None):
    if actor["role"] != "department":
        raise PermissionError("地址更正须由院系复核")
    with tx(db_path) as conn:
        ch = conn.execute("SELECT * FROM address_changes WHERE id=?", (change_id,)).fetchone()
        if not ch:
            raise ServiceError("更正申请不存在")
        if ch["status"] != "pending":
            raise ServiceError("该申请已复核")
        o = _get_order(conn, ch["order_id"])
        # 寄出前必须完成复核；封装完成（已排寄）后拒绝变更，保证地址锁定
        if o["status"] not in ("declared", "verified"):
            raise ServiceError("档案已封装/寄出，地址已锁定；只能退回重寄")
        if approve:
            conn.execute("UPDATE transfer_orders SET receiver_org=?,receiver_addr=?,receiver_contact=?,"
                         "receiver_phone=?,updated_at=? WHERE id=?",
                         (ch["new_org"], ch["new_addr"], ch["new_contact"], ch["new_phone"],
                          _now(conn), o["id"]))
            conn.execute("UPDATE address_changes SET status='approved',reviewed_by=?,review_note=?,reviewed_at=? WHERE id=?",
                         (actor["id"], note, _now(conn), change_id))
            _event(conn, o["id"], "department", actor, "地址更正复核通过，接收信息已更新",
                   {"new_org": ch["new_org"], "new_addr": ch["new_addr"], "note": note})
        else:
            conn.execute("UPDATE address_changes SET status='rejected',reviewed_by=?,review_note=?,reviewed_at=? WHERE id=?",
                         (actor["id"], note, _now(conn), change_id))
            _event(conn, o["id"], "department", actor, "地址更正复核驳回，维持原接收信息", {"note": note})
        return _order_now(conn, o["id"], actor)


# ---------------------------------------------------------------- 4. 封装清单
DEFAULT_PACKING = [
    {"name": "高中毕业生登记表", "sensitive": 1, "copies": 1},
    {"name": "高等学校毕业生登记表", "sensitive": 1, "copies": 1},
    {"name": "成绩单", "sensitive": 1, "copies": 1},
    {"name": "学位授予证明存根", "sensitive": 1, "copies": 1},
    {"name": "奖惩材料", "sensitive": 1, "copies": 1},
    {"name": "党团组织关系材料", "sensitive": 1, "copies": 1},
    {"name": "报到证副本", "sensitive": 0, "copies": 1},
]


def pack(actor, order_id, materials=None, db_path=None):
    if actor["role"] != "archive":
        raise PermissionError("仅档案室可封装档案袋")
    with tx(db_path) as conn:
        o = _get_order(conn, order_id)
        if o["status"] != "verified":
            raise ServiceError("仅“院系已核对”状态可封装")
        pend = conn.execute("SELECT id FROM address_changes WHERE order_id=? AND kind='address' AND status='pending'",
                            (order_id,)).fetchone()
        if pend:
            raise ServiceError("该单有地址更正申请尚未复核，须先完成复核才能封装")
        existing = conn.execute("SELECT id FROM packing_lists WHERE order_id=?", (order_id,)).fetchone()
        if existing:
            raise ServiceError("该单已封装")
        mats = materials or DEFAULT_PACKING
        seal = f"FQ{o['grad_batch']}-{order_id:04d}"
        conn.execute("INSERT INTO packing_lists(order_id,seal_no,materials,packed_by,created_at)"
                     " VALUES(?,?,?,?,?)",
                     (order_id, seal, json.dumps(mats, ensure_ascii=False), actor["id"], _now(conn)))
        _set_status(conn, order_id, "packed")
        _event(conn, order_id, "archive", actor, "档案袋封装并加封签", {"seal_no": seal, "items": len(mats)})
        return _order_now(conn, order_id, actor)


# ---------------------------------------------------------------- 5. 寄出与物流推进
def ship(actor, order_id, scenario="normal", db_path=None):
    if actor["role"] != "archive":
        raise PermissionError("仅档案室可寄出档案")
    if scenario not in shipping.STEPS:
        raise ServiceError("未知物流场景")
    with tx(db_path) as conn:
        o = _get_order(conn, order_id)
        if o["status"] != "packed":
            raise ServiceError("仅“已封装”状态可寄出")
        leg = conn.execute("SELECT COALESCE(MAX(leg),0)+1 l FROM waybills WHERE order_id=?",
                           (order_id,)).fetchone()["l"]
        tracking = f"SF{o['grad_batch']}{order_id:04d}" if leg == 1 else f"SF{o['grad_batch']}{order_id:04d}R{leg}"
        cur = conn.execute(
            "INSERT INTO waybills(order_id,leg,parent_id,tracking_no,scenario,status,ticks,shipped_at)"
            " VALUES(?,?, (SELECT id FROM waybills WHERE order_id=? AND leg=?), ?,?,?,1,?)",
            (order_id, leg, order_id, leg - 1, tracking, scenario, "picked_up", _now(conn)))
        wid = cur.lastrowid
        conn.execute("INSERT INTO waybill_events(waybill_id,seq,status,location,note,created_at)"
                     " VALUES(?,1,'picked_up','校寄达营业点','已揽收',?)", (wid, _now(conn)))
        _set_status(conn, order_id, "shipped")
        _event(conn, order_id, "archive", actor, "档案寄出",
               {"tracking_no": tracking, "leg": leg, "scenario": scenario})
        return _order_now(conn, order_id, actor)


def _open_exception(conn, order_id, wb, etype, note, actor_id):
    wb_id = wb["id"] if wb is not None else None
    row = conn.execute("SELECT id FROM exceptions WHERE order_id=? AND IFNULL(waybill_id,-1)=IFNULL(?,-1)"
                       " AND type=? AND status='open'",
                       (order_id, wb_id, etype)).fetchone()
    if row:
        return
    cur = conn.execute(
        "INSERT INTO exceptions(order_id,waybill_id,type,status,note,opened_by,created_at)"
        " VALUES(?, ?,?,'open',?, ?,?)",
        (order_id, wb_id, etype, note, actor_id, _now(conn)))
    _set_status(conn, order_id, "exception")
    _event(conn, order_id, "receiver" if etype in ("reject", "seal_damaged") else "shipping",
           {"id": actor_id, "role": "archive"}, f"登记异常：{EXC_CN[etype]}",
           {"exception_id": cur.lastrowid, "waybill": wb["tracking_no"], "note": note})


def advance_logistics(db_path=None):
    """推进所有在途运单一跳（模拟物流回调）。返回本跳发生变化的运单号列表。"""
    changed = []
    with tx(db_path) as conn:
        rows = conn.execute("SELECT * FROM waybills WHERE status NOT IN ('delivered','returned')").fetchall()
        for wb in rows:
            urged = bool(wb["urged"])
            step = shipping.advance(wb["scenario"], wb["ticks"], urged=urged)
            if step is None:
                # 滞留件本跳无物流动态：仍推进计数，达到阈值跳后登记滞留异常
                conn.execute("UPDATE waybills SET ticks=? WHERE id=?", (wb["ticks"] + 1, wb["id"]))
                continue
            status, loc, note, etype, stagnant_alert = step
            new_ticks = wb["ticks"] + 1
            seq = conn.execute("SELECT COALESCE(MAX(seq),0)+1 s FROM waybill_events WHERE waybill_id=?",
                               (wb["id"],)).fetchone()["s"]
            conn.execute("INSERT INTO waybill_events(waybill_id,seq,status,location,note,created_at)"
                         " VALUES(?,?,?,?,?,?)", (wb["id"], seq, status, loc, note, _now(conn)))
            wu = {"delivered_at": None, "returned_at": None}
            if status == "delivered":
                wu["delivered_at"] = _now(conn)
            if status == "returned":
                wu["returned_at"] = _now(conn)
            conn.execute("UPDATE waybills SET status=?,ticks=?,delivered_at=COALESCE(?,delivered_at),"
                         "returned_at=COALESCE(?,returned_at) WHERE id=?",
                         (status, new_ticks, wu["delivered_at"], wu["returned_at"], wb["id"]))
            changed.append(wb["tracking_no"])
            o = conn.execute("SELECT * FROM transfer_orders WHERE id=?", (wb["order_id"],)).fetchone()
            if etype:
                _open_exception(conn, o["id"], wb, etype, note, "物流系统")
            elif stagnant_alert:
                _open_exception(conn, o["id"], wb, "stagnant", note, "物流系统")
            elif status == "returned":
                _set_status(conn, o["id"], "returned")
                _event(conn, o["id"], "archive", {"id": "物流系统", "role": "archive"},
                       "退回件已送达档案室", {"tracking_no": wb["tracking_no"]})
            elif status == "delivered":
                # 同名混淆剧本：签收时挂“待身份核对”，需档案室比对身份证后四位+学号
                if wb["scenario"] == "confusion":
                    _open_exception(conn, o["id"], wb, "confusion",
                                    "接收单位签收时发现另有同名档案，需核对身份证后四位与学号后确认归属",
                                    "接收单位")
                else:
                    _auto_archive(conn, o, wb)
    return changed


def _auto_archive(conn, o, wb):
    if not conn.execute("SELECT 1 FROM exceptions WHERE order_id=? AND status='open'", (o["id"],)).fetchone():
        _set_status(conn, o["id"], "archived")
        _event(conn, o["id"], "archive", {"id": "系统", "role": "archive"}, "签收录入完成，转递单归档",
               {"tracking_no": wb["tracking_no"]})


# ---------------------------------------------------------------- 6. 异常处置
def open_exception(actor, order_id, etype, note, waybill_id=None, db_path=None):
    if actor["role"] != "archive":
        raise PermissionError("仅档案室可登记异常")
    if etype not in EXC_CN:
        raise ServiceError("未知异常类型")
    with tx(db_path) as conn:
        o = _get_order(conn, order_id)
        wb = None
        if waybill_id:
            wb = conn.execute("SELECT * FROM waybills WHERE id=?", (waybill_id,)).fetchone()
        if wb is None:
            wb = conn.execute("SELECT * FROM waybills WHERE order_id=? ORDER BY leg DESC LIMIT 1",
                              (order_id,)).fetchone()
        _open_exception(conn, order_id, wb, etype, note, actor["id"])
        return _order_now(conn, order_id, actor)


def urge_shipment(actor, order_id, db_path=None):
    """滞留处置：档案室催办，物流恢复流转。"""
    if actor["role"] != "archive":
        raise PermissionError("仅档案室可催办物流")
    with tx(db_path) as conn:
        o = _get_order(conn, order_id)
        wb = conn.execute("SELECT * FROM waybills WHERE order_id=? ORDER BY leg DESC LIMIT 1",
                          (order_id,)).fetchone()
        if not wb or wb["scenario"] != "stagnant":
            raise ServiceError("仅滞留运单可催办")
        # 将计数重置到告警点的下一跳，催办后先派送再签收
        conn.execute("UPDATE waybills SET urged=1, ticks=? WHERE id=?",
                     (shipping.STAGNANT_ALERT_AT + 1, wb["id"],))
        _resolve_exc(conn, o["id"], "stagnant", actor, "已向承运方催办，物流恢复流转", None)
        _event(conn, o["id"], "archive", actor, "发起物流催办", {"tracking_no": wb["tracking_no"]})
        if not conn.execute("SELECT 1 FROM exceptions WHERE order_id=? AND status='open'",
                            (o["id"],)).fetchone():
            _set_status(conn, o["id"], "shipped")
        return _order_now(conn, order_id, actor)


def recall_shipment(actor, order_id, db_path=None):
    """滞留处置（备选）：召回档案，退回重寄。"""
    if actor["role"] != "archive":
        raise PermissionError("仅档案室可召回运单")
    with tx(db_path) as conn:
        o = _get_order(conn, order_id)
        wb = conn.execute("SELECT * FROM waybills WHERE order_id=? ORDER BY leg DESC LIMIT 1",
                          (order_id,)).fetchone()
        if not wb:
            raise ServiceError("无在途运单")
        seq = conn.execute("SELECT COALESCE(MAX(seq),0)+1 s FROM waybill_events WHERE waybill_id=?",
                           (wb["id"],)).fetchone()["s"]
        conn.execute("INSERT INTO waybill_events(waybill_id,seq,status,location,note,created_at)"
                     " VALUES(?,?, 'returning','异地分拣中心','滞留件召回，退回中',?)",
                     (wb["id"], seq, _now(conn)))
        seq += 1
        conn.execute("INSERT INTO waybill_events(waybill_id,seq,status,location,note,created_at)"
                     " VALUES(?,?, 'returned','学校档案室','召回件已退回档案室',?)",
                     (wb["id"], seq, _now(conn)))
        conn.execute("UPDATE waybills SET status='returned',returned_at=? WHERE id=?",
                     (_now(conn), wb["id"]))
        _resolve_exc(conn, o["id"], "stagnant", actor, "滞留件召回，退回档案室待重寄", None)
        _set_status(conn, o["id"], "returned")
        _event(conn, o["id"], "archive", actor, "滞留件召回退回", {"tracking_no": wb["tracking_no"]})
        return _order_now(conn, order_id, actor)


def _resolve_exc(conn, order_id, etype, actor, resolution, waybill_id=None):
    q = "SELECT * FROM exceptions WHERE order_id=? AND type=? AND status='open'"
    args = [order_id, etype]
    if waybill_id:
        q += " AND waybill_id=?"; args.append(waybill_id)
    rows = conn.execute(q, args).fetchall()
    for r in rows:
        conn.execute("UPDATE exceptions SET status='resolved',resolved_by=?,resolution=?,resolved_at=? WHERE id=?",
                     (actor["id"], resolution, _now(conn), r["id"]))


def verify_seal_on_return(actor, order_id, intact, note="", db_path=None):
    """封签破损处置：退回后开箱核验。完好→可重寄；破损→补封并登记，触发缺页检查。"""
    if actor["role"] != "archive":
        raise PermissionError("仅档案室可核验封签")
    with tx(db_path) as conn:
        o = _get_order(conn, order_id)
        if o["status"] not in ("exception", "returned"):
            raise ServiceError("仅异常处理/已退回状态可核验封签")
        if intact:
            _resolve_exc(conn, order_id, "seal_damaged", actor, "退回核验：外封签磨损但内封完好，重新加封待重寄" + note, None)
            _set_status(conn, order_id, "returned")
            _event(conn, o["id"], "archive", actor, "破损件核验：内封完好，重新加封", {})
        else:
            _resolve_exc(conn, order_id, "seal_damaged", actor, "内封破损，已开箱清点并重新加封" + note, None)
            _event(conn, o["id"], "archive", actor, "破损件开箱清点、重新加封", {"need_page_check": True})
            _set_status(conn, order_id, "returned")
        return _order_now(conn, order_id, actor)


def request_missing_page_waiver(actor, order_id, missing_materials, reason, db_path=None):
    """材料缺页：档案室发起豁免申请（不能自己批准）。"""
    if actor["role"] != "archive":
        raise PermissionError("仅档案室可发起缺页豁免申请")
    with tx(db_path) as conn:
        o = _get_order(conn, order_id)
        pend = conn.execute("SELECT id FROM missing_requests WHERE order_id=? AND status='pending'",
                            (order_id,)).fetchone()
        if pend:
            raise ServiceError("已有缺页豁免申请待审批")
        cur = conn.execute(
            "INSERT INTO missing_requests(order_id,reason,materials,status,request_by,created_at)"
            " VALUES(?,?,?, 'pending',?,?)",
            (order_id, reason, json.dumps(missing_materials, ensure_ascii=False), actor["id"], _now(conn)))
        _open_exception(conn, order_id,
                        conn.execute("SELECT * FROM waybills WHERE order_id=? ORDER BY leg DESC LIMIT 1",
                                     (order_id,)).fetchone(),
                        "missing_page", f"退回清点发现缺页：{ '、'.join(missing_materials) }", actor["id"])
        _event(conn, o["id"], "archive", actor, "发起材料缺页豁免申请（待学校审批人批准）",
               {"request_id": cur.lastrowid, "materials": missing_materials})
        return _order_now(conn, order_id, actor)


def review_missing_waiver(actor, request_id, approve, note="", db_path=None):
    if actor["role"] != "approver":
        raise PermissionError("缺页豁免仅学校审批人可批准")
    with tx(db_path) as conn:
        rq = conn.execute("SELECT * FROM missing_requests WHERE id=?", (request_id,)).fetchone()
        if not rq:
            raise ServiceError("豁免申请不存在")
        if rq["status"] != "pending":
            raise ServiceError("该申请已审批")
        # 职责分离硬约束：发起人不能审批自己的申请；档案室角色一律不可批准
        if rq["request_by"] == actor["id"]:
            raise PermissionError("不能审批本人发起的豁免申请")
        conn.execute("UPDATE missing_requests SET status=?,reviewed_by=?,review_note=?,reviewed_at=? WHERE id=?",
                     ("approved" if approve else "rejected", actor["id"], note, _now(conn), request_id))
        if approve:
            _resolve_exc(conn, rq["order_id"], "missing_page", actor,
                         f"缺页豁免已批准：{rq['reason']}。{note}", None)
            _event(conn, rq["order_id"], "approver", actor, "批准材料缺页豁免，同意补材后重寄", {"note": note})
            # 缺页问题关闭后，若无其他未决异常则进入待重寄
            if not conn.execute("SELECT 1 FROM exceptions WHERE order_id=? AND status='open'",
                                (rq["order_id"],)).fetchone():
                _set_status(conn, rq["order_id"], "returned")
        else:
            _event(conn, rq["order_id"], "approver", actor, "驳回缺页豁免，需补齐材料", {"note": note})
        return _order_now(conn, rq["order_id"], actor)


def resolve_confusion(actor, order_id, id_card_tail, student_no, db_path=None):
    """同名混淆处置：凭身份证后四位 + 学号双重比对确认归属。"""
    if actor["role"] != "archive":
        raise PermissionError("仅档案室可进行同名核对")
    with tx(db_path) as conn:
        o = _get_order(conn, order_id)
        stu = conn.execute("SELECT * FROM students WHERE id=?", (o["student_id"],)).fetchone()
        if stu["id_card"][-4:] != str(id_card_tail) or stu["student_no"] != str(student_no):
            _event(conn, o["id"], "archive", actor, "同名核对未通过，档案将召回",
                   {"input_tail": id_card_tail, "input_no": student_no})
            raise ServiceError("身份信息不匹配：该档案不属于当前转递单学生，须召回重新核对")
        _resolve_exc(conn, order_id, "confusion", actor,
                     f"身份证后四位 {id_card_tail} 与学号 {student_no} 双重核对一致，档案归属确认", None)
        _event(conn, o["id"], "archive", actor, "同名档案核对通过，身份匹配确认",
               {"id_card_tail": id_card_tail, "student_no": student_no})
        wb = conn.execute("SELECT * FROM waybills WHERE order_id=? ORDER BY leg DESC LIMIT 1",
                          (order_id,)).fetchone()
        if not conn.execute("SELECT 1 FROM exceptions WHERE order_id=? AND status='open'",
                            (order_id,)).fetchone():
            _auto_archive(conn, o, wb)
        return _order_now(conn, order_id, actor)


# ---------------------------------------------------------------- 7. 退回重寄（地址锁定后的唯一路径）
def request_reship(actor, order_id, payload, db_path=None):
    if actor["role"] != "student":
        raise PermissionError("仅学生可申请重寄")
    with tx(db_path) as conn:
        o = _get_order(conn, order_id)
        if actor.get("student_id") != o["student_id"]:
            raise PermissionError("只能申请本人转递单重寄")
        if o["status"] != "returned":
            raise ServiceError("仅“已退回待重寄”状态可申请重寄")
        pend = conn.execute("SELECT id FROM address_changes WHERE order_id=? AND kind='reship' AND status='pending'",
                            (order_id,)).fetchone()
        if pend:
            raise ServiceError("已有重寄申请待复核")
        cur = conn.execute(
            "INSERT INTO address_changes(order_id,new_org,new_addr,new_contact,new_phone,reason,kind,"
            "status,requested_by,created_at) VALUES(?,?,?,?,?,?,'reship','pending',?,?)",
            (order_id, payload.get("receiver_org", o["receiver_org"]).strip(),
             payload["receiver_addr"].strip(), payload.get("receiver_contact", o["receiver_contact"]).strip(),
             payload.get("receiver_phone", o["receiver_phone"]).strip(),
             payload.get("reason", "原单位拒收，申请退回重寄"), actor["id"], _now(conn)))
        _event(conn, o["id"], "student", actor, "退回件重寄申请（待院系复核新地址）",
               {"change_id": cur.lastrowid, "new_addr": payload["receiver_addr"]})
        return _order_now(conn, order_id, actor)


def review_reship(actor, change_id, approve, note="", db_path=None):
    """重寄地址由院系复核；复核通过即更新地址，档案室重新封装后可再次寄出（新运单腿）。"""
    if actor["role"] != "department":
        raise PermissionError("重寄地址须由院系复核")
    with tx(db_path) as conn:
        ch = conn.execute("SELECT * FROM address_changes WHERE id=?", (change_id,)).fetchone()
        if not ch or ch["kind"] != "reship":
            raise ServiceError("重寄申请不存在")
        if ch["status"] != "pending":
            raise ServiceError("该申请已复核")
        o = _get_order(conn, ch["order_id"])
        if o["status"] != "returned":
            raise ServiceError("转递单当前状态不允许复核重寄")
        if approve:
            conn.execute("UPDATE transfer_orders SET receiver_org=?,receiver_addr=?,receiver_contact=?,"
                         "receiver_phone=?,updated_at=? WHERE id=?",
                         (ch["new_org"], ch["new_addr"], ch["new_contact"], ch["new_phone"],
                          _now(conn), o["id"]))
            conn.execute("UPDATE address_changes SET status='approved',reviewed_by=?,review_note=?,reviewed_at=? WHERE id=?",
                         (actor["id"], note, _now(conn), change_id))
            _resolve_exc(conn, o["id"], "reject", actor, "学生已申请重寄并经复核，原拒收异常关闭", None)
            # 退回重寄沿用同一转递单（唯一性约束）；院系只复核地址，加封由档案室完成
            _set_status(conn, o["id"], "verified")
            _event(conn, o["id"], "department", actor, "重寄地址复核通过，待档案室重新加封",
                   {"new_org": ch["new_org"], "new_addr": ch["new_addr"]})
        else:
            conn.execute("UPDATE address_changes SET status='rejected',reviewed_by=?,review_note=?,reviewed_at=? WHERE id=?",
                         (actor["id"], note, _now(conn), change_id))
            _event(conn, o["id"], "department", actor, "重寄申请驳回", {"note": note})
        return _order_now(conn, o["id"], actor)


def repack(actor, order_id, db_path=None):
    """退回重寄前由档案室重新加封（新封签号），随后可再次寄出。"""
    if actor["role"] != "archive":
        raise PermissionError("仅档案室可重新加封")
    with tx(db_path) as conn:
        o = _get_order(conn, order_id)
        old = conn.execute("SELECT * FROM packing_lists WHERE order_id=?", (order_id,)).fetchone()
        if not old:
            raise ServiceError("缺少原封装记录")
        if o["status"] != "verified":
            raise ServiceError("仅“院系已核对（重寄地址已复核）”状态可重新加封")
        nleg = conn.execute("SELECT COALESCE(MAX(leg),0) l FROM waybills WHERE order_id=?",
                            (order_id,)).fetchone()["l"]
        new_seal = f"FQ{o['grad_batch']}-{o['id']:04d}-R{nleg + 1}"
        conn.execute("UPDATE packing_lists SET seal_no=?,packed_by=?,created_at=? WHERE order_id=?",
                     (new_seal, actor["id"], _now(conn), order_id))
        _set_status(conn, order_id, "packed")
        _event(conn, o["id"], "archive", actor, "退回件重新清点加封",
               {"old_seal": old["seal_no"], "new_seal": new_seal})
        return _order_now(conn, order_id, actor)


# ---------------------------------------------------------------- 查询
def _order_now(conn, order_id, actor):
    return _order_dict(conn, _get_order(conn, order_id), actor)


def get_order(order_id, actor, db_path=None):
    with connect(db_path) as conn:
        row = _get_order(conn, order_id)
        return _order_dict(conn, row, actor)


def get_order_by_student(student_id, actor, db_path=None):
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM transfer_orders WHERE student_id=? ORDER BY id DESC LIMIT 1",
                           (student_id,)).fetchone()
        return _order_dict(conn, row, actor) if row else None


def list_orders(actor, batch=None, status=None, db_path=None):
    if actor["role"] not in ("department", "archive", "approver"):
        raise PermissionError("仅工作人员可列表查询")
    q = "SELECT * FROM transfer_orders WHERE 1=1"
    args = []
    if batch:
        q += " AND grad_batch=?"; args.append(batch)
    if status:
        q += " AND status=?"; args.append(status)
    q += " ORDER BY grad_batch DESC, id"
    with connect(db_path) as conn:
        return [_order_dict(conn, r, actor, with_chain=False)
                for r in conn.execute(q, args).fetchall()]


def pending_changes(actor, kind="address", db_path=None):
    if actor["role"] != "department":
        raise PermissionError("仅院系可查看复核队列")
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT c.*, o.status order_status FROM address_changes c"
            " JOIN transfer_orders o ON o.id=c.order_id WHERE c.kind=? AND c.status='pending' ORDER BY c.id",
            (kind,)).fetchall()
        return [dict(r) for r in rows]


def pending_waivers(actor, db_path=None):
    if actor["role"] != "approver":
        raise PermissionError("仅审批人可查看豁免队列")
    with connect(db_path) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM missing_requests WHERE status='pending' ORDER BY id").fetchall()]

"""WSGI Web 层：路由、会话、三端页面处理器（学生 / 院系 / 档案室）。"""
import os
import re
import secrets
from urllib.parse import parse_qs

from . import domain, views
from .db import all_, now, one

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


class Resp:
    def __init__(self, body="", status="200 OK", headers=None, content_type="text/html; charset=utf-8"):
        self.body = body.encode("utf-8") if isinstance(body, str) else body
        self.status = status
        self.headers = headers or []
        if content_type:
            self.headers.append(("Content-Type", content_type))


def redirect(location):
    return Resp(b"", "303 See Other", [("Location", location)], None)


def ok(msg):
    from urllib.parse import quote
    return f"?msg={quote(msg)}&kind=ok"


def err(msg):
    from urllib.parse import quote
    return f"?msg={quote(msg)}&kind=err"


class Request:
    def __init__(self, environ, conn):
        self.environ = environ
        self.conn = conn
        self.method = environ["REQUEST_METHOD"]
        self.path = environ["PATH_INFO"]
        self.query = {k: v[0] for k, v in
                      parse_qs(environ.get("QUERY_STRING", "")).items()}
        self.form = {}
        if self.method == "POST":
            length = int(environ.get("CONTENT_LENGTH") or 0)
            body = environ["wsgi.input"].read(length).decode("utf-8")
            self.form = {k: v[0] for k, v in parse_qs(body).items()}
        self.cookies = {}
        for part in environ.get("HTTP_COOKIE", "").split(";"):
            if "=" in part:
                k, v = part.strip().split("=", 1)
                self.cookies[k] = v
        self.user = None
        token = self.cookies.get("sid")
        if token:
            row = one(conn, "SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id"
                      " WHERE s.token=?", (token,))
            self.user = row

    @property
    def msg(self):
        return self.query.get("msg", "")

    @property
    def kind(self):
        return self.query.get("kind", "ok")


# ---------------------------------------------------------------- 页面处理

def index(req):
    if not req.user:
        return redirect("/login")
    return redirect({"student": "/student", "department": "/dept",
                     "archivist": "/archive"}[req.user["role"]])


def login_get(req):
    users = all_(req.conn, "SELECT * FROM users ORDER BY role, id")
    return Resp(views.login_page(users, req.query.get("msg", "")))


def login_post(req):
    uid = req.form.get("user_id")
    if not one(req.conn, "SELECT id FROM users WHERE id=?", (uid,)):
        return redirect("/login" + err("用户不存在"))
    token = secrets.token_hex(16)
    req.conn.execute("INSERT INTO sessions(token, user_id, created_at) VALUES(?,?,?)",
                     (token, uid, now()))
    req.conn.commit()
    return Resp(b"", "303 See Other",
                [("Location", "/"), ("Set-Cookie", f"sid={token}; Path=/; HttpOnly")],
                None)


def logout(req):
    token = req.cookies.get("sid")
    if token:
        req.conn.execute("DELETE FROM sessions WHERE token=?", (token,))
        req.conn.commit()
    return Resp(b"", "303 See Other",
                [("Location", "/login"), ("Set-Cookie", "sid=; Path=/; Max-Age=0")], None)


def static_css(req):
    with open(os.path.join(STATIC_DIR, "style.css"), "rb") as f:
        return Resp(f.read(), content_type="text/css; charset=utf-8")


# ---------------------------------------------------------------- 学生端

def _student_bundle(conn, student_id):
    orders = all_(conn, """
        SELECT o.*, b.name AS batch_name FROM transfer_orders o
        JOIN batches b ON b.id=o.batch_id
        WHERE o.student_id=? AND o.status!='cancelled'
        ORDER BY CASE o.status WHEN 'archived' THEN 1 ELSE 0 END, o.id DESC""",
        (student_id,))
    bundle = {"orders": orders, "exceptions": {}, "waybills": {}, "changes": {},
              "materials": {}, "audit": {},
              "student": domain.get_student(conn, student_id)}
    for o in orders:
        oid = o["id"]
        bundle["exceptions"][oid] = all_(
            conn, "SELECT * FROM exceptions WHERE order_id=? AND status='open'"
            " ORDER BY id DESC", (oid,))
        wbs = all_(conn, "SELECT * FROM waybills WHERE order_id=? ORDER BY seq", (oid,))
        wbs = [dict(w) for w in wbs]
        for w in wbs:
            w["events"] = all_(conn, "SELECT * FROM tracking_events WHERE waybill_id=?"
                               " ORDER BY id", (w["id"],))
        bundle["waybills"][oid] = wbs
        bundle["changes"][oid] = all_(
            conn, "SELECT * FROM address_changes WHERE order_id=? ORDER BY id DESC", (oid,))
        bundle["materials"][oid] = all_(
            conn, "SELECT * FROM materials WHERE order_id=? ORDER BY id", (oid,))
        bundle["audit"][oid] = all_(
            conn, "SELECT * FROM audit_log WHERE order_id=? ORDER BY id DESC LIMIT 30",
            (oid,))
    return bundle


def student_home(req):
    bundle = _student_bundle(req.conn, req.user["id"])
    active_batches = {o["batch_id"] for o in bundle["orders"]}
    batches_open = all_(req.conn, "SELECT * FROM batches ORDER BY id")
    batches_open = [b for b in batches_open if b["id"] not in active_batches]
    return Resp(views.student_page(req.user, bundle, batches_open, req.msg, req.kind))


def student_new_order(req):
    f = req.form
    try:
        oid = domain.create_order(req.conn, req.user, f.get("batch_id"),
                                  f.get("recv_unit", ""), f.get("recv_address", ""),
                                  f.get("recv_person", ""), f.get("recv_phone", ""),
                                  f.get("materials_note", ""))
        return redirect("/student" + ok(f"草稿已保存（转递单 #{oid}），请核对后提交"))
    except domain.DomainError as e:
        return redirect("/student" + err(str(e)))


def _own_order(req, oid):
    order = domain.get_order(req.conn, oid)
    if order["student_id"] != req.user["id"]:
        raise domain.DomainError("只能操作本人的转递单")
    return order


def student_submit(req, oid):
    try:
        _own_order(req, oid)
        domain.submit_order(req.conn, oid, req.user)
        return redirect("/student" + ok("已提交，等待院系核对"))
    except domain.DomainError as e:
        return redirect("/student" + err(str(e)))


def student_edit(req, oid):
    f = req.form
    try:
        _own_order(req, oid)
        domain.update_draft(req.conn, oid, req.user, f.get("recv_unit", ""),
                            f.get("recv_address", ""), f.get("recv_person", ""),
                            f.get("recv_phone", ""), f.get("materials_note", ""))
        return redirect("/student" + ok("修改已保存"))
    except domain.DomainError as e:
        return redirect("/student" + err(str(e)))


def student_cancel(req, oid):
    try:
        _own_order(req, oid)
        domain.cancel_order(req.conn, oid, req.user)
        return redirect("/student" + ok("转递单已作废"))
    except domain.DomainError as e:
        return redirect("/student" + err(str(e)))


def student_addr_change(req, oid):
    f = req.form
    try:
        _own_order(req, oid)
        domain.request_address_change(req.conn, oid, req.user, f.get("new_unit", ""),
                                      f.get("new_address", ""), f.get("new_person", ""),
                                      f.get("new_phone", ""), f.get("reason", ""))
        return redirect("/student" + ok("地址更正申请已提交，待院系复核"))
    except domain.DomainError as e:
        return redirect("/student" + err(str(e)))


def student_material(req, oid, materials_dir):
    f = req.form
    try:
        _own_order(req, oid)
        domain.upload_material(req.conn, oid, req.user, f.get("title", ""),
                               f.get("content", ""), materials_dir)
        return redirect("/student" + ok("材料已上传（内容已遮罩存储）"))
    except domain.DomainError as e:
        return redirect("/student" + err(str(e)))


def material_view(req, mid):
    try:
        m, content = domain.read_material(req.conn, mid, req.user)
        return Resp(views.material_view(req.user, m, content))
    except domain.DomainError as e:
        return redirect("/" + err(str(e)))


# ---------------------------------------------------------------- 院系端

def dept_home(req):
    to_verify = all_(req.conn, """
        SELECT o.*, b.name AS batch_name, u.display_name, s.student_no, s.grad_status
        FROM transfer_orders o
        JOIN batches b ON b.id=o.batch_id
        JOIN users u ON u.id=o.student_id
        JOIN students s ON s.user_id=u.id
        WHERE o.status='submitted' ORDER BY o.id""")
    changes = all_(req.conn, """
        SELECT c.*, o.order_no, u.display_name FROM address_changes c
        JOIN transfer_orders o ON o.id=c.order_id
        JOIN users u ON u.id=o.student_id
        WHERE c.status='pending' ORDER BY c.id""")
    exemptions = all_(req.conn, """
        SELECT x.*, o.order_no, u.display_name, e.detail FROM exemptions x
        JOIN transfer_orders o ON o.id=x.order_id
        JOIN users u ON u.id=o.student_id
        JOIN exceptions e ON e.id=x.exception_id
        WHERE x.status='pending' ORDER BY x.id""")
    done = all_(req.conn, """
        SELECT o.*, u.display_name, s.student_no FROM transfer_orders o
        JOIN users u ON u.id=o.student_id
        JOIN students s ON s.user_id=u.id
        WHERE o.status NOT IN ('draft','submitted','cancelled')
        ORDER BY o.updated_at DESC LIMIT 10""")
    return Resp(views.dept_page(req.user, to_verify, changes, exemptions, done,
                                req.msg, req.kind))


def dept_verify(req, oid):
    try:
        domain.dept_verify(req.conn, oid, req.user,
                           req.form.get("approve") == "1", req.form.get("note", ""))
        return redirect("/dept" + ok("核对完成"))
    except domain.DomainError as e:
        return redirect("/dept" + err(str(e)))


def dept_addr_change(req, cid):
    try:
        domain.review_address_change(req.conn, cid, req.user,
                                     req.form.get("approve") == "1")
        return redirect("/dept" + ok("复核完成"))
    except domain.DomainError as e:
        return redirect("/dept" + err(str(e)))


def dept_exemption(req, xid):
    try:
        domain.decide_exemption(req.conn, xid, req.user, req.form.get("approve") == "1")
        return redirect("/dept" + ok("审批完成"))
    except domain.DomainError as e:
        return redirect("/dept" + err(str(e)))


# ---------------------------------------------------------------- 档案室端

def archive_home(req):
    batches = all_(req.conn, "SELECT * FROM batches ORDER BY id")
    by_batch = []
    for b in batches:
        rows = all_(req.conn, """
            SELECT o.*, u.display_name, s.student_no,
              (SELECT COUNT(*) FROM exceptions e WHERE e.order_id=o.id
                AND e.status='open') AS open_exc,
              (SELECT w.tracking_no FROM waybills w WHERE w.order_id=o.id
                ORDER BY w.seq DESC LIMIT 1) AS tracking_no
            FROM transfer_orders o
            JOIN users u ON u.id=o.student_id
            JOIN students s ON s.user_id=u.id
            WHERE o.batch_id=? AND o.status!='cancelled'
            ORDER BY o.seal_no IS NULL, o.seal_no, o.id""", (b["id"],))
        by_batch.append((b["name"], rows))
    open_excs = all_(req.conn, """
        SELECT e.*, o.order_no, u.display_name FROM exceptions e
        JOIN transfer_orders o ON o.id=e.order_id
        JOIN users u ON u.id=o.student_id
        WHERE e.status='open' ORDER BY e.id DESC""")
    return Resp(views.archive_page(req.user, by_batch, open_excs, req.msg, req.kind))


def _order_detail(conn, oid):
    o = domain.get_order(conn, oid)
    d = {"order": o, "student": domain.get_student(conn, o["student_id"])}
    d["batch_name"] = one(conn, "SELECT name FROM batches WHERE id=?",
                          (o["batch_id"],))["name"]
    wbs = [dict(w) for w in all_(conn, "SELECT * FROM waybills WHERE order_id=?"
                                 " ORDER BY seq", (oid,))]
    for w in wbs:
        w["events"] = all_(conn, "SELECT * FROM tracking_events WHERE waybill_id=?"
                           " ORDER BY id", (w["id"],))
    d["waybills"] = wbs
    excs = all_(conn, "SELECT * FROM exceptions WHERE order_id=? ORDER BY id DESC", (oid,))
    d["open_excs"] = [e for e in excs if e["status"] == "open"]
    d["resolved_excs"] = [e for e in excs if e["status"] == "resolved"]
    d["pending_change"] = one(conn, "SELECT id FROM address_changes WHERE order_id=?"
                              " AND status='pending'", (oid,))
    d["materials"] = all_(conn, "SELECT * FROM materials WHERE order_id=? ORDER BY id",
                          (oid,))
    d["audit"] = all_(conn, """
        SELECT a.*, COALESCE(u.display_name, '系统/物流') AS actor
        FROM audit_log a LEFT JOIN users u ON u.id=a.actor_id
        WHERE a.order_id=? ORDER BY a.id DESC LIMIT 50""", (oid,))
    if o["sealed_by"]:
        d["sealed_by"] = one(conn, "SELECT display_name FROM users WHERE id=?",
                             (o["sealed_by"],))["display_name"]
    else:
        d["sealed_by"] = None
    return d


def archive_order(req, oid):
    d = _order_detail(req.conn, oid)
    return Resp(views.archive_order_page(req.user, d, req.msg, req.kind))


def _back(req, oid, result):
    return redirect(f"/archive/order/{oid}" + result)


def archive_seal(req, oid):
    try:
        seal_no = domain.seal_order(req.conn, oid, req.user)
        return _back(req, oid, ok(f"封装完成，封签号 {seal_no}"))
    except domain.DomainError as e:
        return _back(req, oid, err(str(e)))


def archive_ship(req, oid):
    try:
        domain.ship_order(req.conn, oid, req.user)
        return _back(req, oid, ok("已寄出，地址锁定"))
    except domain.DomainError as e:
        return _back(req, oid, err(str(e)))


def archive_archive(req, oid):
    try:
        domain.archive_order(req.conn, oid, req.user)
        return _back(req, oid, ok("已签收归档，转递链闭环"))
    except domain.DomainError as e:
        return _back(req, oid, err(str(e)))


def archive_advance(req):
    results = domain.advance_logistics(req.conn)
    if not results:
        return redirect("/archive" + ok("没有可推进的在途运单"))
    msgs = [f"{wb['tracking_no']}：{ev['description']}" for wb, ev in results]
    return redirect("/archive" + ok("；".join(msgs)))


def archive_return(req, wid):
    try:
        wb = one(req.conn, "SELECT * FROM waybills WHERE id=?", (wid,))
        domain.initiate_return(req.conn, wid, req.user, req.form.get("reason", ""))
        return _back(req, wb["order_id"], ok("已启动退回"))
    except domain.DomainError as e:
        return redirect("/archive" + err(str(e)))


def archive_returned(req, wid):
    try:
        wb = one(req.conn, "SELECT * FROM waybills WHERE id=?", (wid,))
        domain.confirm_returned(req.conn, wid, req.user)
        return _back(req, wb["order_id"], ok("退件已入库，可执行退回重寄"))
    except domain.DomainError as e:
        return redirect("/archive" + err(str(e)))


def archive_resend(req, oid):
    f = req.form
    try:
        domain.resend(req.conn, oid, req.user, f.get("new_unit", ""),
                      f.get("new_address", ""), f.get("new_person", ""),
                      f.get("new_phone", ""))
        return _back(req, oid, ok("已生成重寄运单，退件链已更新"))
    except domain.DomainError as e:
        return _back(req, oid, err(str(e)))


def archive_reseal(req, oid):
    try:
        new_seal = domain.reseal(req.conn, oid, req.user)
        return _back(req, oid, ok(f"已重新封装，新封签号 {new_seal}"))
    except domain.DomainError as e:
        return _back(req, oid, err(str(e)))


def archive_flag_exception(req, oid):
    try:
        domain.flag_exception(req.conn, oid, req.user, req.form.get("type", ""),
                              req.form.get("detail", ""))
        return _back(req, oid, ok("异常已登记"))
    except domain.DomainError as e:
        return _back(req, oid, err(str(e)))


def archive_supplement(req, eid):
    try:
        exc = one(req.conn, "SELECT * FROM exceptions WHERE id=?", (eid,))
        domain.supplement_missing_pages(req.conn, eid, req.user)
        return _back(req, exc["order_id"], ok("缺页已补齐，异常闭环"))
    except domain.DomainError as e:
        return redirect("/archive" + err(str(e)))


def archive_exemption(req, eid):
    exc = one(req.conn, "SELECT * FROM exceptions WHERE id=?", (eid,))
    try:
        if not exc:
            raise domain.DomainError("异常单不存在")
        domain.request_exemption(req.conn, eid, req.user, req.form.get("reason", ""))
        return _back(req, exc["order_id"], ok("缺页豁免已申请，待院系审批"))
    except domain.DomainError as e:
        return _back(req, exc["order_id"], err(str(e))) if exc else \
            redirect("/archive" + err(str(e)))


def archive_identity(req, eid):
    exc = one(req.conn, "SELECT * FROM exceptions WHERE id=?", (eid,))
    try:
        if not exc:
            raise domain.DomainError("异常单不存在")
        domain.resolve_name_confusion(req.conn, eid, req.user,
                                      req.form.get("student_no", ""),
                                      req.form.get("id_tail", ""))
        return _back(req, exc["order_id"], ok("身份核验通过，混淆解除"))
    except domain.DomainError as e:
        return _back(req, exc["order_id"], err(str(e))) if exc else \
            redirect("/archive" + err(str(e)))


def archive_urge(req, wid):
    try:
        wb = one(req.conn, "SELECT * FROM waybills WHERE id=?", (wid,))
        domain.urge_shipment(req.conn, wid, req.user)
        return _back(req, wb["order_id"], ok("已催办，运单恢复运输"))
    except domain.DomainError as e:
        return redirect("/archive" + err(str(e)))


# ---------------------------------------------------------------- 路由

ROUTES = [
    ("GET", r"^/$", index, None),
    ("GET", r"^/login$", login_get, None),
    ("POST", r"^/login$", login_post, None),
    ("POST", r"^/logout$", logout, None),
    ("GET", r"^/static/style\.css$", static_css, None),
    ("GET", r"^/student$", student_home, "student"),
    ("POST", r"^/student/order/new$", student_new_order, "student"),
    ("POST", r"^/student/order/(\d+)/submit$", student_submit, "student"),
    ("POST", r"^/student/order/(\d+)/edit$", student_edit, "student"),
    ("POST", r"^/student/order/(\d+)/cancel$", student_cancel, "student"),
    ("POST", r"^/student/order/(\d+)/address-change$", student_addr_change, "student"),
    ("POST", r"^/student/order/(\d+)/material$", student_material, "student"),
    ("GET", r"^/student/material/(\d+)$", material_view, "student"),
    ("GET", r"^/dept$", dept_home, "department"),
    ("POST", r"^/dept/verify/(\d+)$", dept_verify, "department"),
    ("POST", r"^/dept/address-change/(\d+)$", dept_addr_change, "department"),
    ("POST", r"^/dept/exemption/(\d+)$", dept_exemption, "department"),
    ("GET", r"^/archive$", archive_home, "archivist"),
    ("GET", r"^/archive/order/(\d+)$", archive_order, "archivist"),
    ("POST", r"^/archive/seal/(\d+)$", archive_seal, "archivist"),
    ("POST", r"^/archive/ship/(\d+)$", archive_ship, "archivist"),
    ("POST", r"^/archive/archive/(\d+)$", archive_archive, "archivist"),
    ("POST", r"^/archive/advance$", archive_advance, "archivist"),
    ("POST", r"^/archive/return/(\d+)$", archive_return, "archivist"),
    ("POST", r"^/archive/returned/(\d+)$", archive_returned, "archivist"),
    ("POST", r"^/archive/resend/(\d+)$", archive_resend, "archivist"),
    ("POST", r"^/archive/reseal/(\d+)$", archive_reseal, "archivist"),
    ("POST", r"^/archive/exception/(\d+)$", archive_flag_exception, "archivist"),
    ("POST", r"^/archive/supplement/(\d+)$", archive_supplement, "archivist"),
    ("POST", r"^/archive/exemption/(\d+)$", archive_exemption, "archivist"),
    ("POST", r"^/archive/identity/(\d+)$", archive_identity, "archivist"),
    ("POST", r"^/archive/urge/(\d+)$", archive_urge, "archivist"),
    ("GET", r"^/archive/material/(\d+)$", material_view, "archivist"),
]
COMPILED = [(m, re.compile(p), h, role) for m, p, h, role in ROUTES]


def make_app(db_path, materials_dir):
    from .db import connect

    def app(environ, start_response):
        conn = connect(db_path)
        try:
            req = Request(environ, conn)
            for method, pattern, handler, role in COMPILED:
                if method != req.method:
                    continue
                m = pattern.match(req.path)
                if not m:
                    continue
                if role and (not req.user or req.user["role"] != role):
                    resp = redirect("/login" + err("请登录或切换身份"))
                    break
                args = [int(g) for g in m.groups()]
                if handler is student_material:
                    resp = handler(req, *args, materials_dir)
                else:
                    resp = handler(req, *args)
                break
            else:
                resp = Resp(views.layout("404", req.user,
                                         '<div class="card"><h3>页面不存在</h3></div>'),
                            "404 Not Found")
            resp.headers.append(("Content-Length", str(len(resp.body))))
            start_response(resp.status, resp.headers)
            return [resp.body]
        finally:
            conn.close()

    return app

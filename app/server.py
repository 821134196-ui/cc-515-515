"""零依赖 HTTP 服务：JSON API + 静态前端。

身份用简单的 X-User-Id 头/查询参数切换演示账号（本地演示，不做密码）。
"""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from . import config, proofs as proofs_mod, seed as seed_mod, service
from .db import connect, init_db

ROLES = ("student", "department", "archive", "approver")


class ApiError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code


def actor_from_qs(qs):
    uid = qs.get("as", [""])[0]
    if not uid:
        raise ApiError(401, "缺少身份（?as=账号 或 X-User-Id 头）")
    with connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not row:
        raise ApiError(401, "账号不存在")
    return {"id": row["id"], "name": row["name"], "role": row["role"],
            "student_id": row["student_id"]}


# (method, 路径片段) → 处理函数(payload, qs, actor, ids)
def api_me(payload, qs, actor):
    return actor


def api_students_me(payload, qs, actor):
    if actor["role"] != "student":
        raise ApiError(403, "仅学生有本人档案")
    return service.get_order_by_student(actor["student_id"], actor) or {"order": None}


def api_orders(payload, qs, actor):
    if qs.get("batch") or qs.get("status") or actor["role"] != "student":
        return {"orders": service.list_orders(
            actor, qs.get("batch", [None])[0], qs.get("status", [None])[0])}
    o = service.get_order_by_student(actor["student_id"], actor)
    return {"orders": [o] if o else []}


def api_order_detail(payload, qs, actor, ids):
    return service.get_order(int(ids[0]), actor)


def api_declare(payload, qs, actor):
    return service.declare(actor, payload)


def api_verify(payload, qs, actor, ids):
    return service.verify_graduation(actor, int(ids[0]), bool(payload["confirmed"]),
                                     payload.get("note", ""))


def api_addr_request(payload, qs, actor, ids):
    return service.request_address_change(actor, int(ids[0]), payload)


def api_addr_review(payload, qs, actor, ids):
    return service.review_address_change(actor, int(ids[0]), bool(payload["approve"]),
                                         payload.get("note", ""))


def api_pack(payload, qs, actor, ids):
    return service.pack(actor, int(ids[0]))


def api_repack(payload, qs, actor, ids):
    return service.repack(actor, int(ids[0]))


def api_ship(payload, qs, actor, ids):
    return service.ship(actor, int(ids[0]), payload.get("scenario", "normal"))


def api_tick(payload, qs, actor):
    n = int(qs.get("n", ["1"])[0])
    changed = []
    for _ in range(max(1, n)):
        changed += service.advance_logistics()
    return {"changed": changed}


def api_exception(payload, qs, actor, ids):
    return service.open_exception(actor, int(ids[0]), payload["type"], payload.get("note", ""),
                                  payload.get("waybill_id"))


def api_urge(payload, qs, actor, ids):
    return service.urge_shipment(actor, int(ids[0]))


def api_recall(payload, qs, actor, ids):
    return service.recall_shipment(actor, int(ids[0]))


def api_seal_check(payload, qs, actor, ids):
    return service.verify_seal_on_return(actor, int(ids[0]), bool(payload["intact"]),
                                         payload.get("note", ""))


def api_waiver_request(payload, qs, actor, ids):
    return service.request_missing_page_waiver(
        actor, int(ids[0]), payload["materials"], payload.get("reason", ""))


def api_waiver_review(payload, qs, actor, ids):
    return service.review_missing_waiver(actor, int(ids[0]), bool(payload["approve"]),
                                         payload.get("note", ""))


def api_confusion(payload, qs, actor, ids):
    return service.resolve_confusion(actor, int(ids[0]),
                                     str(payload["id_card_tail"]), str(payload["student_no"]))


def api_reship_request(payload, qs, actor, ids):
    return service.request_reship(actor, int(ids[0]), payload)


def api_reship_review(payload, qs, actor, ids):
    return service.review_reship(actor, int(ids[0]), bool(payload["approve"]),
                                 payload.get("note", ""))


def api_changes(payload, qs, actor):
    return {"changes": service.pending_changes(actor, qs.get("kind", ["address"])[0])}


def api_waivers(payload, qs, actor):
    return {"waivers": service.pending_waivers(actor)}


def api_proofs_list(payload, qs, actor):
    raw = qs.get("student_id", [actor.get("student_id")])[0]
    sid = int(raw) if raw not in (None, "") else actor.get("student_id")
    return {"proofs": proofs_mod.list_proofs(sid, actor)}


def api_proof_detail(payload, qs, actor, ids):
    return proofs_mod.read_proof(int(ids[0]), actor)


def api_reference(payload, qs, actor):
    """前端下拉用：学生名册（不含完整身份证号）。"""
    with connect() as conn:
        rows = conn.execute("SELECT id,student_no,name,college,major,grad_batch FROM students ORDER BY id").fetchall()
    return {"students": [dict(r) for r in rows], "scenarios": list(config.SHIPPING_SCENARIOS)}


ROUTES = [
    ("GET", "/api/me", api_me, False),
    ("GET", "/api/my-order", api_students_me, False),
    ("GET", "/api/orders", api_orders, False),
    ("GET", "/api/orders/", api_order_detail, True),
    ("POST", "/api/orders/declare", api_declare, False),
    ("POST", "/api/orders/", lambda p, q, a, i: _sub_route(p, q, a, i), True),
    ("GET", "/api/changes", api_changes, False),
    ("GET", "/api/waivers", api_waivers, False),
    ("POST", "/api/address-changes/", api_addr_review, True),
    ("POST", "/api/reship-changes/", api_reship_review, True),
    ("POST", "/api/waivers/", api_waiver_review, True),
    ("GET", "/api/proofs", api_proofs_list, False),
    ("GET", "/api/proofs/", api_proof_detail, True),
    ("POST", "/api/logistics/tick", api_tick, False),
    ("GET", "/api/reference", api_reference, False),
]

SUB_ACTIONS = {
    "verify": api_verify, "address": api_addr_request, "pack": api_pack, "repack": api_repack,
    "ship": api_ship, "exception": api_exception, "urge": api_urge, "recall": api_recall,
    "seal-check": api_seal_check, "waiver": api_waiver_request,
    "confusion-check": api_confusion, "reship": api_reship_request,
}


def _sub_route(payload, qs, actor, ids):
    action = qs.get("action", [""])[0]
    fn = SUB_ACTIONS.get(action)
    if not fn:
        raise ApiError(404, f"未知操作：{action}")
    return fn(payload, qs, actor, ids)


def dispatch(method, path, query, payload, actor):
    for m, prefix, fn, takes_ids in ROUTES:
        if method != m or not path.startswith(prefix):
            continue
        rest = path[len(prefix):].strip("/")
        if takes_ids:
            parts = [p for p in rest.split("/") if p != ""]
            if not parts or not parts[0].isdigit():
                continue
            return fn(payload, query, actor, parts)
        if rest:
            continue
        return fn(payload, query, actor)
    raise ApiError(404, f"接口不存在：{method} {path}")


class Handler(BaseHTTPRequestHandler):
    server_version = "ArchiveTransfer/1.0"

    def log_message(self, fmt, *args):
        pass  # 静默；演示时保持控制台干净

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_static(self, path):
        rel = path.lstrip("/") or "index.html"
        f = (config.STATIC_DIR / rel).resolve()
        if not str(f).startswith(str(config.STATIC_DIR.resolve())) or not f.is_file():
            f = config.STATIC_DIR / "index.html"
        ctype = {"html": "text/html; charset=utf-8", "css": "text/css; charset=utf-8",
                 "js": "application/javascript; charset=utf-8"}.get(f.suffix.lstrip("."),
                                                                    "application/octet-stream")
        self._send(200, f.read_bytes(), ctype)

    def _handle_api(self, method):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        try:
            actor = actor_from_qs(query) if method == "GET" else self._actor_from_header()
            payload = {}
            if method == "POST":
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    payload = json.loads(raw or b"{}")
                except json.JSONDecodeError:
                    raise ApiError(400, "请求体不是合法 JSON")
            result = dispatch(method, parsed.path, query, payload, actor)
            self._send(200, {"ok": True, "data": result})
        except ApiError as e:
            self._send(e.code, {"ok": False, "error": str(e)})
        except PermissionError as e:
            self._send(403, {"ok": False, "error": str(e)})
        except service.ServiceError as e:
            self._send(409, {"ok": False, "error": str(e)})
        except FileNotFoundError as e:
            self._send(404, {"ok": False, "error": str(e)})
        except Exception as e:  # noqa: BLE001
            self._send(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})

    def _actor_from_header(self):
        uid = self.headers.get("X-User-Id", "")
        if not uid:
            raise ApiError(401, "缺少 X-User-Id 头")
        with connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if not row:
            raise ApiError(401, "账号不存在")
        return {"id": row["id"], "name": row["name"], "role": row["role"],
                "student_id": row["student_id"]}

    def do_GET(self):
        if self.path.startswith("/api/"):
            self._handle_api("GET")
        else:
            self._serve_static(urlparse(self.path).path)

    def do_POST(self):
        self._handle_api("POST")


def serve(host=None, port=None):
    init_db()
    httpd = ThreadingHTTPServer((host or config.HOST, port or config.PORT), Handler)
    print(f"档案转递服务已启动：http://{config.HOST}:{config.PORT}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    serve()

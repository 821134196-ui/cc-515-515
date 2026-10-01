"""毕业生档案转递：核心规则测试。

覆盖：
- 身份匹配（同名学生，身份证后四位 + 学号双因子）
- 地址锁定（寄出前更正须复核；寄出后只能退回重寄）
- 退件重寄（拒收→退回→重寄，原运单与新运单同单并排、封签号更新）
- 职责分离（院系不可见敏感材料；档案室不能自批缺页豁免）
另含：脱敏落盘、唯一有效转递单、五类异常剧本与 HTTP 接口冒烟。
"""
import json
import tempfile
import unittest
from pathlib import Path

from app import config, proofs as proofs_mod, seed as seed_mod, service, shipping
from app.db import connect, init_db

DEPT = {"id": "dept01", "role": "department", "name": "王教务"}
ARCH = {"id": "arch01", "role": "archive", "name": "刘档案"}
APPR = {"id": "appr01", "role": "approver", "name": "周审批"}


class FlowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name)
        cls.db = cls.root / "test.db"
        cls.proof_dir = cls.root / "proofs"
        config.DATA_DIR = cls.root
        config.DB_PATH = cls.db
        config.PROOF_DIR = cls.proof_dir
        init_db(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        # 每个用例一个全新库
        self.db.unlink(missing_ok=True)
        init_db(self.db)
        self._sid = {}
        with connect(self.db) as c:
            c.execute("INSERT INTO users(id,name,role) VALUES('dept01','王教务','department')")
            c.execute("INSERT INTO users(id,name,role) VALUES('arch01','刘档案','archive')")
            c.execute("INSERT INTO users(id,name,role) VALUES('appr01','周审批','approver')")
            for uid, name, no, idc in (
                    ("s_lin", "林一", "2021010001", "110101200305122318"),
                    ("s_lin2", "林一", "2021020002", "320102200308154521"),  # 同名学生
                    ("s_sun", "孙二", "2021010003", "330106200302201240")):
                cur = c.execute(
                    "INSERT INTO students(student_no,name,id_card,phone,college,major,grad_batch)"
                    " VALUES(?,?,?, '13800000000','测试学院','测试专业','2025')", (no, name, idc))
                sid = cur.lastrowid
                self._sid[uid] = sid
                c.execute("INSERT INTO users(id,name,role,student_id) VALUES(?,?, 'student',?)",
                          (uid, name, sid))
            c.commit()

    def stu(self, uid):
        return {"id": uid, "role": "student", "student_id": self._sid[uid]}

    def declare(self, uid="s_lin", org="某公司人事部"):
        return service.declare(self.stu(uid), {
            "receiver_org": org, "receiver_addr": "某市某区1号",
            "receiver_contact": "张三", "receiver_phone": "010-88888888"}, self.db)["id"]

    def to_verified(self, uid="s_lin"):
        oid = self.declare(uid)
        service.verify_graduation(DEPT, oid, True, db_path=self.db)
        return oid

    def to_packed(self, uid="s_lin"):
        oid = self.to_verified(uid)
        service.pack(ARCH, oid, db_path=self.db)
        return oid

    def to_shipped(self, uid="s_lin", scenario="normal"):
        oid = self.to_packed(uid)
        service.ship(ARCH, oid, scenario, db_path=self.db)
        return oid

    # ---------------------------------------------------------- 1. 身份匹配
    def test_same_name_identity_match(self):
        # 两名都叫"林一"的学生，身份证后四位与学号不同
        oid = self.to_shipped("s_lin", "confusion")
        for _ in range(4):
            service.advance_logistics(self.db)
        o = service.get_order(oid, ARCH, self.db)
        self.assertEqual(o["status"], "exception")
        self.assertIn("confusion", [e["type"] for e in o["open_exceptions"]])

        # 错的后四位 / 错的学号都必须被拒（防止同名混淆）
        with self.assertRaises(service.ServiceError):
            service.resolve_confusion(ARCH, oid, "0000", "2021010001", self.db)
        with self.assertRaises(service.ServiceError):
            service.resolve_confusion(ARCH, oid, "2318", "2021020002", self.db)
        # 学生/院系无权核对
        with self.assertRaises(PermissionError):
            service.resolve_confusion(self.stu("s_lin"), oid, "2318", "2021010001", self.db)
        with self.assertRaises(PermissionError):
            service.resolve_confusion(DEPT, oid, "2318", "2021010001", self.db)
        # 双因子一致才确认归档
        o = service.resolve_confusion(ARCH, oid, "2318", "2021010001", self.db)
        self.assertEqual(o["status"], "archived")

    # ---------------------------------------------------------- 2. 地址锁定
    def test_address_change_must_be_reviewed_before_ship(self):
        oid = self.to_verified()
        # 学生申请更正
        o = service.request_address_change(self.stu("s_lin"), oid, {
            "receiver_addr": "新地址99号", "reason": "换单位"}, self.db)
        change = [c for c in o["address_changes"] if c["kind"] == "address"][0]
        self.assertEqual(change["status"], "pending")
        # 未复核前地址不变
        self.assertEqual(o["receiver_addr"], "某市某区1号")
        # 学生自己不能复核，档案室也不能（必须院系）
        with self.assertRaises(PermissionError):
            service.review_address_change(self.stu("s_lin"), change["id"], True, db_path=self.db)
        with self.assertRaises(PermissionError):
            service.review_address_change(ARCH, change["id"], True, db_path=self.db)
        # 院系复核通过后地址才生效
        o = service.review_address_change(DEPT, change["id"], True, "已与新单位核实", self.db)
        self.assertEqual(o["receiver_addr"], "新地址99号")

    def test_address_locked_after_pack_or_ship(self):
        # 封装后地址锁定：更正申请即使存在也不能通过
        oid = self.to_packed()
        with self.assertRaises(service.ServiceError):
            service.request_address_change(self.stu("s_lin"), oid,
                                           {"receiver_addr": "来不及了"}, self.db)
        # 寄出后只能走退回重寄
        oid2 = self.to_shipped("s_sun", "refuse")
        with self.assertRaises(service.ServiceError):
            service.request_address_change(self.stu("s_sun"), oid2,
                                           {"receiver_addr": "寄出后改地址"}, self.db)
        # 物流跑完拒收剧本
        for _ in range(5):
            service.advance_logistics(self.db)
        self.assertEqual(service.get_order(oid2, ARCH, self.db)["status"], "returned")

    # ---------------------------------------------------------- 3. 退件重寄
    def test_reject_return_and_reship_chain(self):
        oid = self.to_shipped("s_lin", "refuse")
        first_no = service.get_order(oid, ARCH, self.db)["waybills"][0]["tracking_no"]
        for _ in range(5):
            service.advance_logistics(self.db)
        o = service.get_order(oid, ARCH, self.db)
        self.assertEqual(o["status"], "returned")
        self.assertEqual(o["waybills"][0]["status"], "returned")
        self.assertIn("reject", [e["type"] for e in o["open_exceptions"]])

        # 学生申请重寄 → 院系复核 → 档案室加封 → 再寄（同一转递单、新运单腿）
        o = service.request_reship(self.stu("s_lin"), oid, {
            "receiver_org": "新人才中心", "receiver_addr": "人才路8号",
            "receiver_contact": "窗口", "receiver_phone": "021-66666666"}, self.db)
        change = [c for c in o["address_changes"] if c["kind"] == "reship"][0]
        # 未复核不能加封
        with self.assertRaises(service.ServiceError):
            service.repack(ARCH, oid, db_path=self.db)
        service.review_reship(DEPT, change["id"], True, db_path=self.db)
        # 加封是档案室的动作，院系不能代劳
        with self.assertRaises(PermissionError):
            service.repack(DEPT, oid, db_path=self.db)
        service.repack(ARCH, oid, db_path=self.db)
        o = service.ship(ARCH, oid, "normal", self.db)
        legs = o["waybills"]
        self.assertEqual(len(legs), 2)                      # 原运单 + 重寄运单并排
        self.assertEqual(legs[0]["leg"], 1)
        self.assertEqual(legs[1]["leg"], 2)
        self.assertTrue(legs[1]["tracking_no"].endswith("R2"))
        self.assertEqual(legs[0]["tracking_no"], first_no)  # 原运单号保留
        self.assertNotEqual(legs[0]["tracking_no"], legs[1]["tracking_no"])
        self.assertRegex(o["packing"]["seal_no"], r"-R2$") # 新封签号
        for _ in range(4):
            service.advance_logistics(self.db)
        o = service.get_order(oid, ARCH, self.db)
        self.assertEqual(o["status"], "archived")
        self.assertEqual([w["status"] for w in o["waybills"]], ["returned", "delivered"])

    def test_unique_valid_order_per_batch(self):
        self.declare()
        with self.assertRaises(service.ServiceError):
            self.declare()
        # 核对不通过作废后允许重新申报
        oid = service.get_order_by_student(self._sid["s_lin"], self.stu("s_lin"), self.db)["id"]
        service.verify_graduation(DEPT, oid, False, "资格不符", self.db)
        new_id = self.declare()
        self.assertNotEqual(oid, new_id)

    # ---------------------------------------------------------- 4. 职责分离
    def test_department_cannot_see_sensitive_materials(self):
        oid = self.to_packed()
        dept_view = service.get_order(oid, DEPT, self.db)
        self.assertEqual(dept_view["packing"]["materials"][0]["name"],
                         "（已封装材料，详情仅档案室可见）")
        self.assertIsNone(dept_view["student"]["id_card_full"])
        arch_view = service.get_order(oid, ARCH, self.db)
        self.assertTrue(any("成绩单" in m["name"] for m in arch_view["packing"]["materials"]))
        self.assertTrue(arch_view["student"]["id_card_full"].endswith("2318"))
        # 院系不能调接口看脱敏证明材料
        with self.assertRaises(PermissionError):
            proofs_mod.list_proofs(self._sid["s_lin"], DEPT, self.db)

    def test_archive_cannot_self_approve_missing_waiver(self):
        oid = self.to_shipped("s_lin", "damage")
        for _ in range(5):
            service.advance_logistics(self.db)
        service.verify_seal_on_return(ARCH, oid, False, "内封断裂", self.db)
        o = service.request_missing_page_waiver(
            ARCH, oid, ["成绩单"], "补制中", self.db)
        rid = o["waivers"][-1]["id"]
        # 档案室不能批准自己发起的豁免（角色级禁止）
        with self.assertRaises(PermissionError):
            service.review_missing_waiver(ARCH, rid, True, self.db)
        # 学生/院系也不行
        with self.assertRaises(PermissionError):
            service.review_missing_waiver(self.stu("s_lin"), rid, True, self.db)
        with self.assertRaises(PermissionError):
            service.review_missing_waiver(DEPT, rid, True, self.db)
        # 学校审批人可以批准，之后无其他异常即可重寄
        o = service.review_missing_waiver(APPR, rid, True, "同意", self.db)
        self.assertEqual(o["status"], "returned")

    # ---------------------------------------------------------- 5. 异常剧本与脱敏
    def test_stagnant_urge_and_recall(self):
        oid = self.to_shipped("s_lin", "stagnant")
        # 揽收 + 分拣 两跳正常，再推进后登记滞留
        service.advance_logistics(self.db)
        service.advance_logistics(self.db)
        self.assertNotIn("stagnant", [e["type"] for e in
                         service.get_order(oid, ARCH, self.db)["open_exceptions"]])
        service.advance_logistics(self.db)
        service.advance_logistics(self.db)
        self.assertIn("stagnant", [e["type"] for e in
                      service.get_order(oid, ARCH, self.db)["open_exceptions"]])
        service.urge_shipment(ARCH, oid, self.db)
        service.advance_logistics(self.db)
        service.advance_logistics(self.db)
        self.assertEqual(service.get_order(oid, ARCH, self.db)["status"], "archived")

    def test_masked_proof_on_disk(self):
        _, masked = proofs_mod.save_proof(
            self._sid["s_lin"], "接收证明",
            "林一身份证110101200305122318手机13812345678，建行卡6222021234567890123。",
            "s_lin", self.db)
        self.assertNotIn("20030512", masked)        # 出生日期段被遮罩
        self.assertIn("2318", masked)               # 后四位保留
        self.assertNotIn("12345678", masked)
        self.assertIn("5678", masked)
        # 磁盘上的文件也不含明文
        f = next(self.proof_dir.glob("*.txt"))
        disk = f.read_text(encoding="utf-8")
        self.assertNotIn("110101200305122318", disk)
        self.assertNotIn("13812345678", disk)

    def test_shipping_scenarios_table(self):
        self.assertEqual(shipping.advance("normal", 3)[0], "delivered")
        self.assertEqual(shipping.advance("refuse", 2)[0], "rejected")
        self.assertEqual(shipping.advance("refuse", 2)[3], "reject")
        self.assertTrue(shipping.advance("stagnant", 3)[4])  # 滞留告警
        self.assertIsNone(shipping.advance("normal", 99))    # 终态不再推进


class SeedSmokeTest(unittest.TestCase):
    """播种脚本必须覆盖五类异常场景且拒收样例就绪。"""
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        config.DATA_DIR = root
        config.DB_PATH = root / "s.db"
        config.PROOF_DIR = root / "proofs"
        cls.orders = seed_mod.seed(config.DB_PATH)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_seed_states(self):
        with connect(config.DB_PATH) as c:
            types = {r["type"] for r in c.execute("SELECT DISTINCT type FROM exceptions")}
        self.assertEqual(types, {"reject", "seal_damaged", "missing_page", "stagnant", "confusion"})
        o = service.get_order(self.orders["lixue"], ARCH, config.DB_PATH)
        self.assertEqual(o["status"], "returned")
        self.assertEqual(o["waybills"][0]["scenario"], "refuse")
        # 同名两人都有数据且身份证尾号不同
        a = service.get_order(self.orders["zhanglei_a"], ARCH, config.DB_PATH)
        b = service.get_order(self.orders["zhanglei_b"], ARCH, config.DB_PATH)
        self.assertEqual(a["student"]["name"], b["student"]["name"])
        self.assertNotEqual(a["student"]["id_card_tail"], b["student"]["id_card_tail"])


class HttpSmokeTest(unittest.TestCase):
    """HTTP 层：权限状态码与拒收样例接口。"""
    @classmethod
    def setUpClass(cls):
        import threading
        from http.server import ThreadingHTTPServer
        from app import server

        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        config.DATA_DIR = root
        config.DB_PATH = root / "h.db"
        config.PROOF_DIR = root / "proofs"
        seed_mod.seed(config.DB_PATH)
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls._tmp.cleanup()

    def url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    def test_permissions_and_reship_over_http(self):
        import urllib.request
        import urllib.error

        def call(method, path, uid=None, body=None):
            headers = {"Content-Type": "application/json"}
            if uid and method == "POST":
                headers["X-User-Id"] = uid
            if uid and method == "GET":
                path += ("&" if "?" in path else "?") + "as=" + uid
            data = json.dumps(body).encode() if body is not None else None
            req = urllib.request.Request(self.url(path), data=data, headers=headers, method=method)
            try:
                with urllib.request.urlopen(req) as r:
                    return r.status, json.loads(r.read())
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read())

        code, d = call("GET", "/api/my-order", "lixue")
        self.assertEqual(code, 200)
        self.assertEqual(d["data"]["status"], "returned")
        oid = d["data"]["id"]

        # 未认证
        code, _ = call("GET", "/api/orders")
        self.assertEqual(code, 401)
        # 档案室不能自批豁免
        code, _ = call("POST", "/api/waivers/1", "arch01", {"approve": True})
        self.assertEqual(code, 403)
        # 院系不能看证明材料
        code, _ = call("GET", "/api/proofs", "dept01")
        self.assertEqual(code, 403)
        # 学生不能替别人核对
        code, _ = call("POST", f"/api/orders/{oid}/verify?action=verify",
                       "lixue", {"confirmed": True})
        self.assertEqual(code, 403)
        # 拒收重寄链走通
        code, d = call("POST", f"/api/orders/{oid}/reship?action=reship", "lixue",
                       {"receiver_org": "浙江省人才市场", "receiver_addr": "古翠路50号",
                        "receiver_contact": "窗口", "receiver_phone": "05718805555"})
        self.assertEqual(code, 200)
        cid = d["data"]["address_changes"][-1]["id"]
        code, _ = call("POST", f"/api/reship-changes/{cid}", "dept01", {"approve": True})
        self.assertEqual(code, 200)
        code, _ = call("POST", f"/api/orders/{oid}/repack?action=repack", "arch01", {})
        self.assertEqual(code, 200)
        code, d = call("POST", f"/api/orders/{oid}/ship?action=ship", "arch01",
                       {"scenario": "normal"})
        self.assertEqual(code, 200)
        code, _ = call("POST", "/api/logistics/tick", "arch01", {})
        self.assertEqual(code, 200)


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""核心规则测试：身份匹配、地址锁定、退件重寄、职责分离。

运行：python3 -m unittest discover -s tests -v
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from archive_app import domain  # noqa: E402
from archive_app.db import connect, init_db, one  # noqa: E402
from archive_app.masking import mask_text  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.conn = connect(":memory:")
        init_db(self.conn)
        c = self.conn
        self.batch = c.execute(
            "INSERT INTO batches(name, year) VALUES('2026届夏季毕业批次', 2026)").lastrowid
        self.batch2 = c.execute(
            "INSERT INTO batches(name, year) VALUES('2026届冬季毕业批次', 2026)").lastrowid
        self.stu_a = self._student("wangfang1", "王芳", "20260103",
                                   "110101200201031234")
        self.stu_b = self._student("wangfang2", "王芳", "20260104",
                                   "110101200201049876")  # 同名不同人
        self.dept = self._user("chenjing", "陈静", "department")
        self.arc1 = self._user("zhaolan", "赵岚", "archivist")
        self.arc2 = self._user("sunhang", "孙航", "archivist")
        c.commit()

    def tearDown(self):
        self.conn.close()

    def _user(self, username, name, role):
        uid = self.conn.execute(
            "INSERT INTO users(username, display_name, role) VALUES(?,?,?)",
            (username, name, role)).lastrowid
        return self.conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()

    def _student(self, username, name, student_no, id_number, grad="已毕业"):
        u = self._user(username, name, "student")
        self.conn.execute(
            "INSERT INTO students(user_id, student_no, id_number, grad_status)"
            " VALUES(?,?,?,?)", (u["id"], student_no, id_number, grad))
        return self.conn.execute("SELECT * FROM users WHERE id=?",
                                 (u["id"],)).fetchone()

    def _order_to(self, student, stage="sealed", batch=None, script="normal"):
        """把一份转递单推进到指定阶段。"""
        oid = domain.create_order(self.conn, student, batch or self.batch,
                                  "某市人才服务中心", "某市某路 1 号",
                                  "接收人", "13800000000", "报到证")
        if stage == "draft":
            return oid
        domain.submit_order(self.conn, oid, student)
        if stage == "submitted":
            return oid
        domain.dept_verify(self.conn, oid, self.dept, True)
        if stage == "dept_verified":
            return oid
        domain.seal_order(self.conn, oid, self.arc1)
        if stage == "sealed":
            return oid
        domain.ship_order(self.conn, oid, self.arc1, script=script)
        return oid


class TestIdentityMatch(Base):
    """身份匹配：同名学生档案混淆必须通过 姓名+学号+身份证后四位 核验。"""

    def test_identity_match_ok(self):
        stu = domain.get_student(self.conn, self.stu_a["id"])
        self.assertTrue(domain.identity_match(stu, "王芳", "20260103", "1234"))

    def test_identity_match_same_name_wrong_student(self):
        stu_a = domain.get_student(self.conn, self.stu_a["id"])
        # 同名学生王芳B的学号/身份证尾号不能通过王芳A的核验
        self.assertFalse(domain.identity_match(stu_a, "王芳", "20260104", "9876"))
        self.assertFalse(domain.identity_match(stu_a, "王芳", "20260103", "9876"))
        self.assertFalse(domain.identity_match(stu_a, "王芳", "20260103", "123"))

    def test_name_confusion_blocks_seal_and_ship(self):
        oid = self._order_to(self.stu_a, "dept_verified")
        domain.flag_exception(self.conn, oid, self.arc1, "name_confusion",
                              "同批次同名学生 王芳(20260104)")
        with self.assertRaises(domain.DomainError):
            domain.seal_order(self.conn, oid, self.arc1)

    def test_name_confusion_resolve_requires_correct_identity(self):
        oid = self._order_to(self.stu_a, "dept_verified")
        exc = domain.flag_exception(self.conn, oid, self.arc1, "name_confusion",
                                    "同批次同名学生")
        # 用同名学生B的学号核验 → 失败
        with self.assertRaises(domain.DomainError):
            domain.resolve_name_confusion(self.conn, exc, self.arc1, "20260104", "9876")
        # 用正确学号但错误尾号 → 失败
        with self.assertRaises(domain.DomainError):
            domain.resolve_name_confusion(self.conn, exc, self.arc1, "20260103", "0000")
        # 正确身份 → 解除，可继续封装寄出
        domain.resolve_name_confusion(self.conn, exc, self.arc1, "20260103", "1234")
        domain.seal_order(self.conn, oid, self.arc1)
        domain.ship_order(self.conn, oid, self.arc1)
        self.assertEqual(domain.get_order(self.conn, oid)["status"], "shipped")


class TestAddressLock(Base):
    """地址锁定：寄出前变更须院系复核；寄出后只能退回重寄。"""

    def test_change_reviewed_and_applied_before_ship(self):
        oid = self._order_to(self.stu_a, "sealed")
        domain.request_address_change(self.conn, oid, self.stu_a,
                                      "新单位", "新地址 2 号", "新接收人", "13900000000",
                                      "单位搬迁")
        # 待复核期间不得寄出
        with self.assertRaises(domain.DomainError):
            domain.ship_order(self.conn, oid, self.arc1)
        domain.review_address_change(self.conn, 1, self.dept, True)
        order = domain.get_order(self.conn, oid)
        self.assertEqual(order["recv_address"], "新地址 2 号")
        domain.ship_order(self.conn, oid, self.arc1)  # 复核通过后可寄出

    def test_change_rejected_after_ship(self):
        oid = self._order_to(self.stu_a, "sealed")
        domain.ship_order(self.conn, oid, self.arc1)
        with self.assertRaisesRegex(domain.DomainError, "退回重寄"):
            domain.request_address_change(self.conn, oid, self.stu_a,
                                          "新单位", "新地址", "新人", "13900000000")

    def test_pending_change_auto_rejected_if_shipped(self):
        oid = self._order_to(self.stu_a, "sealed")
        domain.request_address_change(self.conn, oid, self.stu_a,
                                      "新单位", "新地址", "新人", "13900000000")
        # 复核前不能寄出；先拒绝申请再寄出，再验证"已寄出复核必驳回"
        domain.review_address_change(self.conn, 1, self.dept, False)
        domain.ship_order(self.conn, oid, self.arc1)
        # 寄出后学生不能再申请（上一条用例），此处验证复核接口对寄出单的兜底
        ch = self.conn.execute(
            "INSERT INTO address_changes(order_id, new_unit, new_address, new_person,"
            " new_phone, requested_by, created_at) VALUES(?,?,?,?,?,?,datetime('now'))",
            (oid, "X", "Y", "Z", "1", self.stu_a["id"])).lastrowid
        with self.assertRaisesRegex(domain.DomainError, "地址锁定"):
            domain.review_address_change(self.conn, ch, self.dept, True)
        self.assertEqual(one(self.conn, "SELECT status FROM address_changes WHERE id=?",
                             (ch,))["status"], "rejected")


class TestReturnResend(Base):
    """退件重寄：拒收 → 退回 → 入库 → 重寄新运单，退件链完整。"""

    def test_full_return_resend_chain(self):
        oid = self._order_to(self.stu_a, "sealed")
        wb = domain.ship_order(self.conn, oid, self.arc1, script="rejected")
        for _ in range(3):
            domain.advance_logistics(self.conn, wb)
        self.assertEqual(one(self.conn, "SELECT status FROM waybills WHERE id=?",
                             (wb,))["status"], "active")
        domain.advance_logistics(self.conn, wb)  # 第 4 步：拒收
        row = one(self.conn, "SELECT status FROM waybills WHERE id=?", (wb,))
        self.assertEqual(row["status"], "returning")
        exc = one(self.conn, "SELECT * FROM exceptions WHERE order_id=? AND type='rejected'",
                  (oid,))
        self.assertIsNotNone(exc)
        # 退件未入库前不能重寄
        with self.assertRaises(domain.DomainError):
            domain.resend(self.conn, oid, self.arc1, "新单位", "新地址 9 号",
                          "新人", "13911112222")
        domain.confirm_returned(self.conn, wb, self.arc1)
        wb2 = domain.resend(self.conn, oid, self.arc1, "新单位", "新地址 9 号",
                            "新人", "13911112222")
        wbs = self.conn.execute(
            "SELECT * FROM waybills WHERE order_id=? ORDER BY seq", (oid,)).fetchall()
        self.assertEqual(len(wbs), 2)
        self.assertEqual(wbs[1]["id"], wb2)
        self.assertEqual(wbs[1]["parent_id"], wb)          # 退件链挂接
        self.assertEqual(wbs[1]["seq"], 2)
        self.assertEqual(wbs[1]["recv_address"], "新地址 9 号")
        # 拒收异常随重寄闭环，转递单地址已更正
        self.assertEqual(one(self.conn, "SELECT status FROM exceptions WHERE id=?",
                             (exc["id"],))["status"], "resolved")
        self.assertEqual(domain.get_order(self.conn, oid)["recv_address"], "新地址 9 号")
        # 重寄运单可正常签收归档
        for _ in range(4):
            domain.advance_logistics(self.conn, wb2)
        self.assertEqual(domain.get_order(self.conn, oid)["status"], "signed")
        domain.archive_order(self.conn, oid, self.arc1)
        self.assertEqual(domain.get_order(self.conn, oid)["status"], "archived")

    def test_seal_damaged_reseal_before_resend(self):
        oid = self._order_to(self.stu_a, "sealed")
        old_seal = domain.get_order(self.conn, oid)["seal_no"]
        wb = domain.ship_order(self.conn, oid, self.arc1)
        domain.initiate_return(self.conn, wb, self.arc1, "封签破损")
        domain.flag_exception(self.conn, oid, self.arc1, "seal_damaged",
                              "退件验收发现封签破损", waybill_id=wb)
        new_seal = domain.reseal(self.conn, oid, self.arc1)
        self.assertNotEqual(new_seal, old_seal)
        self.assertTrue(new_seal.startswith(old_seal.split("-")[0]))
        domain.confirm_returned(self.conn, wb, self.arc1)
        domain.resend(self.conn, oid, self.arc1, "某市人才服务中心", "某市某路 1 号",
                      "接收人", "13800000000")
        order = domain.get_order(self.conn, oid)
        self.assertEqual(order["seal_no"], new_seal)

    def test_stalled_urge_resumes(self):
        oid = self._order_to(self.stu_a, "sealed")
        wb = domain.ship_order(self.conn, oid, self.arc1, script="stalled")
        for _ in range(3):
            domain.advance_logistics(self.conn, wb)  # 揽收/运输/滞留
        self.assertIsNotNone(one(
            self.conn, "SELECT id FROM exceptions WHERE order_id=? AND type='stalled'"
            " AND status='open'", (oid,)))
        # 滞留中不推进
        self.assertEqual(domain.advance_logistics(self.conn, wb), [])
        domain.urge_shipment(self.conn, wb, self.arc1)
        results = domain.advance_logistics(self.conn, wb)
        self.assertEqual(len(results), 1)  # 催办后恢复推进


class TestSeparationOfDuties(Base):
    """职责分离：院系不见袋内材料；档案室不能批准自己的缺页豁免；
    一名学生同一批次只允许一份有效转递单。"""

    def test_department_cannot_view_materials(self):
        oid = self._order_to(self.stu_a, "submitted")
        with tempfile.TemporaryDirectory() as d:
            domain.upload_material(self.conn, oid, self.stu_a, "报到证",
                                   "身份证号 110101200201031234", d)
            with self.assertRaisesRegex(domain.DomainError, "院系不得查看"):
                domain.list_materials_for_view(self.conn, oid, self.dept)
            # 学生本人与档案室可见（内容为遮罩版）
            self.assertEqual(len(domain.list_materials_for_view(
                self.conn, oid, self.stu_a)), 1)
            self.assertEqual(len(domain.list_materials_for_view(
                self.conn, oid, self.arc1)), 1)

    def test_material_stored_masked(self):
        oid = self._order_to(self.stu_a, "draft")
        with tempfile.TemporaryDirectory() as d:
            mid = domain.upload_material(
                self.conn, oid, self.stu_a, "报到证",
                "身份证号 110101200201031234，电话 13812345678", d)
            m, content = domain.read_material(self.conn, mid, self.arc1)
            self.assertNotIn("110101200201031234", content)
            self.assertNotIn("13812345678", content)
            self.assertIn("110101**********1234", content)
            self.assertIn("138****5678", content)
            with open(m["stored_path"], encoding="utf-8") as f:
                self.assertNotIn("110101200201031234", f.read())

    def test_exemption_requires_department_approval(self):
        oid = self._order_to(self.stu_a, "dept_verified")
        exc = domain.flag_exception(self.conn, oid, self.arc1, "missing_pages",
                                    "学位材料第 2 页缺失")
        xid = domain.request_exemption(self.conn, exc, self.arc1, "留存联可佐证")
        # 档案室（含非申请人的另一名档案员）无权批准
        with self.assertRaises(domain.DomainError):
            domain.decide_exemption(self.conn, xid, self.arc1, True)
        with self.assertRaises(domain.DomainError):
            domain.decide_exemption(self.conn, xid, self.arc2, True)
        # 院系批准后缺页异常闭环，可继续封装
        domain.decide_exemption(self.conn, xid, self.dept, True)
        self.assertEqual(one(self.conn, "SELECT status FROM exceptions WHERE id=?",
                             (exc,))["status"], "resolved")
        domain.seal_order(self.conn, oid, self.arc1)

    def test_exemption_reject_keeps_exception_open(self):
        oid = self._order_to(self.stu_a, "dept_verified")
        exc = domain.flag_exception(self.conn, oid, self.arc1, "missing_pages", "缺页")
        xid = domain.request_exemption(self.conn, exc, self.arc1, "理由")
        domain.decide_exemption(self.conn, xid, self.dept, False)
        self.assertEqual(one(self.conn, "SELECT status FROM exceptions WHERE id=?",
                             (exc,))["status"], "open")
        with self.assertRaises(domain.DomainError):
            domain.seal_order(self.conn, oid, self.arc1)  # 缺页未闭环不得封装
        domain.supplement_missing_pages(self.conn, exc, self.arc1)
        domain.seal_order(self.conn, oid, self.arc1)

    def test_one_active_order_per_batch(self):
        self._order_to(self.stu_a, "submitted")
        with self.assertRaisesRegex(domain.DomainError, "同一毕业批次"):
            domain.create_order(self.conn, self.stu_a, self.batch,
                                "另一单位", "另一地址", "某人", "13700000000")
        # 不同批次不受限
        domain.create_order(self.conn, self.stu_a, self.batch2,
                            "另一单位", "另一地址", "某人", "13700000000")

    def test_cancel_frees_the_slot(self):
        oid = self._order_to(self.stu_a, "draft")
        domain.cancel_order(self.conn, oid, self.stu_a)
        oid2 = domain.create_order(self.conn, self.stu_a, self.batch,
                                   "另一单位", "另一地址", "某人", "13700000000")
        self.assertNotEqual(oid, oid2)

    def test_role_guards(self):
        oid = self._order_to(self.stu_a, "submitted")
        with self.assertRaises(domain.DomainError):
            domain.dept_verify(self.conn, oid, self.arc1, True)   # 档案室不能核对
        with self.assertRaises(domain.DomainError):
            domain.seal_order(self.conn, oid, self.dept)          # 院系不能封装
        with self.assertRaises(domain.DomainError):
            domain.ship_order(self.conn, oid, self.stu_a)         # 学生不能寄出

    def test_grad_status_blocks_verify(self):
        stu = self._student("liuyan", "刘研", "20260199",
                            "110101200201990099", grad="结业")
        oid = self._order_to(stu, "submitted")
        with self.assertRaisesRegex(domain.DomainError, "结业"):
            domain.dept_verify(self.conn, oid, self.dept, True)


class TestMasking(unittest.TestCase):
    def test_mask_text(self):
        out = mask_text("证号 110101200201011234 手机 13812345678")
        self.assertEqual(out, "证号 110101**********1234 手机 138****5678")


if __name__ == "__main__":
    unittest.main()

"""演示数据：全部通过 domain 层函数写入，保证转递链（audit_log）完整。

包含开箱即用的样例：
- 张三：已寄出、物流脚本为「拒收」——推进物流即可看到接收单位拒收样例；
- 李四：已完成一次退回重寄，退件链与原运单并排展示；
- 王芳(20260103) 与 王芳(20260104)：同批次同名学生，前者挂同名混淆异常；
- 王芳(20260104)：待院系复核的地址变更申请；
- 钱六：材料缺页异常 + 档案室已发起的缺页豁免（待院系审批）；
- 赵五：一份草稿（夏季批次）+ 一份已签收归档（冬季批次）。
"""
from . import domain

DEMO_ID_TAILS = {"wangfang1": "1234", "wangfang2": "9876"}


def _user(conn, username, name, role, dept=""):
    cur = conn.execute(
        "INSERT INTO users(username, display_name, role, dept_name) VALUES(?,?,?,?)",
        (username, name, role, dept))
    return cur.lastrowid


def _student(conn, username, name, student_no, id_number, cls, grad="已毕业"):
    uid = _user(conn, username, name, "student")
    conn.execute(
        "INSERT INTO students(user_id, student_no, id_number, class_name, grad_status)"
        " VALUES(?,?,?,?,?)",
        (uid, student_no, id_number, cls, grad))
    return uid


def seed(conn, materials_dir):
    b1 = conn.execute(
        "INSERT INTO batches(name, year) VALUES('2026届夏季毕业批次', 2026)").lastrowid
    b2 = conn.execute(
        "INSERT INTO batches(name, year) VALUES('2026届冬季毕业批次', 2026)").lastrowid

    zhangsan = _student(conn, "zhangsan", "张三", "20260101",
                        "110101200201010011", "计算机2201")
    lisi = _student(conn, "lisi", "李四", "20260102",
                    "110101200201020022", "计算机2201")
    wangfang1 = _student(conn, "wangfang1", "王芳", "20260103",
                         "110101200201031234", "软件工程2201")
    wangfang2 = _student(conn, "wangfang2", "王芳", "20260104",
                         "110101200201049876", "软件工程2202")
    zhaowu = _student(conn, "zhaowu", "赵五", "20260105",
                      "110101200201050055", "网络工程2201")
    qianliu = _student(conn, "qianliu", "钱六", "20260106",
                       "110101200201060066", "计算机2202")
    chenjing = _user(conn, "chenjing", "陈静", "department", "计算机学院")
    zhaolan = _user(conn, "zhaolan", "赵岚", "archivist", "校档案室")
    _user(conn, "sunhang", "孙航", "archivist", "校档案室")
    conn.commit()

    def U(uid):
        return conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()

    stu = {uid: U(uid) for uid in
           (zhangsan, lisi, wangfang1, wangfang2, zhaowu, qianliu)}
    dept, arc = U(chenjing), U(zhaolan)

    def declare(student, batch, unit, addr, person, phone, note):
        oid = domain.create_order(conn, stu[student], batch, unit, addr, person,
                                  phone, note)
        domain.submit_order(conn, oid, stu[student])
        return oid

    # 1) 张三：拒收样例 —— 已寄出，物流脚本 rejected，已推进 2 个节点
    o1 = declare(zhangsan, b1, "湖北省人才交流中心（拒收样例）",
                 "武汉市武昌区中南路 99 号", "周接收", "13800000001",
                 "报到证、学位授予材料、成绩单")
    domain.dept_verify(conn, o1, dept, True)
    domain.seal_order(conn, o1, arc)
    wb1 = domain.ship_order(conn, o1, arc, script="rejected")
    domain.advance_logistics(conn, wb1)
    domain.advance_logistics(conn, wb1)
    domain.upload_material(conn, o1, stu[zhangsan], "报到证扫描件",
                           "报到证编号 2026BJ0088，持证人张三，"
                           "身份证号 110101200201010011，联系电话 13800000001",
                           materials_dir)

    # 2) 李四：退回重寄完整链 —— 原运单被拒收退回，新运单运输中
    o2 = declare(lisi, b1, "广州市天河区人才服务办公室",
                 "广州市天河区体育西路 120 号", "吴接收", "13800000002",
                 "报到证、户口迁移证")
    domain.dept_verify(conn, o2, dept, True)
    domain.seal_order(conn, o2, arc)
    wb2 = domain.ship_order(conn, o2, arc, script="rejected")
    for _ in range(4):
        domain.advance_logistics(conn, wb2)          # 直至拒收
    domain.confirm_returned(conn, wb2, arc)
    wb2b = domain.resend(conn, o2, arc, "广州市天河区人才服务办公室",
                         "广州市天河区体育西路 120 号 3 楼 302 室",
                         "吴接收", "13800000002")
    domain.advance_logistics(conn, wb2b)

    # 3) 王芳(20260103)：已封装，同名混淆异常挂起（与 20260104 王芳 同批次同名）
    o3 = declare(wangfang1, b1, "深圳市南山区人力资源服务中心",
                 "深圳市南山区深南大道 9668 号", "林接收", "13800000003",
                 "报到证、成绩单")
    domain.dept_verify(conn, o3, dept, True)
    domain.seal_order(conn, o3, arc)
    domain.flag_exception(conn, o3, arc, "name_confusion",
                          "同批次存在同名学生 王芳(20260104)，寄出前须身份核验")

    # 4) 王芳(20260104)：已提交院系核对，另有待复核的地址变更申请
    o4 = declare(wangfang2, b1, "杭州市西湖区人才管理服务中心",
                 "杭州市西湖区文三路 199 号", "沈接收", "13800000004",
                 "报到证")
    domain.request_address_change(conn, o4, stu[wangfang2],
                                  "杭州市西湖区人才管理服务中心",
                                  "杭州市西湖区文三路 199 号 2 号楼 501",
                                  "沈接收", "13800000004",
                                  "接收单位搬迁，地址更新")

    # 5) 赵五：夏季批次草稿 + 冬季批次已签收归档
    domain.create_order(conn, stu[zhaowu], b1, "成都市锦江区人才交流中心",
                        "成都市锦江区红星路三段 1 号", "何接收", "13800000005",
                        "报到证、成绩单")
    o5b = declare(zhaowu, b2, "成都市锦江区人才交流中心",
                  "成都市锦江区红星路三段 1 号", "何接收", "13800000005",
                  "报到证、成绩单")
    domain.dept_verify(conn, o5b, dept, True)
    domain.seal_order(conn, o5b, arc)
    wb5 = domain.ship_order(conn, o5b, arc, script="normal")
    for _ in range(4):
        domain.advance_logistics(conn, wb5)
    domain.archive_order(conn, o5b, arc)

    # 6) 钱六：材料缺页异常 + 档案室已申请缺页豁免（待院系审批）
    o6 = declare(qianliu, b1, "西安市雁塔区人才服务中心",
                 "西安市雁塔区小寨东路 66 号", "马接收", "13800000006",
                 "报到证、学位授予材料")
    domain.dept_verify(conn, o6, dept, True)
    exc = domain.flag_exception(conn, o6, arc, "missing_pages",
                                "学位授予材料第 2 页缺失，学生短期内无法补齐")
    domain.request_exemption(conn, exc, arc,
                             "学校学位委员会留存联可佐证，申请豁免该页")

    conn.commit()
    return {"batch1": b1, "batch2": b2}

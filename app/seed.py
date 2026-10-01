"""初始化演示数据库：学生、账号、各节点上的转递单与五类异常样例。

重点样例：
- 李雪（lixue）：接收单位拒收 → 已退回档案室，等待学生申请重寄【拒收样例】
- 孙琪（sunqi）：封签破损退回 → 缺页豁免待学校审批人批准【职责分离】
- 赵敏（zhaomin）：物流滞留，档案室可催办或召回
- 张磊 ×2（zhanglei_a / zhanglei_b）：同名学生，凭身份证后四位+学号核对
"""
import json
import shutil

from . import config, proofs as proofs_mod, service
from .db import connect, init_db

STUDENTS = [
    # (登录账号, 姓名, 学号, 身份证号, 手机, 学院, 专业)
    ("wangchen", "王晨", "2021001001", "110101200305122318", "13800000001", "计算机学院", "软件工程"),
    ("zhanglei_a", "张磊", "2021001002", "320102200308154521", "13800000002", "计算机学院", "网络工程"),
    ("zhanglei_b", "张磊", "2021002003", "44030520031107883X", "13800000003", "经济管理学院", "会计学"),
    ("lixue", "李雪", "2021001004", "330106200302201240", "13800000004", "外国语学院", "英语"),
    ("zhaomin", "赵敏", "2021001005", "510104200307095512", "13800000005", "机械工程学院", "机械设计"),
    ("sunqi", "孙琪", "2021001006", "320506200309213328", "13800000006", "文学院", "汉语言文学"),
    ("chenjie", "陈杰", "2021001007", "610113200304187745", "13800000007", "电子信息学院", "通信工程"),
    ("liuyang", "刘洋", "2021001008", "420106200312036633", "13800000008", "数学与统计学院", "信息与计算科学"),
    ("zhouting", "周婷", "2021001009", "350203200306254427", "13800000009", "法学院", "法学"),
    ("wuqiang", "吴强", "2021001010", "120103200310112216", "13800000010", "材料学院", "高分子材料"),
    ("zhengshuang", "郑爽", "2021001011", "53010220030908334X", "13800000011", "新闻学院", "新闻学"),
    ("fengyuan", "冯远", "2021001012", "210102200307075518", "13800000012", "建筑学院", "城乡规划"),
]

RECEIVERS = {
    "wangchen": ("北京星河科技有限公司人事部", "北京市海淀区中关村软件园8号", "周敏", "01082660101"),
    "zhanglei_a": ("腾讯科技（深圳）有限公司人事档案中心", "深圳市南山区科技园科兴科学园", "林芳", "075586013388"),
    "zhanglei_b": ("上海浦东发展银行人力资源部", "上海市浦东新区世纪大道1200号", "许杰", "02161618888"),
    "lixue": ("杭州恒信翻译有限公司综合办", "杭州市西湖区文三路90号", "高峰", "057188225566"),
    "zhaomin": ("东方电气集团人力资源部", "成都市高新区天府大道北段666号", "黄磊", "02887580000"),
    "sunqi": ("江苏凤凰教育出版社人事科", "南京市鼓楼区中山北路110号", "钱雅", "02583201122"),
    "chenjie": ("广州视源电子科技股份有限公司人力资源部", "广州市天河区珠江新城黄埔大道中333号", "何俊", "02087060099"),
    "liuyang": ("武汉市人才服务中心", "武汉市江岸区车站路1号", "档案窗口", "02782835555"),
    "zhouting": ("厦门市思明区人民法院政治部", "厦门市思明区湖滨北路110号", "蒋律师", "05925300011"),
    "wuqiang": ("中材高新材料股份有限公司人力部", "淄博市高新区鲁泰大道51号", "孟伟", "05333980022"),
    "zhengshuang": ("云南日报报业集团人事处", "昆明市西山区日新中路516号", "杨敏", "087164140033"),
    "fengyuan": ("大连市城市规划设计研究院人事办", "大连市西岗区唐山街24号", "梁工", "041183620077"),
}

DEPT = {"id": "dept01", "role": "department", "name": "王教务"}
ARCHIVE = {"id": "arch01", "role": "archive", "name": "刘档案"}
APPROVER = {"id": "appr01", "role": "approver", "name": "周审批"}


def reset():
    if config.DB_PATH.exists():
        config.DB_PATH.unlink()
    if config.PROOF_DIR.exists():
        shutil.rmtree(config.PROOF_DIR)
    init_db()


def seed(db_path=None):
    if db_path is None:
        reset()
        db_path = config.DB_PATH
    else:
        from .db import init_db
        init_db(db_path)

    with connect(db_path) as conn:
        for uid, name, no, idc, phone, college, major in STUDENTS:
            cur = conn.execute(
                "INSERT INTO students(student_no,name,id_card,phone,college,major,grad_batch)"
                " VALUES(?,?,?,?,?,?,'2025')",
                (no, name, idc, phone, college, major))
            sid = cur.lastrowid
            conn.execute("INSERT INTO users(id,name,role,student_id) VALUES(?,?, 'student',?)",
                         (uid, name, sid))
        conn.execute("INSERT INTO users(id,name,role) VALUES('dept01','王教务','department')")
        conn.execute("INSERT INTO users(id,name,role) VALUES('arch01','刘档案','archive')")
        conn.execute("INSERT INTO users(id,name,role) VALUES('appr01','周审批','approver')")
        conn.commit()
        sid_of = {uid: conn.execute("SELECT student_id FROM users WHERE id=?", (uid,)).fetchone()["student_id"]
                  for uid, *_ in STUDENTS}

    def stu(uid):
        return {"id": uid, "role": "student", "student_id": sid_of[uid]}

    orders = {}
    for uid, *_ in STUDENTS:
        org, addr, contact, phone = RECEIVERS[uid]
        o = service.declare(stu(uid),
                            {"receiver_org": org, "receiver_addr": addr,
                             "receiver_contact": contact, "receiver_phone": phone}, db_path)
        orders[uid] = o["id"]

    # 刘洋：仅申报，院系尚未核对
    for uid in ("wangchen", "zhanglei_a", "zhanglei_b", "lixue", "zhaomin", "sunqi",
                "chenjie", "zhouting", "wuqiang", "zhengshuang", "fengyuan"):
        service.verify_graduation(DEPT, orders[uid], True, "毕业资格与学籍信息核对一致", db_path)

    # 周婷：已申请更正接收地址，待院系复核（寄出前）
    service.request_address_change(stu("zhouting"), orders["zhouting"], {
        "receiver_org": "厦门市中级人民法院政治部",
        "receiver_addr": "厦门市思明区湖滨北路123号",
        "receiver_contact": "蒋律师", "receiver_phone": "05925300099",
        "reason": "签约单位由区法院调整为市中级法院"}, db_path)

    # 封装（刘洋/周婷保持未封装）
    for uid in ("wangchen", "zhanglei_a", "zhanglei_b", "lixue", "zhaomin",
                "sunqi", "chenjie", "wuqiang", "fengyuan"):
        service.pack(ARCHIVE, orders[uid], db_path=db_path)

    # 寄出一批：拒收/破损/滞留/混淆/正常签收 五种剧本同时跑
    service.ship(ARCHIVE, orders["lixue"], "refuse", db_path)
    service.ship(ARCHIVE, orders["sunqi"], "damage", db_path)
    service.ship(ARCHIVE, orders["zhaomin"], "stagnant", db_path)
    service.ship(ARCHIVE, orders["zhanglei_a"], "confusion", db_path)
    service.ship(ARCHIVE, orders["chenjie"], "normal", db_path)
    for _ in range(5):
        service.advance_logistics(db_path)

    # 孙琪：封签核验为内封破损 → 发起缺页豁免（待审批人处理）
    service.verify_seal_on_return(ARCHIVE, orders["sunqi"], False,
                                  "外封签撕裂约5cm，内封骑缝章断开", db_path)
    service.request_missing_page_waiver(
        ARCHIVE, orders["sunqi"], ["成绩单（补考记载页）"],
        "退回开箱发现成绩单缺补考记载页，原件存于考务中心补制中，申请先予豁免后补寄", db_path)

    # 王晨：正常件，运输中（学生端展示“在途节点”）
    service.ship(ARCHIVE, orders["wangchen"], "normal", db_path)
    service.advance_logistics(db_path)
    service.advance_logistics(db_path)

    # 郑爽：院系已核对待封装；张磊B 保持已封装待寄，便于演示同名密集列表
    # 遮罩证明材料落盘（内容含明文身份证/手机号，落盘版本必须是遮罩的）
    proofs_mod.save_proof(sid_of["wangchen"], "身份证复印件",
                          "兹证明王晨，公民身份号码110101200305122318，联系电话13800000001，"
                          "复印件用于2025届毕业生档案转递。", ARCHIVE["id"], db_path)
    proofs_mod.save_proof(sid_of["lixue"], "单位接收证明",
                          "杭州恒信翻译有限公司同意接收李雪（身份证330106200302201240，"
                          "手机13800000004）档案，联系人高峰 057188225566。",
                          "lixue", db_path)
    return orders


GUIDE = """
================ 毕业生档案转递演示系统 ================
启动成功：http://{host}:{port}

【演示账号】（无密码，界面左上角切换）
  学生端：lixue 李雪（拒收样例）/ wangchen 王晨（在途）
          zhanglei_a 张磊（同名混淆）/ zhanglei_b 张磊（同名另一人）
          sunqi 孙琪（缺页豁免）/ zhaomin 赵敏（物流滞留）
  院系端：dept01    档案室：arch01    学校审批人：appr01

【拒收样例】用 lixue 登录 → 查看拒收与退回链 → 申请重寄；
  换 dept01 复核重寄地址 → arch01 重新加封并寄出（新运单与原运单并排）。
【职责分离】孙琪缺页豁免：arch01 只能发起，appr01 才能批准。
=======================================================
"""


def main():
    orders = seed()
    print(GUIDE.format(host=config.HOST, port=config.PORT))
    return orders


if __name__ == "__main__":
    main()

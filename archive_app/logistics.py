"""模拟物流适配器：不访问任何外部服务，按脚本推进运单。

脚本：
  normal   揽收 -> 运输 -> 派送 -> 签收
  rejected 揽收 -> 运输 -> 派送 -> 拒收（接收单位拒收样例）
  stalled  揽收 -> 运输 -> 滞留 ->（催办后）运输 -> 派送 -> 签收

每次 advance() 让运单向前走一个节点；签收/拒收/滞留会回写业务表
（运单状态、转递单状态、异常单），这些回写由 domain 层提供的钩子完成，
适配器本身只负责产生物流事件。
"""
from .db import now, one

SCRIPTS = {
    "normal": [
        ("校园集散中心", "档案袋已揽收，封签完好", "normal"),
        ("省城分拨中心", "运输中，已到达分拨中心", "normal"),
        ("目的地网点", "派送中，派件员已出发", "normal"),
        ("接收单位", "已签收，签收人核对封签无误", "signed"),
    ],
    "rejected": [
        ("校园集散中心", "档案袋已揽收，封签完好", "normal"),
        ("省城分拨中心", "运输中，已到达分拨中心", "normal"),
        ("目的地网点", "派送中，派件员已出发", "normal"),
        ("接收单位", "接收单位拒收：接收信息不符，快件退回", "rejected"),
    ],
    "stalled": [
        ("校园集散中心", "档案袋已揽收，封签完好", "normal"),
        ("省城分拨中心", "运输中，已到达分拨中心", "normal"),
        ("省城分拨中心", "物流滞留：分拨中心积压，暂未发出", "stalled"),
        ("省城分拨中心", "运输中，滞留件已重新发出", "normal"),
        ("目的地网点", "派送中，派件员已出发", "normal"),
        ("接收单位", "已签收，签收人核对封签无误", "signed"),
    ],
}


class MockLogisticsAdapter:
    """模拟物流商。tracking_no 形如 MN2026-00001。"""

    def __init__(self, conn):
        self.conn = conn

    def create_shipment(self, waybill_id, script="normal"):
        if script not in SCRIPTS:
            raise ValueError(f"未知物流脚本: {script}")
        tracking_no = f"MN2026-{waybill_id:05d}"
        self.conn.execute(
            "UPDATE waybills SET tracking_no=?, script=? WHERE id=?",
            (tracking_no, script, waybill_id),
        )
        return tracking_no

    def progress_of(self, waybill_id):
        row = one(
            self.conn,
            "SELECT COUNT(*) AS c FROM tracking_events WHERE waybill_id=?",
            (waybill_id,),
        )
        return row["c"]

    def advance(self, waybill):
        """推进一张运单，返回新产生的事件 row（无推进则返回 None）。

        滞留中的运单（存在未处理的 stalled 异常）在催办前不再推进。
        """
        wb_id = waybill["id"]
        if waybill["status"] != "active":
            return None
        script = SCRIPTS[waybill["script"]]
        step = self.progress_of(wb_id)
        if step >= len(script):
            return None
        location, description, kind = script[step]
        if kind == "stalled":
            # 产生滞留事件后停住，等待档案室催办
            pass
        elif step > 0 and script[step - 1][2] == "stalled":
            # 上一节点是滞留：必须已催办才能继续
            if not waybill["urged"]:
                return None
        ts = now()
        cur = self.conn.execute(
            "INSERT INTO tracking_events(waybill_id, ts, location, description, kind)"
            " VALUES(?,?,?,?,?)",
            (wb_id, ts, location, description, kind),
        )
        return one(self.conn, "SELECT * FROM tracking_events WHERE id=?", (cur.lastrowid,))

"""模拟物流适配器。

不依赖任何外部服务，用离散的 tick 推进运单状态，剧本化五类场景：
normal / refuse / damage / stagnant / confusion。
service 层每个 tick 调用 advance()，取回本跳事件；终态后再 tick 返回 None。
"""

# 每一跳：(waybill_status, location, note, 触发的异常类型或 None)
STEPS = {
    "normal": [
        ("picked_up", "校寄达营业点", "已揽收", None),
        ("in_transit", "省会分拣中心", "运输中", None),
        ("in_transit", "目的地派送站", "快递员派送中", None),
        ("delivered", "接收单位收发室", "正常签收", None),
    ],
    "refuse": [
        ("picked_up", "校寄达营业点", "已揽收", None),
        ("in_transit", "省会分拣中心", "运输中", None),
        ("rejected", "接收单位", "接收单位拒收：该单位无人事档案接收资质，档案无人签收", "reject"),
        ("returning", "省会分拣中心", "拒收件退回中", None),
        ("returned", "学校档案室", "拒收件已退回档案室", None),
    ],
    "damage": [
        ("picked_up", "校寄达营业点", "已揽收", None),
        ("in_transit", "省会分拣中心", "运输中", None),
        ("rejected", "接收单位", "封签破损，接收单位拒签", "seal_damaged"),
        ("returning", "省会分拣中心", "破损件退回中", None),
        ("returned", "学校档案室", "破损件已退回档案室，待开箱核验", None),
    ],
    "stagnant": [
        ("picked_up", "校寄达营业点", "已揽收", None),
        ("in_transit", "异地分拣中心", "到达异地分拣中心", None),
        # 之后连续滞留，直到档案室催办或召回
    ],
    "confusion": [
        ("picked_up", "校寄达营业点", "已揽收", None),
        ("in_transit", "省会分拣中心", "运输中", None),
        ("in_transit", "目的地派送站", "快递员派送中", None),
        ("delivered", "接收单位收发室", "签收待身份核对（签收人姓名一致）", None),
    ],
}

# 走完剧本节点后，再推进一跳即登记“物流滞留”异常；催办后从该点恢复
STAGNANT_ALERT_AT = 3

STATUS_CN = {
    "picked_up": "已揽收",
    "in_transit": "运输中",
    "delivered": "已签收",
    "rejected": "拒收",
    "returning": "退回中",
    "returned": "已退回",
    "misdelivered": "误投追回",
}


def advance(scenario, ticks, urged=False):
    """返回第 ticks 跳（0 基）应产生的事件，或 None 表示本跳无进展/已终态。

    返回 (status, location, note, exception_type, stagnant_alert:bool)
    """
    steps = STEPS[scenario]

    # 催办后：滞留件从告警点的下一跳恢复流转，然后正常签收
    if scenario == "stagnant" and urged:
        resume = [
            ("in_transit", "目的地派送站", "催办成功，恢复运输并派送", None),
            ("delivered", "接收单位收发室", "正常签收", None),
        ]
        idx = ticks - STAGNANT_ALERT_AT - 1
        if 0 <= idx < len(resume):
            return (*resume[idx], False)
        return None

    if ticks < len(steps):
        status, loc, note, exc = steps[ticks]
        return (status, loc, note, exc, False)

    if scenario == "stagnant" and ticks == STAGNANT_ALERT_AT:
        return ("in_transit", "异地分拣中心", "物流长时间滞留，无物流动态", None, True)

    return None


def is_terminal(status):
    return status in ("delivered", "returned")

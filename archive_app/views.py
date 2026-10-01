"""页面渲染：学生端 / 院系端 / 档案室端。

视觉基调：白底、深灰文字、校园蓝主色。敏感字段默认遮挡，
仅学生本人与档案室可手动临时显示；院系一律不可见。
"""
from html import escape

from .masking import mask_id_number, mask_phone

BLUE = "#1b5ec4"

STATUS_LABEL = {
    "draft": ("草稿", "st-gray"),
    "submitted": ("待院系核对", "st-blue"),
    "dept_rejected": ("院系退回", "st-red"),
    "dept_verified": ("待封装", "st-blue"),
    "sealed": ("已封装待寄出", "st-blue"),
    "shipped": ("已寄出", "st-blue"),
    "signed": ("已签收", "st-green"),
    "archived": ("已归档", "st-dark"),
    "cancelled": ("已作废", "st-gray"),
}

EXC_LABEL = {
    "rejected": "接收单位拒收",
    "seal_damaged": "封签破损",
    "missing_pages": "材料缺页",
    "stalled": "物流滞留",
    "name_confusion": "同名混淆",
}

WB_LABEL = {
    "active": "运输中",
    "delivered": "已签收",
    "returning": "退回中",
    "returned": "已退回",
    "cancelled": "已作废",
}

NODES = ["去向申报", "院系核对", "档案封装", "寄出运输", "签收归档"]
SCORE = {"draft": 0, "dept_rejected": 0, "submitted": 1, "dept_verified": 2,
         "sealed": 3, "shipped": 4, "signed": 5, "archived": 6}
DONE_AT = [1, 2, 3, 5, 6]          # 各节点完成所需进度分
NODE_HINT = {
    "draft": "填写中", "dept_rejected": "院系退回，请修改后重新提交",
    "submitted": "等待院系核对毕业状态", "dept_verified": "核对通过，待档案室封装",
    "sealed": "已封装，待寄出", "shipped": "运输中",
    "signed": "接收单位已签收，待归档", "archived": "转递完成，已归档",
}


def esc(s):
    return escape(str(s if s is not None else ""))


def status_badge(status):
    label, cls = STATUS_LABEL.get(status, (status, "st-gray"))
    return f'<span class="badge {cls}">{esc(label)}</span>'


def exc_badge(type_):
    return f'<span class="badge st-red">{esc(EXC_LABEL.get(type_, type_))}</span>'


def sens(value, allow_reveal=False, kind="phone"):
    """敏感字段：默认遮挡；有权限的角色可临时显示。院系拿不到真实值。"""
    masked = mask_id_number(value) if kind == "id" else mask_phone(value)
    if allow_reveal:
        return (f'<span class="sens"><span class="vm">{esc(masked)}</span>'
                f'<span class="vr" hidden>{esc(value)}</span>'
                f'<button type="button" class="rv">显示</button></span>')
    return f'<span class="sens"><span class="vm">{esc(masked)}</span></span>'


def stepper(status):
    score = SCORE.get(status, 0)
    cur = next((i for i, t in enumerate(DONE_AT) if score < t), len(NODES))
    parts = ['<div class="stepper">']
    for i, name in enumerate(NODES):
        cls = "done" if i < cur else ("cur" if i == cur else "todo")
        parts.append(
            f'<div class="step {cls}"><div class="dot">{i + 1}</div>'
            f'<div class="sname">{esc(name)}</div>'
            + (f'<div class="shint">{esc(NODE_HINT.get(status, ""))}</div>'
               if i == cur else "")
            + "</div>")
        if i < len(NODES) - 1:
            parts.append(f'<div class="bar {"done" if i < cur else ""}"></div>')
    parts.append("</div>")
    return "".join(parts)


def layout(title, user, body, msg="", kind="ok"):
    nav = ""
    if user:
        home = {"student": "/student", "department": "/dept",
                "archivist": "/archive"}[user["role"]]
        role_name = {"student": "学生", "department": "院系",
                     "archivist": "档案室"}[user["role"]]
        nav = (f'<nav><a href="{home}">工作台</a></nav>'
               f'<div class="who"><span class="role">{role_name}</span>'
               f'{esc(user["display_name"])}'
               f'<form method="post" action="/logout" class="inline">'
               f'<button class="link">退出</button></form></div>')
    flash = f'<div class="flash {esc(kind)}">{esc(msg)}</div>' if msg else ""
    return f"""<!doctype html>
<html lang="zh-CN"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)} · 毕业生档案转递系统</title>
<link rel="stylesheet" href="/static/style.css">
</head><body>
<header><div class="brand"><span class="logo">档</span>毕业生档案转递系统</div>{nav}</header>
<main>{flash}{body}</main>
<footer>校园演示环境 · 物流为模拟适配器 · 敏感字段默认遮挡</footer>
<script>
document.querySelectorAll('.rv').forEach(function(b){{
  b.addEventListener('click',function(){{
    var p=b.parentElement,m=p.querySelector('.vm'),r=p.querySelector('.vr');
    var show=r.hidden;r.hidden=!show;m.hidden=show;b.textContent=show?'隐藏':'显示';
  }});
}});
</script>
</body></html>"""


def login_page(users, msg=""):
    groups = {}
    for u in users:
        groups.setdefault(u["role"], []).append(u)
    role_name = {"student": "学生", "department": "院系", "archivist": "档案室"}
    blocks = []
    for role in ("student", "department", "archivist"):
        opts = "".join(
            f'<option value="{u["id"]}">{esc(u["display_name"])}'
            f'（{esc(u["dept_name"] or u["username"])}）</option>'
            for u in groups.get(role, []))
        blocks.append(f'<optgroup label="{role_name[role]}">{opts}</optgroup>')
    body = f"""
<div class="login card">
  <h1>毕业生档案转递系统</h1>
  <p class="muted">去向申报 · 院系核对 · 档案封装 · 邮寄追踪 · 退件重寄 · 签收归档</p>
  <form method="post" action="/login">
    <label>选择演示身份</label>
    <select name="user_id">{''.join(blocks)}</select>
    <button class="primary">进入系统</button>
  </form>
  <p class="muted small">演示环境免密码。同名混淆演示：王芳 20260103 的身份证后四位为 1234。</p>
</div>"""
    return layout("登录", None, body, msg, "err" if msg else "ok")


# ------------------------------------------------------------------ 学生端

def _order_card(user, o, bundle):
    excs = bundle["exceptions"].get(o["id"], [])
    wbs = bundle["waybills"].get(o["id"], [])
    changes = bundle["changes"].get(o["id"], [])
    open_exc = "".join(
        f'<div class="alert">{exc_badge(e["type"])} {esc(e["detail"])}'
        f'<span class="muted">（处理中）</span></div>' for e in excs)
    if o["status"] == "dept_rejected":
        open_exc += '<div class="alert">院系已退回，请修改申报内容后重新提交。</div>'

    # 物流时间线（最新运单）
    track = ""
    if wbs:
        last = wbs[-1]
        evs = "".join(
            f'<li><span class="ts">{esc(e["ts"])}</span> {esc(e["location"])}'
            f' — {esc(e["description"])}</li>'
            for e in reversed(last["events"]))
        track = (f'<div class="track"><h4>物流跟踪 · 运单 {esc(last["tracking_no"])}'
                 f'（{esc(WB_LABEL[last["status"]])}）</h4>'
                 f'<ul class="timeline">{evs or "<li class=muted>暂无物流信息</li>"}</ul></div>')
        if len(wbs) > 1:
            chain = " → ".join(
                f'{esc(w["tracking_no"])}（{esc(WB_LABEL[w["status"]])}）' for w in wbs)
            track += f'<p class="muted small">退件重寄链：{chain}</p>'

    # 操作区
    actions = []
    if o["status"] in ("draft", "dept_rejected"):
        actions.append(f'''<details class="editbox"><summary>修改申报内容</summary>
<form method="post" action="/student/order/{o["id"]}/edit" class="grid2">
<label>接收单位<input name="recv_unit" value="{esc(o["recv_unit"])}" required></label>
<label>接收人<input name="recv_person" value="{esc(o["recv_person"])}" required></label>
<label>接收地址<input name="recv_address" value="{esc(o["recv_address"])}" required></label>
<label>接收人电话<input name="recv_phone" value="{esc(o["recv_phone"])}" required></label>
<label class="span2">报到材料说明<input name="materials_note" value="{esc(o["materials_note"])}"></label>
<button class="primary">保存修改</button></form></details>''')
        actions.append(f'<form method="post" action="/student/order/{o["id"]}/submit" '
                       f'class="inline"><button class="primary">提交申报</button></form>')
        actions.append(f'<form method="post" action="/student/order/{o["id"]}/cancel" '
                       f'class="inline"><button class="danger">作废</button></form>')
    if o["status"] in ("submitted", "dept_verified", "sealed"):
        actions.append(f'''<details class="editbox"><summary>申请更正接收地址</summary>
<form method="post" action="/student/order/{o["id"]}/address-change" class="grid2">
<label>新接收单位<input name="new_unit" value="{esc(o["recv_unit"])}" required></label>
<label>新接收人<input name="new_person" value="{esc(o["recv_person"])}" required></label>
<label>新接收地址<input name="new_address" value="{esc(o["recv_address"])}" required></label>
<label>新接收人电话<input name="new_phone" value="{esc(o["recv_phone"])}" required></label>
<label class="span2">更正理由<input name="reason" placeholder="必填" required></label>
<button class="primary">提交更正申请（院系复核后生效）</button></form></details>''')
    if o["status"] in ("shipped", "signed"):
        actions.append('<div class="notice">转递单已寄出，地址已锁定；'
                       '如需变更只能由档案室启动退回重寄。</div>')
    actions.append(f'''<details class="editbox"><summary>上传证明材料（落盘即遮罩）</summary>
<form method="post" action="/student/order/{o["id"]}/material">
<label>材料名称<input name="title" required placeholder="如：报到证扫描件"></label>
<label>材料内容<textarea name="content" rows="3" required
 placeholder="粘贴材料文本，身份证号、手机号将在保存前自动遮罩"></textarea></label>
<button class="primary">上传</button></form></details>''')

    chg = ""
    if changes:
        rows = "".join(
            f'<tr><td>{esc(c["created_at"])}</td><td>{esc(c["new_address"])}</td>'
            f'<td>{"待复核" if c["status"] == "pending" else ("已通过" if c["status"] == "approved" else "已拒绝")}</td></tr>'
            for c in changes)
        chg = (f'<h4>地址更正记录</h4><table class="dense"><tr><th>时间</th>'
               f'<th>新地址</th><th>复核结果</th></tr>{rows}</table>')

    mats = ""
    if bundle["materials"].get(o["id"]):
        items = "".join(
            f'<li>{esc(m["title"])} <span class="muted">（已遮罩存储，'
            f'{esc(m["created_at"])}）</span> '
            f'<a href="/student/material/{m["id"]}">查看</a></li>'
            for m in bundle["materials"][o["id"]])
        mats = f'<h4>证明材料</h4><ul>{items}</ul>'

    audit = "".join(
        f'<li><span class="ts">{esc(a["ts"])}</span> {esc(a["action"])}'
        + (f' — {esc(a["note"])}' if a["note"] else "") + "</li>"
        for a in bundle["audit"].get(o["id"], []))

    return f"""
<section class="card order">
  <div class="order-head">
    <div>
      <div class="muted small">转递单号 {esc(o["order_no"])} · {esc(o["batch_name"])}</div>
      <div class="recv-unit">{esc(o["recv_unit"])}</div>
      <div class="muted">{esc(o["recv_address"])} · 接收人 {esc(o["recv_person"])} ·
        {sens(o["recv_phone"], allow_reveal=True)}</div>
    </div>
    <div class="head-side">{status_badge(o["status"])}
      {f'<div class="seal">封签号 <b>{esc(o["seal_no"])}</b></div>' if o["seal_no"] else ""}
    </div>
  </div>
  {stepper(o["status"])}
  {open_exc}
  {track}
  {chg}
  {mats}
  <div class="actions">{''.join(actions)}</div>
  <details><summary class="muted">转递链（操作留痕）</summary>
    <ul class="timeline">{audit}</ul></details>
</section>"""


def student_page(user, bundle, batches_open, msg="", kind="ok"):
    cards = "".join(_order_card(user, o, bundle) for o in bundle["orders"])
    if not cards:
        cards = '<div class="card muted">还没有转递单，请在下方进行去向申报。</div>'
    new_form = ""
    if batches_open:
        opts = "".join(f'<option value="{b["id"]}">{esc(b["name"])}</option>'
                       for b in batches_open)
        new_form = f"""
<section class="card"><h3>去向申报（新转递单）</h3>
<form method="post" action="/student/order/new" class="grid2">
<label>毕业批次<select name="batch_id">{opts}</select></label>
<label>接收单位<input name="recv_unit" required placeholder="如：××市人才服务中心"></label>
<label>接收地址<input name="recv_address" required></label>
<label>接收人<input name="recv_person" required></label>
<label>接收人电话<input name="recv_phone" required></label>
<label class="span2">报到材料说明<input name="materials_note"
  placeholder="如：报到证、学位授予材料、成绩单"></label>
<button class="primary">保存草稿</button>
<p class="muted small span2">同一毕业批次只允许一份有效转递单。</p>
</form></section>"""
    stu = bundle["student"]
    head = f"""
<section class="card idcard">
  <div><b>{esc(user["display_name"])}</b> · 学号 {esc(stu["student_no"])} ·
    {esc(stu["class_name"])}</div>
  <div class="muted">身份证号 {sens(stu["id_number"], allow_reveal=True, kind="id")}
    · 毕业状态 {esc(stu["grad_status"])}</div>
</section>"""
    return layout("学生工作台", user, head + cards + new_form, msg, kind)


def material_view(user, material, content):
    body = f"""
<section class="card">
  <h3>{esc(material["title"])}</h3>
  <p class="muted small">本地仅保存遮罩后的版本（{esc(material["created_at"])} 上传）。</p>
  <pre class="material">{esc(content)}</pre>
  <p><a href="javascript:history.back()">返回</a></p>
</section>"""
    return layout("证明材料", user, body)


# ------------------------------------------------------------------ 院系端

def dept_page(user, to_verify, changes, exemptions, done, msg="", kind="ok"):
    tv_rows = "".join(f"""
<tr><td>{esc(o["order_no"])}</td><td>{esc(o["batch_name"])}</td>
<td>{esc(o["display_name"])}</td><td>{esc(o["student_no"])}</td>
<td>{esc(o["grad_status"])}</td><td>{esc(o["recv_unit"])}</td>
<td>{sens(o["recv_phone"])}</td>
<td class="nowrap"><form method="post" action="/dept/verify/{o["id"]}" class="inline">
  <input type="hidden" name="approve" value="1">
  <button class="primary sm">核对通过</button></form>
<form method="post" action="/dept/verify/{o["id"]}" class="inline">
  <input type="hidden" name="approve" value="0">
  <button class="danger sm">退回</button></form></td></tr>""" for o in to_verify)
    to_verify_tbl = (f'<table class="dense"><tr><th>转递单号</th><th>批次</th><th>姓名</th>'
                     f'<th>学号</th><th>毕业状态</th><th>接收单位</th><th>接收人电话</th>'
                     f'<th>操作</th></tr>{tv_rows}</table>'
                     if tv_rows else '<p class="muted">暂无待核对的转递单。</p>')

    ch_rows = "".join(f"""
<tr><td>{esc(c["order_no"])}</td><td>{esc(c["display_name"])}</td>
<td>{esc(c["new_unit"])}<br><span class="muted">{esc(c["new_address"])}</span></td>
<td>{esc(c["reason"])}</td>
<td class="nowrap"><form method="post" action="/dept/address-change/{c["id"]}" class="inline">
  <input type="hidden" name="approve" value="1"><button class="primary sm">同意</button></form>
<form method="post" action="/dept/address-change/{c["id"]}" class="inline">
  <input type="hidden" name="approve" value="0"><button class="danger sm">拒绝</button></form>
</td></tr>""" for c in changes)
    ch_tbl = (f'<table class="dense"><tr><th>转递单号</th><th>学生</th><th>新接收信息</th>'
              f'<th>理由</th><th>复核</th></tr>{ch_rows}</table>'
              if ch_rows else '<p class="muted">暂无待复核的地址变更。</p>')

    ex_rows = "".join(f"""
<tr><td>{esc(x["order_no"])}</td><td>{esc(x["display_name"])}</td>
<td>{esc(x["detail"])}</td><td>{esc(x["reason"])}</td>
<td class="nowrap"><form method="post" action="/dept/exemption/{x["id"]}" class="inline">
  <input type="hidden" name="approve" value="1"><button class="primary sm">批准豁免</button></form>
<form method="post" action="/dept/exemption/{x["id"]}" class="inline">
  <input type="hidden" name="approve" value="0"><button class="danger sm">拒绝</button></form>
</td></tr>""" for x in exemptions)
    ex_tbl = (f'<table class="dense"><tr><th>转递单号</th><th>学生</th><th>缺页情况</th>'
              f'<th>豁免理由</th><th>审批</th></tr>{ex_rows}</table>'
              if ex_rows else '<p class="muted">暂无待审批的缺页豁免。</p>')

    done_rows = "".join(
        f'<tr><td>{esc(o["order_no"])}</td><td>{esc(o["display_name"])}</td>'
        f'<td>{esc(o["student_no"])}</td><td>{status_badge(o["status"])}</td></tr>'
        for o in done)
    done_tbl = (f'<table class="dense"><tr><th>转递单号</th><th>姓名</th><th>学号</th>'
                f'<th>当前状态</th></tr>{done_rows}</table>' if done_rows else "")

    body = f"""
<section class="card"><h3>待核对毕业状态（{len(to_verify)}）</h3>{to_verify_tbl}</section>
<section class="card"><h3>地址变更复核（{len(changes)}）</h3>
<p class="muted small">地址变更须在转递单寄出前完成复核；寄出后地址锁定，只能退回重寄。</p>{ch_tbl}</section>
<section class="card"><h3>缺页豁免审批（{len(exemptions)}）</h3>
<p class="muted small">缺页豁免由档案室发起、院系审批；档案室不能批准自己的豁免。</p>{ex_tbl}</section>
<section class="card"><h3>近期已核对</h3>{done_tbl or '<p class="muted">暂无。</p>'}
<p class="muted small">依据职责分离要求，院系无权查看档案袋内的证明材料与敏感信息。</p></section>"""
    return layout("院系工作台", user, body, msg, kind)


# ------------------------------------------------------------------ 档案室端

def archive_page(user, by_batch, open_excs, msg="", kind="ok"):
    sections = []
    for batch_name, rows in by_batch:
        trs = []
        for o in rows:
            exc_mark = (f' <span class="dot-red" title="有未处理异常"></span>'
                        if o["open_exc"] else "")
            trs.append(f"""
<tr><td><a href="/archive/order/{o["id"]}">{esc(o["order_no"])}</a>{exc_mark}</td>
<td>{esc(o["student_no"])}</td><td>{esc(o["display_name"])}</td>
<td class="seal-cell">{esc(o["seal_no"] or "—")}</td>
<td>{status_badge(o["status"])}</td>
<td>{esc(o["tracking_no"] or "—")}</td>
<td>{esc(o["recv_unit"])}</td></tr>""")
        sections.append(f"""
<section class="card"><h3>{esc(batch_name)}
  <span class="muted small">（{len(rows)} 份转递单）</span></h3>
<table class="dense seals"><tr><th>转递单号</th><th>学号</th><th>姓名</th>
<th>封签号</th><th>状态</th><th>当前运单</th><th>接收单位</th></tr>
{''.join(trs)}</table></section>""")

    exc_items = "".join(f"""
<li>{exc_badge(e["type"])} <a href="/archive/order/{e["order_id"]}">{esc(e["order_no"])}</a>
  {esc(e["display_name"])} — {esc(e["detail"])}</li>""" for e in open_excs)
    exc_box = (f'<section class="card exc"><h3>未处理异常（{len(open_excs)}）</h3>'
               f'<ul class="exclist">{exc_items}</ul></section>' if open_excs else
               '<section class="card"><h3>未处理异常（0）</h3>'
               '<p class="muted">当前没有待处理异常。</p></section>')

    body = f"""
<section class="card toolbar">
  <form method="post" action="/archive/advance" class="inline">
    <button class="primary">推进物流（模拟）</button>
  </form>
  <span class="muted">模拟适配器让所有在途运单前进一个节点；含「拒收样例」运单。</span>
</section>
{exc_box}
{''.join(sections)}"""
    return layout("档案室工作台", user, body, msg, kind)


def _waybill_chain(wbs):
    cards = []
    for i, w in enumerate(wbs):
        evs = "".join(
            f'<li><span class="ts">{esc(e["ts"])}</span> {esc(e["location"])}'
            f' — {esc(e["description"])}</li>' for e in reversed(w["events"]))
        cls = {"returned": "wb returned", "returning": "wb returning",
               "delivered": "wb delivered"}.get(w["status"], "wb active")
        tag = "原运单" if i == 0 else f"重寄 #{w['seq'] - 1}"
        cards.append(f"""
<div class="{cls}">
  <div class="wb-tag">{tag}</div>
  <div class="wb-no">{esc(w["tracking_no"])}</div>
  <div class="muted small">{esc(WB_LABEL[w["status"]])} · 封签号 {esc(w["seal_no"])}</div>
  <div class="small">{esc(w["recv_unit"])}<br>{esc(w["recv_address"])}</div>
  <ul class="timeline">{evs or '<li class="muted">暂无物流事件</li>'}</ul>
</div>""")
        if i < len(wbs) - 1:
            cards.append('<div class="wb-arrow">退回<br>重寄<br>→</div>')
    return f'<div class="wb-chain">{"".join(cards)}</div>'


def archive_order_page(user, d, msg="", kind="ok"):
    o = d["order"]
    stu = d["student"]
    can_ship = o["status"] == "sealed" and not d["open_excs"] and not d["pending_change"]
    can_seal = o["status"] == "dept_verified" and not any(
        e["type"] in ("missing_pages", "name_confusion") for e in d["open_excs"])

    ops = []
    if can_seal:
        ops.append(f'<form method="post" action="/archive/seal/{o["id"]}" class="inline">'
                   f'<button class="primary">封装（生成封签号）</button></form>')
    if can_ship:
        ops.append(f'<form method="post" action="/archive/ship/{o["id"]}" class="inline">'
                   f'<button class="primary">寄出</button></form>')
    if o["status"] == "signed":
        ops.append(f'<form method="post" action="/archive/archive/{o["id"]}" '
                   f'class="inline"><button class="primary">签收归档</button></form>')
    last_wb = d["waybills"][-1] if d["waybills"] else None
    if last_wb and last_wb["status"] == "active":
        ops.append(f'''<form method="post" action="/archive/return/{last_wb["id"]}"
 class="inline"><input name="reason" placeholder="退回原因" required>
 <button class="warn">启动退回</button></form>''')
    if last_wb and last_wb["status"] == "returning":
        ops.append(f'<form method="post" action="/archive/returned/{last_wb["id"]}" '
                   f'class="inline"><button class="primary">确认退件入库</button></form>')
    if last_wb and last_wb["status"] == "returned" and o["status"] == "shipped":
        ops.append(f'''<details class="editbox" open><summary>退回重寄（更正接收信息）</summary>
<form method="post" action="/archive/resend/{o["id"]}" class="grid2">
<label>接收单位<input name="new_unit" value="{esc(o["recv_unit"])}" required></label>
<label>接收人<input name="new_person" value="{esc(o["recv_person"])}" required></label>
<label>接收地址<input name="new_address" value="{esc(o["recv_address"])}" required></label>
<label>接收人电话<input name="new_phone" value="{esc(o["recv_phone"])}" required></label>
<button class="primary">生成重寄运单</button></form></details>''')

    # 异常处理面板（按类型给出独立操作）
    exc_panels = []
    for e in d["open_excs"]:
        panel = ""
        if e["type"] == "missing_pages":
            panel = f"""
<form method="post" action="/archive/exemption/{e["id"]}" class="inline">
  <input name="reason" placeholder="豁免理由" required>
  <button class="warn sm">申请缺页豁免（院系审批）</button></form>
<form method="post" action="/archive/supplement/{e["id"]}" class="inline">
  <button class="primary sm">材料已补齐，闭环</button></form>"""
        elif e["type"] == "name_confusion":
            panel = f"""
<form method="post" action="/archive/identity/{e["id"]}" class="inline">
  <input name="student_no" placeholder="学号" required>
  <input name="id_tail" placeholder="身份证后四位" maxlength="4" required>
  <button class="primary sm">身份核验并解除</button></form>"""
        elif e["type"] == "stalled":
            panel = f"""
<form method="post" action="/archive/urge/{e["waybill_id"]}" class="inline">
  <button class="primary sm">向物流商催办</button></form>"""
        elif e["type"] == "seal_damaged":
            panel = f"""
<form method="post" action="/archive/reseal/{o["id"]}" class="inline">
  <button class="primary sm">重新封装（换发封签号）</button></form>"""
        elif e["type"] == "rejected":
            panel = ('<span class="muted small">拒收件退回入库后，'
                     '通过「退回重寄」生成新运单闭环。</span>')
        exc_panels.append(
            f'<div class="exc-panel">{exc_badge(e["type"])} {esc(e["detail"])}'
            f'<div class="exc-ops">{panel}</div></div>')
    exc_html = (f'<section class="card exc"><h3>异常处理</h3>{"".join(exc_panels)}</section>'
                if exc_panels else "")

    # 登记新异常
    flag_form = f"""
<details class="editbox"><summary>登记异常</summary>
<form method="post" action="/archive/exception/{o["id"]}" class="inline">
<select name="type">{''.join(f'<option value="{k}">{v}</option>' for k, v in EXC_LABEL.items())}</select>
<input name="detail" placeholder="异常说明" required>
<button class="warn sm">登记</button></form></details>"""

    mats = ""
    if d["materials"]:
        items = "".join(
            f'<li>{esc(m["title"])} <span class="muted">（遮罩存储）</span> '
            f'<a href="/archive/material/{m["id"]}">查看</a></li>'
            for m in d["materials"])
        mats = f'<h4>袋内证明材料（院系不可见）</h4><ul>{items}</ul>'

    hist_excs = "".join(
        f'<li>{exc_badge(e["type"])} {esc(e["detail"])} → '
        f'<span class="muted">{esc(e["resolution"])}（{esc(e["resolved_at"])}）</span></li>'
        for e in d["resolved_excs"])
    hist = f'<h4>已闭环异常</h4><ul>{hist_excs}</ul>' if hist_excs else ""

    audit = "".join(
        f'<li><span class="ts">{esc(a["ts"])}</span> '
        f'{esc(a["actor"])} {esc(a["action"])}'
        + (f' — {esc(a["note"])}' if a["note"] else "") + "</li>"
        for a in d["audit"])

    packing = ""
    if o["seal_no"]:
        mat_list = "".join(f"<li>{esc(m['title'])}</li>" for m in d["materials"]) or \
                   "<li class='muted'>无电子材料</li>"
        packing = f"""
<section class="card packing"><h3>封装清单</h3>
<div class="pack-grid">
<div><div class="muted small">封签号</div><div class="seal-big">{esc(o["seal_no"])}</div></div>
<div><div class="muted small">封装人 / 时间</div>{esc(d["sealed_by"] or "—")}<br>
{esc(o["sealed_at"] or "—")}</div>
<div><div class="muted small">报到材料说明</div>{esc(o["materials_note"] or "—")}</div>
<div><div class="muted small">袋内材料</div><ul class="tight">{mat_list}</ul></div>
</div></section>"""

    body = f"""
<section class="card order">
  <div class="order-head">
    <div>
      <div class="muted small">转递单号 {esc(o["order_no"])} · {esc(d["batch_name"])}</div>
      <div class="recv-unit">{esc(o["recv_unit"])}</div>
      <div class="muted">{esc(o["recv_address"])} · 接收人 {esc(o["recv_person"])} ·
        {sens(o["recv_phone"], allow_reveal=True)}</div>
      <div class="muted small">学生 {esc(stu["name"])}（学号 {esc(stu["student_no"])}）
        · 身份证 {sens(stu["id_number"], allow_reveal=True, kind="id")}
        · 毕业状态 {esc(stu["grad_status"])}</div>
    </div>
    <div class="head-side">{status_badge(o["status"])}
      {f'<div class="seal">封签号 <b>{esc(o["seal_no"])}</b></div>' if o["seal_no"] else ""}
    </div>
  </div>
  {stepper(o["status"])}
  <div class="actions">{''.join(ops)}{flag_form}</div>
</section>
{exc_html}
{packing}
<section class="card"><h3>运单链（退件链与原运单并排）</h3>
{_waybill_chain(d["waybills"]) if d["waybills"] else '<p class="muted">尚未寄出。</p>'}
</section>
<section class="card">{mats or '<p class="muted">暂无证明材料。</p>'}{hist}</section>
<section class="card"><h3>转递链（操作留痕）</h3>
<ul class="timeline">{audit}</ul></section>
<p><a href="/archive">← 返回档案室工作台</a></p>"""
    return layout(f"转递单 {o['order_no']}", user, body, msg, kind)

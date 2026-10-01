/* 毕业生档案转递 SPA：学生 / 院系 / 档案室 / 审批人 四端。无框架、无构建。 */
"use strict";

const USERS = [
  {id:"", name:"请选择身份…", role:""},
  {id:"lixue", name:"李雪（学生·拒收样例）", role:"student"},
  {id:"wangchen", name:"王晨（学生·在途）", role:"student"},
  {id:"zhanglei_a", name:"张磊 A（学生·同名混淆）", role:"student"},
  {id:"zhanglei_b", name:"张磊 B（学生·同名另一人）", role:"student"},
  {id:"sunqi", name:"孙琪（学生·缺页豁免）", role:"student"},
  {id:"zhaomin", name:"赵敏（学生·物流滞留）", role:"student"},
  {id:"zhouting", name:"周婷（学生·地址更正中）", role:"student"},
  {id:"chenjie", name:"陈杰（学生·已归档）", role:"student"},
  {id:"dept01", name:"王教务（院系）", role:"department"},
  {id:"arch01", name:"刘档案（档案室）", role:"archive"},
  {id:"appr01", name:"周审批（学校审批人）", role:"approver"},
];
const ROLE_CN = {student:"学生端", department:"院系端", archive:"档案室端", approver:"审批端"};
const STATUS_CN = {declared:"去向已申报", verified:"院系已核对", packed:"已封装", shipped:"已寄出",
  exception:"异常处理中", returned:"已退回待重寄", archived:"已签收归档", void:"已作废"};
const EXC_CN = {reject:"接收单位拒收", seal_damaged:"封签破损", missing_page:"材料缺页",
  stagnant:"物流滞留", confusion:"同名档案混淆"};
const SCENARIO_CN = {normal:"正常签收", refuse:"拒收", damage:"封签破损", stagnant:"物流滞留", confusion:"同名混淆"};

let me = null, ref = {students:[], scenarios:[]}, state = {tab:null};

const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

async function api(path, opts={}) {
  const headers = {"Content-Type":"application/json"};
  if (opts.method === "POST") headers["X-User-Id"] = me.id;
  if (opts.method !== "POST" && !path.includes("?")) path += "?as=" + me.id;
  if (opts.method !== "POST" && path.includes("?")) path += "&as=" + me.id;
  const r = await fetch(path, {method:opts.method||"GET", headers, body:opts.body?JSON.stringify(opts.body):undefined});
  const j = await r.json();
  if (!j.ok) throw new Error(j.error);
  return j.data;
}
const post = (p, body) => api(p, {method:"POST", body});

function toast(msg, isErr) {
  const t = $("#toast");
  t.textContent = msg; t.className = "toast show" + (isErr ? " err" : "");
  clearTimeout(t._h); t._h = setTimeout(()=>t.className="toast", 2600);
}
async function guard(fn) { try { await fn(); } catch (e) { toast(e.message, true); } }

function dialog(html) {
  $("#dialogBox").innerHTML = html;
  $("#dialog").classList.add("show");
}
function closeDialog() { $("#dialog").classList.remove("show"); }
$("#dialog").addEventListener("click", e => { if (e.target.id === "dialog") closeDialog(); });

function pill(status) { return `<span class="pill ${status}">${STATUS_CN[status]||status}</span>`; }
function excPill(e) {
  return `<span class="pill ${e.status==='open'?'exception':'archived'}" style="margin-right:6px">`
    + `${EXC_CN[e.type]}·${e.status==='open'?'待处理':'已处理'}</span>`;
}
function fmt(o) { return o ? esc(o).replace("T"," ").slice(0,19) : "—"; }

/* ------------------------------------------------------------- 通用：单据详情 */
function orderView(o) {
  const canSensitive = me.role === "archive" || me.role === "approver";
  const s = o.student;
  let idCard = `<span class="mask">**************${esc(s.id_card_tail)}</span>`;
  if (canSensitive && s.id_card_full)
    idCard += ` <button class="sm ghost" onclick="toggleId(this,'${esc(s.id_card_full)}')">显示完整</button>`;

  const wbs = o.waybills.map((w,i) => {
    const cls = w.status==="returned"||w.status==="rejected" ? "bad" : w.status==="delivered" ? "ok" : "";
    const leg = w.leg===1 ? "原运单（首寄）" : `退件链 · 第 ${w.leg} 次寄递`;
    const tracks = w.events.map(ev =>
      `<li><div>${esc(ev.location)} · ${esc(ev.note)}</div><div class="when">${fmt(ev.created_at)}</div></li>`).join("");
    return `<div class="waybill ${cls}">
      <div class="head"><div><b>${leg}</b><div class="muted mono">${esc(w.tracking_no)}</div></div>
      <span class="pill ${w.status==='delivered'?'archived':w.status==='returned'?'returned':'shipped'}">${w.status_cn}</span></div>
      <div class="body"><ul class="track">${tracks}</ul>
      <div class="muted">剧本：${SCENARIO_CN[w.scenario]||w.scenario}${w.returned_at?" · 退回："+fmt(w.returned_at):""}${w.delivered_at?" · 签收："+fmt(w.delivered_at):""}</div></div></div>`;
  }).join("");

  const packing = o.packing ? `
    <h3>封装清单与封签</h3>
    <div class="row" style="margin-bottom:6px"><b>封签号：</b><span class="mono" style="font-size:15px">${esc(o.packing.seal_no)}</span>
      <span class="muted">封装人 ${esc(o.packing.packed_by)} · ${fmt(o.packing.created_at)}</span></div>
    <table><tr><th>袋内材料</th><th>份数</th><th>密级</th></tr>
      ${o.packing.materials.map(m=>`<tr><td>${esc(m.name)}</td><td>${m.copies}</td>
        <td>${m.sensitive?'<span class="tag sens">敏感</span>':'<span class="tag">普通</span>'}</td></tr>`).join("")}
    </table>${canSensitive?"":'<div class="muted" style="margin-top:6px">🔒 按职责分离要求，院系与学生不可查看袋内敏感材料明细。</div>'}` : "";

  const excs = o.exceptions.length ? `<h3>异常记录</h3>` + o.exceptions.map(e=>`
    <div class="exc-box ${e.status==='resolved'?'resolved':''}">
      <h4>${EXC_CN[e.type]} <span class="muted">${e.status==='open'?'待处理':'已处理'}</span></h4>
      <div>${esc(e.note)}</div>
      ${e.resolution?`<div class="muted">处置：${esc(e.resolution)}（${esc(e.resolved_by)}）</div>`:""}
    </div>`).join("") : "";

  const changes = o.address_changes.length ? `<h3>接收信息变更记录</h3><table>
    <tr><th>类型</th><th>新接收单位/地址</th><th>状态</th><th>复核意见</th></tr>
    ${o.address_changes.map(c=>`<tr><td>${c.kind==="address"?"寄出前更正":"退回重寄"}</td>
      <td>${esc(c.new_org)}<br><span class="muted">${esc(c.new_addr)}</span></td>
      <td>${{pending:"待复核",approved:"已通过",rejected:"已驳回"}[c.status]}</td>
      <td>${esc(c.review_note||"—")}</td></tr>`).join("")}</table>` : "";

  const waivers = o.waivers.length ? `<h3>缺页豁免</h3>${o.waivers.map(w=>`
    <div class="exc-box ${w.status==='approved'?'resolved':''}">
      <h4>缺页：${esc(JSON.parse(w.materials||"[]").join("、"))} <span class="muted">${{approved:"已批准",rejected:"已驳回",pending:"待审批"}[w.status]}</span></h4>
      <div>${esc(w.reason)}</div>
      <div class="muted">发起：${esc(w.request_by)} → 审批：${esc(w.reviewed_by||"（待学校审批人）")} ${esc(w.review_note)}</div>
    </div>`).join("")}` : "";

  return `
    <div class="card">
      <div class="row" style="justify-content:space-between">
        <div><h2 style="margin:0">转递单 ${esc(o.order_no)} ${pill(o.status)}</h2>
        <div class="muted">${esc(s.college)} · ${esc(s.major)} · ${esc(s.grad_batch)}届</div></div>
        <div class="muted">最近更新 ${fmt(o.updated_at)}</div>
      </div>
    </div>
    <div class="card">
      <div class="row"><b>${esc(s.name)}</b>（学号 ${esc(s.student_no)}） 证件：${idCard}
        <span class="muted">手机 <span class="mask">${esc(s.phone_masked)}</span></span></div>
      <h3>接收单位与报到材料</h3>
      <div class="row" style="align-items:flex-start">
        <div style="flex:2;min-width:240px"><b style="font-size:15px">${esc(o.receiver_org)}</b>
          <div>${esc(o.receiver_addr)}</div>
          <div class="muted">接收人：${esc(o.receiver_contact)} · <span class="mask">${esc(o.receiver_phone_masked)}</span></div></div>
        <div style="flex:1;min-width:200px"><div class="muted">报到材料</div>
          ${o.report_materials.map(m=>`<span class="tag" style="margin:2px 4px 2px 0">${esc(m)}</span>`).join("")}</div>
      </div>
    </div>
    <div class="grid" style="grid-template-columns:1fr 1fr">
      <div class="card"><h3>转递链节点</h3><ul class="timeline">
        ${o.events.map((e,i)=>`<li class="${i===o.events.length-1?'now':'done'}">
          <b>[${e.node_cn}] ${esc(e.action)}</b>
          <div class="muted" style="white-space:pre-wrap">${esc(Object.entries(e.detail).map(([k,v])=>`${k}: ${typeof v==='object'?JSON.stringify(v):v}`).join("\n"))}</div>
          <div class="t">${e.actor_role?ROLE_CN[e.actor_role]||e.actor_role:""} · ${fmt(e.created_at)}</div></li>`).join("")}
      </ul></div>
      <div class="card"><h3>运单 / 退件链（并排）</h3><div class="waybills">${wbs||'<div class="muted">尚未寄出</div>'}</div></div>
    </div>
    <div class="card">${excs}${packing}${changes}${waivers}</div>`;
}

/* ------------------------------------------------------------- 学生端 */
async function studentView() {
  const o = await api("/api/my-order");
  if (!o || !o.id) {
    $("#app").innerHTML = `<div class="card"><h2>去向申报</h2><p class="muted" style="margin-bottom:12px">你还没有本批次的转递单，请填写档案接收信息。</p>${declareForm()}</div>`;
    bindDeclare();
    return;
  }
  window._order = o;
  const lastEvent = o.events[o.events.length-1];
  const proofs = await api(`/api/proofs?student_id=${o.student.id}`).catch(()=>({proofs:[]}));
  const canAddrChange = ["declared","verified"].includes(o.status);
  const canReship = o.status === "returned";
  $("#app").innerHTML = `
    <div class="hero">
      <div><div class="muted" style="color:#cfe0f4">档案接收单位</div>
        <div class="org">${esc(o.receiver_org)}</div>
        <div class="kv">${esc(o.receiver_addr)}<br>接收人：${esc(o.receiver_contact)}</div></div>
      <div class="node"><div class="muted" style="color:#cfe0f4">当前节点</div>
        <div class="n">${lastEvent?`[${lastEvent.node_cn}]`:""} ${esc(lastEvent?lastEvent.action:"")}</div>
        <div class="kv">${o.status_cn} · ${fmt(o.updated_at)}</div></div>
    </div>
    <div class="card" style="margin-top:14px">
      <div class="actions">
        ${canAddrChange?`<button onclick="askAddressChange()">申请更正接收地址（寄出前）</button>`:""}
        ${canReship?`<button onclick="askReship()">申请退回重寄</button>`:""}
        ${o.status==="shipped"||o.status==="exception"?`<span class="muted">档案已寄出：如需改址，只能等退回后发起重寄</span>`:""}
      </div>
    </div>
    ${orderView(o)}
    <div class="card"><h3>本地证明材料（默认遮罩存档）</h3>
      <table><tr><th>类型</th><th>文件</th><th>登记时间</th><th></th></tr>
      ${(proofs.proofs||[]).map(p=>`<tr><td>${esc(p.doc_type)}</td><td class="mono">${esc(p.file_name)}</td>
        <td>${fmt(p.created_at)}</td><td><button class="sm ghost" onclick="viewProof(${p.id})">查看脱敏件</button></td></tr>`).join("")
        ||'<tr><td colspan="4" class="muted">暂无材料</td></tr>'}</table></div>`;
}

function declareForm() {
  return `<div class="dual">
    <div class="field"><label>档案接收单位 *</label><input id="f_org" placeholder="例：XX公司人力资源部 / XX人才服务中心"></div>
    <div class="field"><label>接收人 *</label><input id="f_contact"></div>
    <div class="field" style="grid-column:1/3"><label>接收地址 *</label><input id="f_addr"></div>
    <div class="field"><label>接收单位电话 *</label><input id="f_phone"></div>
    <div class="field"><label>报到材料（逗号分隔）</label><input id="f_mats" value="报到证,毕业证书复印件,学位证书复印件"></div>
  </div><button onclick="submitDeclare()">提交去向申报</button>`;
}
function bindDeclare() {}
async function submitDeclare() {
  await guard(async () => {
    const materials = $("#f_mats").value.split(/[,，]/).map(s=>s.trim()).filter(Boolean);
    const body = {receiver_org:$("#f_org").value, receiver_addr:$("#f_addr").value,
      receiver_contact:$("#f_contact").value, receiver_phone:$("#f_phone").value, report_materials:materials};
    await post("/api/orders/declare", body);
    toast("去向已申报，等待院系核对毕业状态"); render();
  });
}

function addressForm(o, reship) {
  return `<h3>${reship?"退回重寄申请":"更正接收地址（寄出前须院系复核）"}</h3>
    <div class="field"><label>接收单位</label><input id="c_org" value="${esc(o.receiver_org)}"></div>
    <div class="field"><label>接收地址 *</label><input id="c_addr" value="${esc(o.receiver_addr)}"></div>
    <div class="dual"><div class="field"><label>接收人</label><input id="c_contact" value="${esc(o.receiver_contact)}"></div>
      <div class="field"><label>电话</label><input id="c_phone" value="${esc(o.receiver_phone_self||o.receiver_phone_masked)}"></div></div>
    <div class="field"><label>事由</label><textarea id="c_reason">${reship?"原接收单位拒收，申请退回重寄":"签约单位信息变更"}</textarea></div>
    <div class="actions"><button id="c_ok">提交${reship?"重寄":"更正"}申请</button>
      <button class="gray" onclick="closeDialog()">取消</button></div>`;
}
async function askAddressChange() {
  dialog(addressForm(window._order,false));
  $("#c_ok").onclick = () => guard(async () => {
    await post(`/api/orders/${window._order.id}/address?action=address`, collectChange());
    closeDialog(); toast("更正申请已提交，等待院系复核（寄出前完成）"); render();
  });
}
async function askReship() {
  dialog(addressForm(window._order,true));
  $("#c_ok").onclick = () => guard(async () => {
    await post(`/api/orders/${window._order.id}/reship?action=reship`, collectChange());
    closeDialog(); toast("重寄申请已提交，等待院系复核新地址"); render();
  });
}
function collectChange() {
  return {receiver_org:$("#c_org").value, receiver_addr:$("#c_addr").value,
    receiver_contact:$("#c_contact").value, receiver_phone:$("#c_phone").value.replace(/\*/g,"0"),
    reason:$("#c_reason").value};
}
async function viewProof(id) {
  await guard(async () => {
    const d = await api(`/api/proofs/${id}`);
    dialog(`<h3>${esc(d.meta.doc_type)}（脱敏副本）</h3>
      <pre style="white-space:pre-wrap;background:#f7f9fc;border:1px solid var(--line);border-radius:8px;padding:12px;font-size:12.5px">${esc(d.content)}</pre>
      <div class="actions"><button class="gray" onclick="closeDialog()">关闭</button></div>`);
  });
}
function toggleId(btn, full) {
  const span = btn.previousElementSibling;
  if (btn.dataset.on) { span.textContent = "**************" + full.slice(-4); btn.textContent="显示完整"; delete btn.dataset.on; }
  else { span.textContent = full; btn.textContent="遮罩"; btn.dataset.on="1"; }
}

/* ------------------------------------------------------------- 院系端 */
let deptTab = "verify";
async function departmentView() {
  const tabs = [["verify","毕业核对"],["address","地址/重寄复核"],["orders","全部转递单"]];
  let html = `<div class="tabs">${tabs.map(([k,n])=>`<button class="${deptTab===k?'active':''}" onclick="deptTab='${k}';render()">${n}</button>`).join("")}</div>`;
  if (deptTab === "verify") {
    const list = await api("/api/orders?status=declared");
    html += `<div class="card"><h2>待核对毕业状态（${list.orders.length}）</h2>
      <p class="muted">院系核对毕业资格；封装后袋内敏感材料对院系不可见。</p>
      <table><tr><th>转递单</th><th>学生</th><th>学号</th><th>学院/专业</th><th>接收单位</th><th>操作</th></tr>
      ${list.orders.map(o=>`<tr><td class="mono">${esc(o.order_no)}</td><td>${esc(o.student.name)}</td>
        <td class="mono">${esc(o.student.student_no)}</td><td>${esc(o.student.college)}<br><span class="muted">${esc(o.student.major)}</span></td>
        <td>${esc(o.receiver_org)}<br><span class="muted">${esc(o.receiver_addr)}</span></td>
        <td class="right"><button class="sm" onclick="verify(${o.id},true)">核对通过</button>
        <button class="sm red" onclick="verify(${o.id},false)">不通过</button></td></tr>`).join("")
        ||'<tr><td colspan="6" class="muted">暂无待核对单据</td></tr>'}</table></div>`;
  } else if (deptTab === "address") {
    const [a, r] = await Promise.all([api("/api/changes?kind=address"), api("/api/changes?kind=reship")]);
    const row = c => `<tr><td>${c.kind==="address"?"寄出前更正":"退回重寄"}</td><td>#${c.order_id}</td>
      <td>${esc(c.new_org)}<br><span class="muted">${esc(c.new_addr)}</span><div class="muted">${esc(c.reason)}</div></td>
      <td>${esc(c.requested_by)}<br><span class="muted">${fmt(c.created_at)}</span></td>
      <td class="right"><button class="sm" onclick="reviewChange(${c.id},'${c.kind}',true)">复核通过</button>
      <button class="sm red" onclick="reviewChange(${c.id},'${c.kind}',false)">驳回</button></td></tr>`;
    html += `<div class="card"><h2>待复核（${a.changes.length+r.changes.length}）</h2>
      <table><tr><th>类型</th><th>单据</th><th>新接收信息</th><th>申请人</th><th>操作</th></tr>
      ${[...a.changes,...r.changes].map(row).join("")||'<tr><td colspan="5" class="muted">暂无待复核申请</td></tr>'}</table></div>`;
  } else {
    const list = await api("/api/orders");
    html += ordersTable(list.orders, true);
  }
  $("#app").innerHTML = html;
}
async function verify(id, ok) {
  const note = ok ? "毕业资格与学籍信息核对一致" : prompt("核对不通过原因：", "毕业资格待确认") || "";
  if (!ok && !note) return;
  await guard(async () => {
    await post(`/api/orders/${id}/verify?action=verify`, {confirmed:ok, note});
    toast(ok?"已核对通过":"转递单已作废，学生可重新申报"); render();
  });
}
async function reviewChange(id, kind, ok) {
  const note = prompt(ok?"复核意见（可空）":"驳回原因：", ok?"信息属实，同意变更":"") ;
  if (!ok && note===null) return;
  await guard(async () => {
    const base = kind === "address" ? "address-changes" : "reship-changes";
    await post(`/api/${base}/${id}`, {approve:ok, note:note||""});
    toast(ok ? (kind==="reship"?"重寄地址已通过，待档案室重新加封":"地址已更新"):"已驳回"); render();
  });
}

/* ------------------------------------------------------------- 档案室端 */
let archTab = "batch";
async function archiveView() {
  const tabs = [["batch","批次封签一览"],["pack","待封装"],["ship","待寄出"],["exc","异常处置"],["orders","全部单据"]];
  let html = `<div class="tabs">${tabs.map(([k,n])=>{
    const dot = k==="exc" ? `<span class="count" id="excCount"></span>`:"";
    return `<button class="${archTab===k?'active':''}" onclick="archTab='${k}';render()">${n}${dot}</button>`;
  }).join("")}</div>
  <div class="card" style="margin-bottom:12px"><div class="row">
    <span class="muted">模拟物流时钟：</span>
    <button class="sm ghost" onclick="tick(1)">推进 1 跳</button>
    <button class="sm ghost" onclick="tick(3)">推进 3 跳</button>
    <span class="muted">（驱动在途运单产生签收/拒收/破损/滞留事件）</span></div></div>`;

  if (archTab === "batch") {
    const list = await api("/api/orders");
    const openExc = list.orders.reduce((n,o)=>n+(o.status==="exception"),0);
    html += batchTable(list.orders, openExc);
  } else if (archTab === "pack") {
    const list = await api("/api/orders?status=verified");
    html += `<div class="card"><h2>待封装（${list.orders.length}）</h2>
      <table><tr><th>转递单</th><th>学生</th><th>接收单位</th><th>操作</th></tr>
      ${list.orders.map(o=>`<tr><td class="mono">${esc(o.order_no)}</td><td>${esc(o.student.name)} <span class="muted">${esc(o.student.student_no)}</span></td>
        <td>${esc(o.receiver_org)}</td>
        <td><button class="sm" onclick="doPack(${o.id})">封装并加封签</button></td></tr>`).join("")
        ||'<tr><td colspan="4" class="muted">暂无待封装单据</td></tr>'}</table></div>`;
  } else if (archTab === "ship") {
    const list = await api("/api/orders?status=packed");
    html += `<div class="card"><h2>待寄出（${list.orders.length}）</h2>
      <table><tr><th>转递单</th><th>封签号</th><th>学生</th><th>物流剧本（模拟）</th><th>操作</th></tr>
      ${list.orders.map(o=>`<tr><td class="mono">${esc(o.order_no)}</td><td class="mono"><b>${esc(o.packing?o.packing.seal_no:"")}</b></td>
        <td>${esc(o.student.name)}</td>
        <td><select id="sc_${o.id}" style="width:150px">
          ${["normal","refuse","damage","stagnant","confusion"].map(s=>`<option value="${s}">${SCENARIO_CN[s]}</option>`).join("")}
        </select></td><td><button class="sm" onclick="doShip(${o.id})">寄出</button></td></tr>`).join("")
        ||'<tr><td colspan="5" class="muted">暂无待寄出单据</td></tr>'}</table></div>`;
  } else if (archTab === "exc") {
    const list = await api("/api/orders?status=exception");
    html += `<div class="card"><h2>异常处置（${list.orders.length}）</h2>` +
      list.orders.map(o=>exceptionCard(o)).join("") +
      (list.orders.length?'':'<div class="muted">没有待处理异常 🎉</div>') + `</div>`;
  } else {
    const list = await api("/api/orders");
    html += ordersTable(list.orders, false);
  }
  $("#app").innerHTML = html;
}

function batchTable(orders, openExc) {
  // 按毕业批次密集列出：封签号是档案室工作台的主索引
  const batches = [...new Set(orders.map(o=>o.grad_batch))];
  return batches.map(b=>{
    const rows = orders.filter(o=>o.grad_batch===b);
    return `<div class="card"><h2>${b} 届毕业批次 · ${rows.length} 份
      <span class="muted">（点击封签号查看单据与退件链）</span></h2>
      <table class="dense"><tr><th>封签号</th><th>转递单</th><th>学生</th><th>学号</th><th>身份证尾号</th>
      <th>接收单位</th><th>运单</th><th>状态</th></tr>
      ${rows.map(o=>{
        const seals = [];
        return `<tr class="clickable" onclick="openOrder(${o.id})">
          <td class="mono"><b>${esc(o.packing?o.packing.seal_no:"—")}</b></td>
          <td class="mono">${esc(o.order_no)}</td>
          <td>${esc(o.student.name)}</td>
          <td class="mono">${esc(o.student.student_no)}</td>
          <td class="mono">${esc(o.student.id_card_tail)}</td>
          <td>${esc(o.receiver_org)}</td>
          <td class="mono">${o.waybills?o.waybills.map(w=>esc(w.tracking_no)+(w.leg>1?"↩":"")).join("<br>"):""}</td>
          <td>${pill(o.status)}</td></tr>`;
      }).join("")}</table></div>`;
  }).join("");
}

function exceptionCard(o) {
  const notes = o.open_exceptions.map(e =>
    `<div class="muted">· ${EXC_CN[e.type]}：${esc(e.note)}</div>`).join("");
  const acts = o.open_exceptions.map(e => {
    if (e.type === "reject") return `<span class="muted">拒收件退回后由学生发起重寄。当前：${o.status==="returned"?"等待学生申请重寄":"等待物流退回"}</span>`;
    if (e.type === "seal_damaged") return `<div class="actions">
        <button class="sm" onclick="sealCheck(${o.id},true)">内封完好·重新加封</button>
        <button class="sm red" onclick="sealCheck(${o.id},false)">内封破损·开箱清点</button></div>`;
    if (e.type === "missing_page") return `<span class="muted">缺页豁免已提交学校审批人，档案室无权自行批准。</span>`;
    if (e.type === "stagnant") return `<div class="actions">
        <button class="sm" onclick="urge(${o.id})">催办物流</button>
        <button class="sm red" onclick="recall(${o.id})">召回档案</button></div>`;
    if (e.type === "confusion") return `<div class="actions">
        <button class="sm" onclick="confusionCheck(${o.id})">同名身份核对（身份证后四位+学号）</button></div>`;
    return "";
  }).join("");
  return `<div class="exc-box" style="margin:8px 0">
    <h4>#${o.id} ${esc(o.student.name)}（${esc(o.student.student_no)}）
      <span>${o.open_exceptions.map(excPill).join("")}</span></h4>
    <div class="muted">${esc(o.receiver_org)}</div>${notes}${acts}</div>`;
}

async function doPack(id){ await guard(async()=>{ await post(`/api/orders/${id}/pack?action=pack`,{}); toast("已封装并生成封签号"); render(); }); }
async function doShip(id){
  const scenario = $(`#sc_${id}`).value;
  await guard(async()=>{ await post(`/api/orders/${id}/ship?action=ship`,{scenario}); toast("档案已寄出，运单已生成"); render(); });
}
async function tick(n){ await guard(async()=>{ const d=await post(`/api/logistics/tick?n=${n}`,{}); toast(d.changed.length?`物流动态：${d.changed.join("、")}`:"本跳无物流动态"); render(); }); }
async function sealCheck(id, intact){
  const note = prompt(intact?"核验备注（可空）":"破损情况记录：", intact?"":"外封签撕裂，内封骑缝章断开") ?? "";
  await guard(async()=>{ await post(`/api/orders/${id}/seal-check?action=seal-check`,{intact,note}); toast("封签核验完成"); render(); });
}
async function urge(id){ await guard(async()=>{ await post(`/api/orders/${id}/urge?action=urge`,{}); toast("已催办，物流将恢复流转，继续点“推进”"); render(); }); }
async function recall(id){ await guard(async()=>{ await post(`/api/orders/${id}/recall?action=recall`,{}); toast("已召回，等待退回档案室"); render(); }); }
function confusionCheck(id){
  dialog(`<h3>同名档案身份核对</h3><p class="muted">签收单位发现同名档案，须凭身份证后四位 + 学号双重比对。</p>
    <div class="field"><label>身份证后四位</label><input id="cf_tail" maxlength="4" placeholder="例：4521"></div>
    <div class="field"><label>学号</label><input id="cf_no" placeholder="例：2021001002"></div>
    <div class="actions"><button onclick="submitConfusion(${id})">确认归属</button><button class="gray" onclick="closeDialog()">取消</button></div>`);
}
async function submitConfusion(id){
  await guard(async()=>{
    await post(`/api/orders/${id}/confusion-check?action=confusion-check`,
      {id_card_tail:$("#cf_tail").value, student_no:$("#cf_no").value});
    closeDialog(); toast("身份匹配一致，档案归属确认并归档"); render();
  });
}
async function repackFromOrder(id){ await guard(async()=>{ await post(`/api/orders/${id}/repack?action=repack`,{}); toast("已重新加封，可寄出"); closeDialog(); render(); }); }
async function shipFromOrder(id, scenario){ await guard(async()=>{ await post(`/api/orders/${id}/ship?action=ship`,{scenario}); toast("已寄出"); closeDialog(); render(); }); }
async function openOrder(id){
  await guard(async()=>{
    const o = await api(`/api/orders/${id}`);
    const actions = roleActions(o);
    dialog(`<div style="max-height:76vh;overflow:auto">${orderView(o)}
      <div class="card" style="margin-top:12px"><h3>办理</h3><div class="actions">${actions||'<span class="muted">当前身份/状态无可办操作</span>'}</div></div></div>`);
    window._dlgOrder = o;
  });
}
function roleActions(o){
  const a = [];
  if (me.role==="archive"){
    if(o.status==="verified") a.push(`<button onclick="repackFromOrder(${o.id})">重新加封</button>`);
    if(o.status==="packed") a.push(`<button onclick="gotoShip()">前往「待寄出」页寄出</button>`);
  }
  return a.join("");
}
function gotoShip(){ closeDialog(); archTab="ship"; render(); }

/* ------------------------------------------------------------- 审批人端 */
let apprTab = "waivers";
async function approverView() {
  const tabs = [["waivers","缺页豁免审批"],["orders","全部单据（可查敏感项）"]];
  let html = `<div class="tabs">${tabs.map(([k,n])=>`<button class="${apprTab===k?'active':''}" onclick="apprTab='${k}';render()">${n}</button>`).join("")}</div>`;
  if (apprTab==="waivers") {
    const d = await api("/api/waivers");
    html += `<div class="card"><h2>待审批（${d.waivers.length}）</h2><p class="muted">职责分离：豁免由档案室发起，审批人独立批准；发起人不得审批本人申请。</p>
      ${d.waivers.map(w=>`<div class="exc-box"><h4>转递单 #${w.order_id} · 缺页材料
        <span class="muted">待审批</span></h4><div>${esc(JSON.parse(w.materials||"[]").join("、"))}</div>
        <div class="muted">理由：${esc(w.reason)}</div><div class="muted">发起人：${esc(w.request_by)} · ${fmt(w.created_at)}</div>
        <div class="actions"><button class="sm" onclick="reviewWaiver(${w.id},true)">批准豁免</button>
        <button class="sm red" onclick="reviewWaiver(${w.id},false)">驳回（须补齐材料）</button></div></div>`).join("")
        ||'<div class="muted">暂无待批申请</div>'}</div>`;
  } else {
    const list = await api("/api/orders");
    html += ordersTable(list.orders, false);
  }
  $("#app").innerHTML = html;
}
async function reviewWaiver(id, ok){
  const note = prompt(ok?"批准意见（可空）":"驳回原因：", ok?"同意豁免，缺页材料由学校另行补寄":"") ;
  if(!ok && note===null) return;
  await guard(async()=>{ await post(`/api/waivers/${id}`,{approve:ok,note:note||""}); toast(ok?"已批准豁免":"已驳回"); render(); });
}

/* ------------------------------------------------------------- 共用列表 */
function ordersTable(orders, deptMode) {
  return `<div class="card"><h2>转递单总览（${orders.length}）</h2>
    <table><tr><th>转递单</th><th>学生</th><th>批次</th><th>接收单位</th><th>封签号</th><th>运单链</th><th>状态</th><th></th></tr>
    ${orders.map(o=>`<tr><td class="mono">${esc(o.order_no)}</td><td>${esc(o.student.name)}</td>
      <td>${esc(o.student.grad_batch)}</td><td>${esc(o.receiver_org)}</td>
      <td class="mono">${esc(o.packing?o.packing.seal_no:"—")}</td>
      <td class="mono">${o.waybills?o.waybills.map(w=>(w.leg>1?"↩":"")+esc(w.tracking_no)).join("<br>"):""}</td>
      <td>${pill(o.status)}</td>
      <td><button class="sm ghost" onclick="openOrder(${o.id})">详情</button></td></tr>`).join("")}
    </table></div>`;
}

/* ------------------------------------------------------------- 启动 */
async function render() {
  if (!me) { $("#app").innerHTML = ""; return; }
  $("#roleBadge").textContent = ROLE_CN[me.role];
  await guard(async () => {
    if (me.role === "student") await studentView();
    if (me.role === "department") await departmentView();
    if (me.role === "archive") await archiveView();
    if (me.role === "approver") await approverView();
  });
}

function initSelect() {
  const sel = $("#userSel");
  sel.innerHTML = USERS.map(u=>`<option value="${u.id}">${esc(u.name)}</option>`).join("");
  sel.value = localStorage.getItem("uid") || "";
  sel.onchange = async () => {
    localStorage.setItem("uid", sel.value);
    if (!sel.value) { me = null; render(); return; }
    me = USERS.find(u=>u.id===sel.value);
    render();
  };
  if (sel.value) { me = USERS.find(u=>u.id===sel.value); render(); }
}
$("#refreshBtn").onclick = () => render();
initSelect();

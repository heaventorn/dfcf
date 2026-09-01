# -*- coding: utf-8 -*-
"""
个人投资组合 · 持仓管理器
========================
在浏览器里可视化增/删/改 positions.json 里的持仓（股票 / ETF）。

运行方式：
    python position_manager.py
    然后浏览器打开 http://127.0.0.1:8765

功能：
    - 查看当前持仓
    - 买入/加入：输入名称、代码、买入价格、数量、备注 → 新增（代码已存在则更新成本与数量）
    - 卖出/删除：选择持仓，输入卖出数量（默认全部）→ 减仓或删除
    - 修改：直接编辑成本 / 数量 / 备注

说明：
    - 仅监听 127.0.0.1（本机），不对外开放
    - 修改结果实时写回同目录 positions.json
"""
import json
import os
import re
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
POS_FILE = os.path.join(BASE_DIR, "positions.json")
PORT = 8765


# ---------------- 持仓数据读写 ----------------
def load_positions():
    with open(POS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_positions(data):
    with open(POS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def guess_market(code):
    """按代码前缀判断市场（sh 沪 / sz 深），用于自动生成 tx 字段"""
    code = (code or "").strip()
    if code.startswith(("159", "150")):
        return "sz"
    if code.startswith(("51", "56", "58", "588", "60", "68", "11")):
        return "sh"
    return "sz"  # 0/3 开头等默认深市


def find_pos(data, code):
    """按代码查找持仓，返回 (索引, 条目) 或 (None, None)"""
    code = str(code).strip()
    for i, p in enumerate(data.get("positions", [])):
        if str(p.get("code", "")).strip() == code:
            return i, p
    return None, None


# ---------------- 接口处理 ----------------
def api_positions():
    return {"ok": True, "positions": load_positions().get("positions", [])}


def api_add(params):
    name = (params.get("name") or "").strip()
    code = (params.get("code") or "").strip()
    cost = params.get("cost")
    shares = params.get("shares")
    note = (params.get("note") or "").strip()
    if not name or not code:
        return {"ok": False, "msg": "名称和代码不能为空"}
    try:
        cost = float(cost)
        shares = int(float(shares))
    except (TypeError, ValueError):
        return {"ok": False, "msg": "价格/数量必须是数字"}
    if cost <= 0 or shares <= 0:
        return {"ok": False, "msg": "价格和数量必须大于 0"}

    data = load_positions()
    idx, _ = find_pos(data, code)
    if idx is not None:
        # 已存在 → 更新成本（加权平均）与数量
        old = data["positions"][idx]
        total_old = float(old.get("cost", 0)) * int(old.get("shares", 0))
        total_new = cost * shares
        new_shares = int(old.get("shares", 0)) + shares
        new_cost = round((total_old + total_new) / new_shares, 4) if new_shares else cost
        old["cost"] = new_cost
        old["shares"] = new_shares
        if note:
            old["note"] = note
        data["positions"][idx] = old
    else:
        data["positions"].append({
            "tx": guess_market(code) + code,
            "code": code,
            "name": name,
            "cost": cost,
            "shares": shares,
            "note": note or "",
        })
    save_positions(data)
    return {"ok": True, "msg": "已买入/加入", "positions": data["positions"]}


def api_delete(params):
    code = (params.get("code") or "").strip()
    sell_shares = params.get("shares")
    if not code:
        return {"ok": False, "msg": "请提供要卖出的代码"}
    data = load_positions()
    idx, pos = find_pos(data, code)
    if idx is None:
        return {"ok": False, "msg": f"未找到代码 {code} 的持仓"}

    if sell_shares is None or sell_shares == "":
        # 默认全部卖出 → 删除
        data["positions"].pop(idx)
    else:
        try:
            sell_shares = int(float(sell_shares))
        except (TypeError, ValueError):
            return {"ok": False, "msg": "卖出数量必须是数字"}
        hold = int(pos.get("shares", 0))
        if sell_shares >= hold:
            data["positions"].pop(idx)
        else:
            data["positions"][idx]["shares"] = hold - sell_shares
    save_positions(data)
    return {"ok": True, "msg": "已卖出/删除", "positions": data["positions"]}


def api_update(params):
    code = (params.get("code") or "").strip()
    field = params.get("field")
    value = params.get("value")
    if not code or field not in ("cost", "shares", "note"):
        return {"ok": False, "msg": "参数错误"}
    data = load_positions()
    idx, pos = find_pos(data, code)
    if idx is None:
        return {"ok": False, "msg": f"未找到代码 {code} 的持仓"}
    if field == "cost":
        try:
            value = round(float(value), 4)
        except (TypeError, ValueError):
            return {"ok": False, "msg": "价格必须是数字"}
        if value <= 0:
            return {"ok": False, "msg": "价格必须大于 0"}
    elif field == "shares":
        try:
            value = int(float(value))
        except (TypeError, ValueError):
            return {"ok": False, "msg": "数量必须是数字"}
        if value <= 0:
            return {"ok": False, "msg": "数量必须大于 0"}
    data["positions"][idx][field] = value
    save_positions(data)
    return {"ok": True, "msg": "已修改", "positions": data["positions"]}


# ---------------- HTTP 服务 ----------------
PAGE_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>个人投资组合 · 持仓管理</title>
<style>
  * { box-sizing: border-box; }
  body { font-family: "Microsoft YaHei", sans-serif; background:#f5f7fa; margin:0; padding:24px; color:#1f2d3d; }
  .wrap { max-width: 920px; margin: 0 auto; }
  h1 { font-size: 20px; margin: 0 0 4px; }
  .sub { color:#8a94a3; font-size: 12px; margin-bottom: 18px; }
  .toolbar { display:flex; gap:12px; margin-bottom: 16px; }
  .btn { border:none; border-radius:8px; padding:10px 18px; font-size:14px; font-weight:700; cursor:pointer; color:#fff; }
  .btn-buy { background:#12a15d; }
  .btn-sell { background:#e64545; }
  .btn:hover { opacity:.88; }
  .card { background:#fff; border-radius:10px; padding:16px; box-shadow:0 1px 6px rgba(0,0,0,.06); }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th, td { padding:9px 8px; text-align:left; border-bottom:1px solid #eef1f5; }
  th { color:#8a94a3; font-weight:600; }
  .empty { color:#8a94a3; text-align:center; padding:24px; }
  .op a { color:#3b82f6; text-decoration:none; margin-right:10px; cursor:pointer; }
  .op a.del { color:#e64545; }
  .modal-mask { position:fixed; inset:0; background:rgba(0,0,0,.4); display:none; align-items:center; justify-content:center; z-index:99; }
  .modal-mask.show { display:flex; }
  .modal { background:#fff; border-radius:12px; padding:22px; width:380px; max-width:92vw; }
  .modal h3 { margin:0 0 14px; font-size:16px; }
  .field { margin-bottom:12px; }
  .field label { display:block; font-size:12px; color:#4a5568; margin-bottom:4px; }
  .field input, .field select, .field textarea { width:100%; padding:8px 10px; border:1px solid #d7dce3; border-radius:6px; font-size:13px; }
  .modal-btns { display:flex; justify-content:flex-end; gap:10px; margin-top:16px; }
  .modal-btns button { border:none; border-radius:6px; padding:8px 16px; font-size:13px; cursor:pointer; }
  .btn-ok { background:#1f2d3d; color:#fff; }
  .btn-no { background:#eef1f5; color:#4a5568; }
  .msg { margin:10px 0 0; font-size:12px; }
  .msg.ok { color:#12a15d; }
  .msg.err { color:#e64545; }
</style>
</head>
<body>
<div class="wrap">
  <h1>📊 个人投资组合 · 持仓管理</h1>
  <div class="sub">修改结果实时写入 positions.json，下次运行爬虫即生效 · 服务仅本机可用</div>

  <div class="toolbar">
    <button class="btn btn-buy" onclick="openBuy()">＋ 买入 / 加入</button>
    <button class="btn btn-sell" onclick="openSell()">－ 卖出 / 删除</button>
    <button class="btn" style="background:#5b6b7b;" onclick="refresh()">↻ 刷新</button>
  </div>

  <div class="card">
    <table>
      <thead>
        <tr><th>名称</th><th>代码</th><th>市场</th><th>成本</th><th>数量</th><th>成本市值</th><th>备注</th><th>操作</th></tr>
      </thead>
      <tbody id="tbody"></tbody>
    </table>
    <div id="empty" class="empty" style="display:none;">暂无持仓，点上方"买入/加入"开始添加</div>
  </div>
  <div id="msg" class="msg"></div>
</div>

<!-- 买入弹窗 -->
<div class="modal-mask" id="maskBuy">
  <div class="modal">
    <h3>买入 / 加入持仓</h3>
    <div class="field"><label>名称 *</label><input id="buy_name" placeholder="如：中兵红箭 / 红利ETF"></div>
    <div class="field"><label>代码 *（6 位数字，自动识别沪/深）</label><input id="buy_code" placeholder="如：000519 / 511260"></div>
    <div class="field"><label>买入价格 *</label><input id="buy_cost" type="number" step="0.001" placeholder="如：14.84"></div>
    <div class="field"><label>数量 *（股/份）</label><input id="buy_shares" type="number" step="1" placeholder="如：100"></div>
    <div class="field"><label>备注（可选）</label><input id="buy_note" placeholder="如：军工·特种装备"></div>
    <div class="modal-btns">
      <button class="btn-no" onclick="closeModal('maskBuy')">取消</button>
      <button class="btn-ok" onclick="doBuy()">确认买入</button>
    </div>
    <div id="msgBuy" class="msg"></div>
  </div>
</div>

<!-- 卖出弹窗 -->
<div class="modal-mask" id="maskSell">
  <div class="modal">
    <h3>卖出 / 删除持仓</h3>
    <div class="field"><label>选择持仓 *</label><select id="sell_code"></select></div>
    <div class="field"><label>卖出数量（留空 = 全部卖出并删除）</label><input id="sell_shares" type="number" step="1" placeholder="留空则删除该持仓"></div>
    <div class="modal-btns">
      <button class="btn-no" onclick="closeModal('maskSell')">取消</button>
      <button class="btn-ok" onclick="doSell()">确认卖出</button>
    </div>
    <div id="msgSell" class="msg"></div>
  </div>
</div>

<script>
let POS = [];
const $ = id => document.getElementById(id);

async function api(path, data) {
  const opt = { method: data ? "POST" : "GET", headers: { "Content-Type": "application/json" } };
  if (data) opt.body = JSON.stringify(data);
  const r = await fetch(path, opt);
  return r.json();
}

function render() {
  const tb = $("tbody");
  tb.innerHTML = "";
  $("empty").style.display = POS.length ? "none" : "block";
  POS.forEach(p => {
    const tr = document.createElement("tr");
    const mv = (p.cost * p.shares).toFixed(2);
    tr.innerHTML = `<td><b>${p.name}</b></td><td>${p.code}</td><td>${(p.tx||"").slice(0,2)}</td>` +
      `<td><a class="op" onclick="editField('${p.code}','cost')">${p.cost}</a></td>` +
      `<td><a class="op" onclick="editField('${p.code}','shares')">${p.shares}</a></td>` +
      `<td>${mv}</td><td style="color:#8a94a3;font-size:12px">${p.note||""}</td>` +
      `<td class="op"><a onclick="editField('${p.code}','note')">改备注</a><a class="del" onclick="delPos('${p.code}')">删除</a></td>`;
    tb.appendChild(tr);
  });
}

async function refresh() {
  const r = await api("/api/positions");
  if (r.ok) { POS = r.positions; render(); showMsg("", ""); }
}

function showMsg(txt, cls) { const m = $("msg"); m.textContent = txt; m.className = "msg " + (cls||""); }
function showIn(id, txt, cls) { const m = $(id); m.textContent = txt; m.className = "msg " + (cls||""); }
function closeModal(id) { $(id).classList.remove("show"); $(id).querySelector(".msg").textContent=""; }

function openBuy() { $("maskBuy").classList.add("show"); $("buy_name").focus(); }
function openSell() {
  const sel = $("sell_code");
  sel.innerHTML = "";
  if (!POS.length) { showMsg("暂无持仓可卖", "err"); return; }
  POS.forEach(p => { const o = document.createElement("option"); o.value = p.code; o.textContent = `${p.name} (${p.code}) · ${p.shares}份`; sel.appendChild(o); });
  $("maskSell").classList.add("show");
}

async function doBuy() {
  const r = await api("/api/add", {
    name: $("buy_name").value, code: $("buy_code").value,
    cost: $("buy_cost").value, shares: $("buy_shares").value, note: $("buy_note").value
  });
  showIn("msgBuy", r.msg, r.ok ? "ok" : "err");
  if (r.ok) {
    ["buy_name","buy_code","buy_cost","buy_shares","buy_note"].forEach(id => $(id).value = "");
    POS = r.positions; render(); closeModal("maskBuy"); showMsg("已买入/加入 ✓", "ok");
  }
}

async function doSell() {
  const r = await api("/api/delete", { code: $("sell_code").value, shares: $("sell_shares").value });
  showIn("msgSell", r.msg, r.ok ? "ok" : "err");
  if (r.ok) { $("sell_shares").value = ""; POS = r.positions; render(); closeModal("maskSell"); showMsg("已卖出/删除 ✓", "ok"); }
}

async function editField(code, field) {
  let label = { cost: "新的成本价", shares: "新的数量", note: "新的备注" }[field];
  let v = prompt(`请输入${label}（代码 ${code}）`);
  if (v === null || v === "") return;
  const r = await api("/api/update", { code, field, value: v });
  if (r.ok) { POS = r.positions; render(); showMsg("已修改 ✓", "ok"); }
  else showMsg(r.msg, "err");
}

async function delPos(code) {
  if (!confirm(`确认删除 ${code} 的持仓？`)) return;
  const r = await api("/api/delete", { code, shares: "" });
  if (r.ok) { POS = r.positions; render(); showMsg("已删除 ✓", "ok"); }
  else showMsg(r.msg, "err");
}

// 回车提交
["buy_name","buy_code","buy_cost","buy_shares","buy_note"].forEach((id,i) => {
  $(id).addEventListener("keydown", e => { if (e.key === "Enter") doBuy(); });
});

refresh();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html, status=200):
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/" or parsed.path == "/index.html":
            self._send_html(PAGE_HTML)
        elif parsed.path == "/api/positions":
            self._send_json(api_positions())
        else:
            self._send_json({"ok": False, "msg": "not found"}, 404)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        params = self._read_json()
        try:
            if parsed.path == "/api/add":
                self._send_json(api_add(params))
            elif parsed.path == "/api/delete":
                self._send_json(api_delete(params))
            elif parsed.path == "/api/update":
                self._send_json(api_update(params))
            else:
                self._send_json({"ok": False, "msg": "not found"}, 404)
        except Exception as e:
            self._send_json({"ok": False, "msg": f"服务端错误: {e}"})

    def log_message(self, *args):
        pass  # 静默日志


def main():
    # 启动前检查 positions.json 是否存在
    if not os.path.exists(POS_FILE):
        print("[错误] 找不到 positions.json：", POS_FILE)
        sys.exit(1)
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print("=" * 52)
    print("  个人投资组合 · 持仓管理已启动")
    print(f"  请在浏览器打开： http://127.0.0.1:{PORT}")
    print("  关闭：按 Ctrl+C")
    print("=" * 52)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
        server.server_close()


if __name__ == "__main__":
    main()

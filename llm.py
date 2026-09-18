# -*- coding: utf-8 -*-
"""大模型分析：把当前持仓快照喂给 DeepSeek，换一段人话分析。

模型在这里的位置是**解释器**，不是决策器：
  决策来自 strategy.json（目标权重）+ 账本（实际持仓）+ 回测数字，
  模型只负责把这些数字读成人话、指出你可能忽略的风险。
  它不产生买卖信号 —— 否则你就得解释「为什么昨天它还看好、今天就变卦」，
  而那种解释对长期持有毫无价值。

Key 读取顺序：环境变量 DEEPSEEK_API_KEY，其次项目里 secrets.json 的
deepseek_api_key。两者都没有就明确报「没配 key」，不要假装能分析。
"""

import json
import os
import sys

import requests

import config
import strategy

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def key_status():
    """返回 (key, 来源说明)。key 为 None 表示没配。"""
    cfg = strategy.llm_cfg()
    env = cfg.get("key_env") or "DEEPSEEK_API_KEY"
    k = os.environ.get(env)
    if k and k.strip():
        return k.strip(), "环境变量 %s" % env
    f = cfg.get("key_file") or "secrets.json"
    path = f if os.path.isabs(f) else os.path.join(config.BASE_DIR, f)
    field = cfg.get("key_field") or "deepseek_api_key"
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        k = (data.get(field) or "").strip()
        if k:
            return k, "%s 的 %s" % (f, field)
    except FileNotFoundError:
        pass
    except Exception as e:
        return None, "读 %s 出错：%s" % (f, e)
    return None, "没配（环境变量 %s 或 %s 的 %s）" % (env, f, field)


def available():
    k, why = key_status()
    cfg = strategy.llm_cfg()
    return {
        "enabled": bool(cfg.get("enabled", True)),
        "has_key": bool(k),
        "key_source": why,
        "base_url": cfg.get("base_url"),
        "model": cfg.get("model"),
        "models": cfg.get("models") or [cfg.get("model")],
    }


# ------------------------------------------------------------------ 快照

def snapshot(sid=None, drag=0.0):
    """喂给模型的事实。只放数字和已确认的事实，不放我的猜测。"""
    import backtest
    sid = sid or strategy.active_id()
    mon = strategy.monitor(sid)
    bt = backtest.result(sid, drag=drag, with_curve=False)

    def pct(x):
        return None if x is None else round(x * 100, 2)

    hist = {
        "区间": "%s ~ %s（%.1f 年，含 2008 全球金融危机）" % (
            bt.get("start"), bt.get("end"), bt.get("years") or 0),
        "年化收益%": pct(bt.get("cagr")),
        "最大回撤%": pct(bt.get("mdd")),
        "最大回撤发生日": bt.get("mdd_at"),
        "年化波动%": pct(bt.get("vol")),
        "最差单年%": pct(bt.get("worst_year")),
        "最差滚动3年%": pct(bt.get("worst_3y")),
        "最长 underwater（年）": round(bt.get("under_years") or 0, 1),
        "分年收益": {k: pct(v) for k, v in (bt.get("yearly") or {}).items()},
        "危机窗口": {k: {"区间收益%": pct(v["ret"]), "区间回撤%": pct(v["mdd"])}
                     for k, v in (bt.get("crisis") or {}).items() if v},
    }

    alloc = [{
        "资产桶": b["name"], "分组": b["group"],
        "目标%": pct(b["target_w"]), "实际%": pct(b["actual_w"]),
        "漂移%": pct(b["drift"]), "超阈值": b["over_band"],
        "可买标的": b["instruments"], "备注": b["note"],
    } for b in mon["buckets"]]

    gate = mon["gate"]
    bk = strategy.buckets()

    def named(w):
        return {bk[k]["name"]: pct(v) for k, v in sorted(w.items(),
                                                         key=lambda kv: -kv[1])}

    # 把「打折前 / 打折后」两套目标都给它，并说清哪些算风险资产。
    # 上次没给，模型只好自己猜黄金算不算风险腿 —— 猜出来的东西不该混进分析里。
    gate_on = mon.get("gate_used")
    risk_state = mon.get("risk")
    live_w = strategy.target_weights(sid, gate_on=bool(gate_on),
                                     risk_state=risk_state)
    plain_w = strategy.target_weights(sid, gate_on=False, risk_state=None)
    risk_keys = (strategy.gate_cfg().get("risk_buckets") or [])

    return {
        "策略": mon["name"], "策略ID": sid,
        "再平衡规则": "每年 %s 月第一个交易日；单桶漂移超过 %.0f%% 提示" % (
            "/".join(map(str, strategy.rebalance_months())), mon["band"] * 100),
        "目标权重": {
            "当前生效": named(live_w),
            "趋势闸未触发时": named(plain_w),
            "哪些算风险资产（会被打折）": [bk[k]["name"] for k in risk_keys if k in bk],
            "注": "「当前生效」这一列才是现在的目标；另一列是趋势闸没触发时的样子。"
                  "现金桶里同时装着没买成资产的闲钱和货币ETF。",
        },
        "账户": {"可投资总额": round(mon["capital"], 2),
                 "已投入": round(mon["invested"], 2),
                 "现金": round(mon["cash"], 2)},
        "趋势闸": {
            "指数": gate.get("index"), "日期": gate.get("date"),
            "收盘": gate.get("close"), "200日均线": gate.get("ma"),
            "偏离%": pct(gate.get("gap")), "是否触发": gate.get("on"),
            "说明": "触发时该策略的风险资产目标权重砍半、转入货币",
        } if gate.get("ok") else {"说明": gate.get("msg")},
        "风险减仓层": {
            "是否生效": (risk_state or {}).get("active"),
            "风险资产预算": (risk_state or {}).get("multiplier"),
            "摘要": (risk_state or {}).get("summary"),
            "空中飞人": (risk_state or {}).get("airman"),
            "H00300回撤": (risk_state or {}).get("drawdown"),
            "各层": (risk_state or {}).get("layers"),
        },
        "当前配置": alloc,
        "场内溢价": {
            "口径": "场内价 ÷ IOPV 实时估值；历史分位用当日收盘 ÷ 当日单位净值",
            "数据时点": mon.get("premium_asof"),
            "各资产桶": [
                {"桶": b["name"], "标的": (b.get("premium") or {}).get("code"),
                 "溢价%": pct((b["premium"] or {}).get("premium"))
                           if (b.get("premium") or {}).get("premium") is not None else None,
                 "三年分位%": (round((b["premium"]).get("percentile"), 1)
                              if (b.get("premium") or {}).get("percentile") is not None
                              else None),
                 "三年中位%": pct(((b["premium"] or {}).get("stats") or {}).get("median")),
                 "判定": (b.get("premium_gate") or {}).get("level"),
                 "说明": (b.get("premium_gate") or {}).get("reason")}
                for b in mon["buckets"]
                if (b.get("premium") or {}).get("premium") is not None
            ],
            "注": "溢价超过上限的桶，买卖计划会给出「暂缓买入」，不按漂移强行买。"
                  "买高溢价 ETF 等于一进场就先亏掉这部分，等溢价回落或走场外申购。",
        },
        "未分类持仓": [{"代码": p["code"], "名称": p["name"],
                        "市值": round(p["value"], 2)}
                       for p in mon["unclassified"]],
        "历史回测": hist,
    }


SYSTEM_PROMPT = """你是一位极其克制的家庭资产配置顾问，服务对象是一个中国普通家庭。

他们的钱分成三层：保险和半年生活费（不参与投资）、无风险资产、中高风险资产。
目标是**长期持有、晚上睡得着**，不是跑赢谁。

回答要求：
1. 用中文，200-450 字，不要用 Markdown 标题，可以用短段落和少量「-」列表。
2. 先看数字：实际比例和目标比例差在哪、差多少、是否超过再平衡阈值。
3. 明确说出当前最主要的一个风险，以及它在历史回测里对应的最大回撤大概是多少。
4. 给「现在该不该动手」的明确态度：如果漂移没超阈值，就直接说不用动。
5. 必须诚实指出数据局限：回测用的是中证全收益指数、红利低波指数 2017 年才
   发布（2017 年前是回填，存在幸存者偏差）、样本起点在 2006 年牛市之前、
   未扣 ETF 费率。不要把这些说成"仅供参考"敷衍过去，要具体。
6. 不要预测点位、不要推荐个股、不要用"牛市/熊市即将到来"这种话。
  你无法预测，就不许假装能预测。
7. 如果用户的问题超出你看到的数据，直接说「这个我不知道」。
8. 思考过程请尽量短：不要在思考里复述快照的数字，直接想结论。
   省下来的额度留给正文。"""


def analyze(question=None, sid=None, model=None, drag=0.0, timeout=None,
            max_tokens=None):
    """调一次模型，返回 {ok, text, usage, model, snapshot}。"""
    cfg = strategy.llm_cfg()
    if not cfg.get("enabled", True):
        return {"ok": False, "msg": "strategy.json 里 llm.enabled 是关的"}
    key, why = key_status()
    if not key:
        return {"ok": False, "msg": "没有可用的 API Key：%s" % why,
                "hint": "在项目目录建一个 secrets.json，内容 "
                        '{"deepseek_api_key": "sk-..."}，或者设环境变量 '
                        "DEEPSEEK_API_KEY，然后重开服务。"}

    snap = snapshot(sid, drag=drag)
    user = ["下面是这个家庭账户此刻的真实快照（JSON）：",
            json.dumps(snap, ensure_ascii=False, indent=1)]
    if question:
        user.append("\n我想问的是：%s" % question)
    else:
        user.append("\n请分析：现在的配置和家庭目标是否一致，"
                    "有没有需要马上处理的事，以及最应该警惕什么。")

    base = (cfg.get("base_url") or "https://api.deepseek.com").rstrip("/")
    model = model or cfg.get("model") or "deepseek-chat"
    tried = []
    for m in [model] + [x for x in (cfg.get("models") or []) if x != model]:
        tried.append(m)
        try:
            r = requests.post(
                base + "/chat/completions",
                headers={"Authorization": "Bearer %s" % key,
                         "Content-Type": "application/json"},
                json={"model": m,
                      "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                                   {"role": "user", "content": "\n".join(user)}],
                      "temperature": 0.3,
                      "max_tokens": int(max_tokens or cfg.get("max_tokens") or 1600),
                      "stream": False},
                timeout=int(timeout or cfg.get("timeout") or 90))
        except Exception as e:
            return {"ok": False, "msg": "请求失败：%s: %s" % (type(e).__name__, e),
                    "model": m, "snapshot": snap}

        if r.status_code == 200:
            try:
                d = r.json()
            except Exception:
                d = {}
            ch = (d.get("choices") or [{}])[0] or {}
            msg = ch.get("message") or {}
            text = (msg.get("content") or "").strip()
            think = (msg.get("reasoning_content") or "").strip()
            fin = ch.get("finish_reason")
            if text:
                return {"ok": True, "text": text, "model": m,
                        "usage": d.get("usage"), "snapshot": snap,
                        "reasoning": think or None,
                        "at": __import__("datetime").datetime.now().strftime("%H:%M:%S")}
            if think:
                # deepseek-flash / v4-pro 都是先思考再作答的两段式模型。
                # 如果正文是空的、思考却有一大段，九成是 max_tokens 被思考吃光了。
                return {"ok": False, "model": m, "snapshot": snap,
                        "msg": "模型只输出了思考过程、没给正文（finish_reason=%s，"
                               "思考 %d 字）。把 strategy.json 里的 llm.max_tokens "
                               "调大再试。" % (fin, len(think))}
            return {"ok": False, "model": m, "snapshot": snap,
                    "msg": "模型返回是空的（finish_reason=%s）" % fin}

        body = (r.text or "")[:300]
        # 模型名不对就换下一个再试，别的错误（401 / 402 / 429）不必重试
        if r.status_code in (400, 404) and "model" in body.lower():
            continue
        return {"ok": False, "model": m,
                "msg": "接口返回 %s：%s" % (r.status_code, body),
                "snapshot": snap}

    return {"ok": False, "model": model, "snapshot": snap,
            "msg": "这些模型名都不可用：%s。检查 strategy.json 的 llm.model。"
                   % "、".join(tried)}


def main():
    import argparse
    p = argparse.ArgumentParser(description="DeepSeek 分析（命令行试跑）")
    p.add_argument("--strategy", default=None)
    p.add_argument("--question", default=None)
    p.add_argument("--model", default=None)
    p.add_argument("--snapshot", action="store_true", help="只打印快照，不调模型")
    a = p.parse_args()
    if a.snapshot:
        print(json.dumps(snapshot(a.strategy), ensure_ascii=False, indent=1))
        return 0
    print("Key:", key_status()[1])
    res = analyze(a.question, sid=a.strategy, model=a.model)
    if res.get("ok"):
        print("模型:", res["model"], "| usage:", res.get("usage"))
        print("-" * 60)
        print(res["text"])
        return 0
    print("失败:", res.get("msg"))
    if res.get("hint"):
        print(res["hint"])
    return 1


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""策略页的 HTTP 接口层。

单独一个模块，是为了让 live_server.py 只多三行 —— 新闻 / 个股那套服务
已经很稳，不想因为加功能把它碰坏。路由不认识就返回 None，交回原处理器。
"""

import threading
import time

import backtest
import bars
import config
import llm
import rebalance
import strategy

_warm_lock = threading.Lock()
_warm_state = {"running": False, "at": None, "result": None, "error": None}


def _proxies():
    return [c.get("proxy") for c in strategy.buckets().values() if c.get("proxy")]


def warm_background():
    """后台预热代理序列。

    第一次跑（或缓存过期）时，七条中证全历史要几十秒。放在后台线程里，
    页面先出来、显示「取数中」，而不是卡住几十秒白屏。
    """
    with _warm_lock:
        if _warm_state["running"]:
            return _warm_state
        _warm_state.update(running=True, error=None)

    def _job():
        try:
            res = bars.warm(_proxies())
            # 溢价历史分位要另拉净值和价格序列，同样放后台，页面先出来
            try:
                import premium
                codes = []
                for c in strategy.buckets().values():
                    codes.extend(c.get("instruments") or [])
                premium.warm(codes)
            except Exception:
                pass
            # 持仓评估要的三样（净值分红记录 / 基准指数 / 成交额）也先抓好
            try:
                import holding_eval
                holding_eval.warm()
            except Exception:
                pass
            with _warm_lock:
                _warm_state.update(result=res, at=time.strftime("%H:%M:%S"))
            backtest.reset()
        except Exception as e:
            with _warm_lock:
                _warm_state.update(error="%s: %s" % (type(e).__name__, e))
        finally:
            with _warm_lock:
                _warm_state["running"] = False

    threading.Thread(target=_job, name="bars-warm", daemon=True).start()
    return _warm_state


def _int(q, key, default=0):
    try:
        return int((q.get(key) or [default])[0])
    except (TypeError, ValueError):
        return default


def _first(q, key, default=None):
    v = q.get(key)
    return v[0] if v else default


# ------------------------------------------------------------------ GET

def route_get(path, qs):
    from urllib.parse import parse_qs
    q = parse_qs(qs)
    sid = _first(q, "id") or strategy.active_id()

    if path == "/api/strategy/list":
        items = []
        for k in strategy.list_ids():
            c = strategy.get(k)
            items.append({"id": k, "name": c.get("name"), "risk": c.get("risk"),
                          "cat": strategy.cat_of(k), "gate": bool(c.get("gate"))})
        rb = rebalance.next_rebalance()
        return 200, {
            "ok": True, "active": strategy.active_id(), "items": items,
            "account": strategy.account(),
            "llm": llm.available(),
            "rebalance": {"months": list(strategy.rebalance_months()),
                          "band": strategy.band(),
                          "next": rb,
                          "is_month": rebalance.is_rebalance_month()},
            "risk_overlay": strategy.risk_cfg(),
            "trade": {"lot": strategy.lot(), "fee_rate": strategy.fee_rate()},
            "warm": dict(_warm_state),
            "cache": bars.cache_info(),
        }

    if path == "/api/strategy/board":
        drag = (_int(q, "drag_bp") or 0) / 10000.0
        try:
            mon = strategy.monitor(sid)
        except KeyError as e:
            return 404, {"ok": False, "msg": str(e)}
        try:
            plan = rebalance.plan(sid)
        except Exception as e:
            plan = {"ok": False, "msg": "%s: %s" % (type(e).__name__, e)}
        try:
            bt = backtest.result(sid, drag=drag, with_curve=True)
        except Exception as e:
            bt = {"ok": False, "msg": "%s: %s" % (type(e).__name__, e)}
        try:
            metrics = backtest.result_all(drag=drag)
            curves = {}
            for k in metrics:
                r = backtest.result(k, drag=drag, with_curve=True)
                curves[k] = r.get("curve") or []
        except Exception:
            metrics, curves = {}, {}
        return 200, {"ok": True, "id": sid, "monitor": mon, "plan": plan,
                     "backtest": bt, "metrics": metrics, "curves": curves,
                     "drag_bp": int(drag * 10000),
                     "warm": dict(_warm_state)}

    if path == "/api/strategy/backtest":
        drag = (_int(q, "drag_bp") or 0) / 10000.0
        try:
            return 200, {"ok": True,
                         "result": backtest.result(sid, drag=drag, with_curve=True),
                         "all": backtest.result_all(drag=drag)}
        except Exception as e:
            return 500, {"ok": False, "msg": "%s: %s" % (type(e).__name__, e)}

    if path == "/api/strategy/monitor":
        try:
            return 200, strategy.monitor(sid)
        except Exception as e:
            return 500, {"ok": False, "msg": "%s: %s" % (type(e).__name__, e)}

    if path == "/api/strategy/books":
        cap = _first(q, "capital")
        try:
            return 200, strategy.books(float(cap) if cap not in (None, "") else None)
        except Exception as e:
            return 500, {"ok": False, "msg": "%s: %s" % (type(e).__name__, e)}

    if path == "/api/strategy/plan":
        try:
            return 200, rebalance.plan(sid)
        except Exception as e:
            return 500, {"ok": False, "msg": "%s: %s" % (type(e).__name__, e)}

    if path == "/api/strategy/history":
        return 200, {"ok": True, "items": rebalance.history(_int(q, "limit", 50) or 50)}

    if path == "/api/strategy/snapshot":
        try:
            return 200, {"ok": True, "snapshot": llm.snapshot(sid)}
        except Exception as e:
            return 500, {"ok": False, "msg": "%s: %s" % (type(e).__name__, e)}

    if path == "/api/strategy/track":
        # 盈亏追踪：纸上建仓的净值 / 累计 / 年化 + 同期回测对照。
        # record=1 顺手记一条当天快照（同一天只留一条，重复调用无害）。
        try:
            import tracker
            rec = _first(q, "record") in ("1", "true", "yes")
            return 200, tracker.report(record=rec, json_out=True)
        except Exception as e:
            return 500, {"ok": False, "msg": "%s: %s" % (type(e).__name__, e)}

    if path == "/api/strategy/warm":
        return 200, {"ok": True, "warm": dict(warm_background())}

    if path == "/api/strategy/check":
        # 策略体检：日快照 / 周风险简检 / 月全面体检 / 季大考 + 三条报警线。
        # level 不传就按「今天该跑哪一档」自动选。
        try:
            import monitor
            lv = _first(q, "level")
            if _first(q, "status") in ("1", "true", "yes"):
                return 200, monitor.status()
            rec = _first(q, "record")
            # 默认只算不写：要落盘必须显式 record=1。
            # 以前默认写，结果「打开体检 tab」也会记一条，非交易日就是一条空行。
            return 200, monitor.run(lv, record=rec in ("1", "true", "yes"))
        except Exception as e:
            return 500, {"ok": False, "msg": "%s: %s" % (type(e).__name__, e)}

    if path == "/api/strategy/holdings":
        # 持仓评估：一个月一次，只看产品本身（跟踪误差 / 成交额 / 溢价）。
        # 默认读当月缓存；force=1 强制重算（页面上的「重算」按钮）。
        try:
            import holding_eval
            if _first(q, "summary") in ("1", "true", "yes"):
                return 200, {"ok": True, **holding_eval.summary()}
            return 200, holding_eval.evaluate(
                force=_first(q, "force") in ("1", "true", "yes"))
        except Exception as e:
            return 500, {"ok": False, "msg": "%s: %s" % (type(e).__name__, e)}

    if path == "/api/strategy/ledger":
        # 现金流台账：汇总（含持仓市值 / 累计盈亏）+ 最近的流水。
        try:
            import ledger
            r = ledger.summary()
            r["entries"] = ledger.entries(limit=_int(q, "limit", 200) or 200)
            return 200, r
        except Exception as e:
            return 500, {"ok": False, "msg": "%s: %s" % (type(e).__name__, e)}

    if path == "/api/strategy/journal":
        # 事件日志：一行一条，只追加。kind 可选，用来筛「只看报警」这类。
        try:
            import journal
            return 200, {"ok": True,
                         "items": journal.read(limit=_int(q, "limit", 120) or 120,
                                               kind=_first(q, "kind")),
                         "stats": journal.stats()}
        except Exception as e:
            return 500, {"ok": False, "msg": "%s: %s" % (type(e).__name__, e)}

    return None


# ------------------------------------------------------------------ POST

def route_post(path, params):
    params = params or {}
    if path == "/api/strategy/active":
        sid = (params.get("id") or "").strip()
        try:
            strategy.set_active(sid)
            return 200, {"ok": True, "active": sid,
                         "msg": "已切到 %s" % strategy.get(sid).get("name")}
        except KeyError as e:
            return 400, {"ok": False, "msg": str(e)}

    if path == "/api/strategy/account":
        cfg = strategy.load()
        acc = cfg.setdefault("account", {})
        try:
            if params.get("capital") not in (None, ""):
                acc["capital"] = float(params["capital"])
            if params.get("cash_manual") not in (None, ""):
                acc["cash_manual"] = float(params["cash_manual"])
        except (TypeError, ValueError):
            return 400, {"ok": False, "msg": "金额必须是数字"}
        if acc.get("capital", 0) < 0 or acc.get("cash_manual", 0) < 0:
            return 400, {"ok": False, "msg": "金额不能是负数"}
        strategy.save(cfg)
        try:
            import journal
            journal.log("系统", "改可投资总额",
                        detail="capital → ¥%s · cash_manual → ¥%s"
                               % (format(float(acc.get("capital") or 0), ",.0f"),
                                  format(float(acc.get("cash_manual") or 0), ",.0f")),
                        level="act")
        except Exception:
            pass
        return 200, {"ok": True, "account": strategy.account(), "msg": "已保存"}

    if path == "/api/strategy/seed":
        # 一键全部建仓：给「还没买任何东西」用的入口。写账本 + 台账 + 日志。
        try:
            res = rebalance.seed(sid=params.get("id"),
                                 capital=params.get("capital") or None,
                                 force=bool(params.get("force")),
                                 note=params.get("note") or "",
                                 paper=params.get("paper") is not False,
                                 dry=bool(params.get("dry")))
        except Exception as e:
            return 500, {"ok": False, "msg": "%s: %s" % (type(e).__name__, e)}
        return (200 if res.get("ok") else 400), res

    if path == "/api/strategy/ledger":
        # 手工记一笔：入金 / 出金 / 分红 / 利息 / 手续费 / 调整。
        try:
            import ledger
            e = ledger.add(params.get("kind"), params.get("amount"),
                           fee=params.get("fee") or 0,
                           code=params.get("code") or "",
                           bucket=params.get("bucket") or "",
                           shares=params.get("shares") or 0,
                           price=params.get("price") or 0,
                           note=params.get("note") or "",
                           paper=bool(params.get("paper")))
        except ValueError as e:
            return 400, {"ok": False, "msg": str(e)}
        except Exception as e:
            return 500, {"ok": False, "msg": "%s: %s" % (type(e).__name__, e)}
        return 200, {"ok": True, "entry": e, "msg": "已记一笔"}

    if path == "/api/strategy/journal":
        # 手工记一条事件（比如「今天跟妈妈讨论后决定不改」）。
        try:
            import journal
            e = journal.log(params.get("kind") or "系统",
                            params.get("title") or "(手工记录)",
                            detail=params.get("detail") or "",
                            level=params.get("level") or "info",
                            sid=params.get("id"))
        except Exception as e:
            return 500, {"ok": False, "msg": "%s: %s" % (type(e).__name__, e)}
        return 200, {"ok": True, "entry": e, "msg": "已记一条"}

    if path == "/api/strategy/apply":
        rows = params.get("rows") or []
        if not isinstance(rows, list):
            return 400, {"ok": False, "msg": "rows 必须是数组"}
        try:
            res = rebalance.apply(rows, sid=params.get("id"),
                                  note=params.get("note") or "",
                                  paper=(params.get("paper")
                                         if "paper" in params else None))
        except Exception as e:
            return 500, {"ok": False, "msg": "%s: %s" % (type(e).__name__, e)}
        if not res.get("applied"):
            res["msg"] = "没有可执行的买卖（都被跳过或金额为 0）"
        else:
            res["msg"] = ("已写入账本 %d 笔，现金流台账同步记了 %d 笔"
                          % (res["applied"], res.get("ledger") or 0))
        return 200, res

    if path == "/api/strategy/paper":
        # 实验仓位 → 真实成交：只去掉 paper 标记，价格 / 份额一分不动。
        try:
            res = rebalance.mark_real(note=params.get("note") or "")
        except Exception as e:
            return 500, {"ok": False, "msg": "%s: %s" % (type(e).__name__, e)}
        return (200 if res.get("ok") else 400), res

    if path == "/api/strategy/track/init":
        # 按当前买卖计划（重新）建账。已有的账会先存成 track.bak.json。
        try:
            import tracker
            res = tracker.init(capital=params.get("capital") or None,
                               sid=params.get("id") or None,
                               force=bool(params.get("force")))
            if res.get("ok"):
                res["report"].pop("legs", None)   # 报告太大，页面自己再拉一次
            return (200 if res.get("ok") else 400), res
        except Exception as e:
            return 500, {"ok": False, "msg": "%s: %s" % (type(e).__name__, e)}

    if path == "/api/strategy/llm":
        try:
            res = llm.analyze(params.get("question"), sid=params.get("id"),
                              model=(params.get("model") or "").strip() or None)
        except Exception as e:
            return 500, {"ok": False, "msg": "%s: %s" % (type(e).__name__, e)}
        # 快照很长，页面不用，去掉再回，省一半流量
        res.pop("snapshot", None)
        return (200 if res.get("ok") else 503), res

    if path == "/api/strategy/reload":
        strategy.reload()
        backtest.reset()
        return 200, {"ok": True, "msg": "已重读 strategy.json"}

    return None

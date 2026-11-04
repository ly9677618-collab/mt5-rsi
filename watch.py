#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MT5 watchlist RSI alert (15m) -> Telegram

Monitors exactly the symbols you keep in MetaTrader Market Watch and every
15 minutes (right after the M15 candle closes) sends the ones with RSI(14) <= 35.

Data: TradingView scanner (real-time, free).
Runs continuously on GitHub Actions and re-arms itself (cron is unreliable).

Env: TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, GH_TOKEN, GITHUB_REPOSITORY
"""

import html
import json
import os
import sys
import time
import urllib.parse
import urllib.request
import datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(HERE, "config.json")
TZ = dt.timezone(dt.timedelta(hours=3))
UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Origin": "https://www.tradingview.com",
    "Referer": "https://www.tradingview.com/",
    "Accept": "application/json",
}

DEFAULT_CFG = {
    "threshold": 35.0,
    "timeframe": 15,
    "rsi_field": "RSI|15",
    "duration": 19800,
    "send_when_empty": False,
    "symbols": {
        "BCHUSD": "BINANCE:BCHUSDT",
        "XAUUSD": "OANDA:XAUUSD",
        "EURUSD": "FX:EURUSD",
        "EURGBP": "FX:EURGBP",
        "CADJPY": "FX:CADJPY",
        "AUDJPY": "FX:AUDJPY",
        "GBPUSD": "FX:GBPUSD",
        "AUDUSD": "FX:AUDUSD",
        "NZDUSD": "FX:NZDUSD",
        "USDCHF": "FX:USDCHF",
        "USDCAD": "FX:USDCAD",
    },
}


def load_cfg():
    cfg = dict(DEFAULT_CFG)
    if os.path.exists(CFG_PATH):
        with open(CFG_PATH, encoding="utf-8-sig") as f:
            cfg.update(json.load(f))
    cfg["token"] = os.environ.get("TELEGRAM_TOKEN") or cfg.get("token")
    cfg["chat_id"] = os.environ.get("TELEGRAM_CHAT_ID") or cfg.get("chat_id")
    return cfg


def get_json(url, tries=3):
    last = None
    for _ in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode())
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.5)
    raise last


def quote(tv_symbol, field):
    url = (f"https://scanner.tradingview.com/symbol?symbol={urllib.parse.quote(tv_symbol)}"
           f"&fields=close,change,{urllib.parse.quote(field)}")
    d = get_json(url)
    return {
        "close": d.get("close"),
        "change": d.get("change"),
        "rsi": d.get(field),
    }


def fmt_price(p):
    if p is None:
        return "—"
    if p >= 1000:
        return f"{p:,.2f}"
    if p >= 100:
        return f"{p:.3f}"
    if p >= 1:
        return f"{p:.4f}"
    return f"{p:.5f}"


def send(cfg, text):
    url = f"https://api.telegram.org/bot{cfg['token']}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": cfg["chat_id"],
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode()
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA["User-Agent"]})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode())


def scan(cfg):
    field = cfg["rsi_field"]
    out = []
    for mt5_name, tv in cfg["symbols"].items():
        try:
            q = quote(tv, field)
            if q["rsi"] is None:
                continue
            out.append({"name": mt5_name, "tv": tv, **q})
        except Exception as e:  # noqa: BLE001
            print(f"{mt5_name}: fetch error {e!r}", flush=True)
        time.sleep(0.2)
    out.sort(key=lambda x: x["rsi"])
    return out


def build_message(cfg, rows, now):
    thr = float(cfg["threshold"])
    tf = int(cfg["timeframe"])
    hits = [r for r in rows if r["rsi"] <= thr]
    near = [r for r in rows if thr < r["rsi"] <= thr + 4][:3]

    candle_close = now.replace(minute=(now.minute // tf) * tf, second=0, microsecond=0)
    candle_open = candle_close - dt.timedelta(minutes=tf)

    head = (
        f"📉 <b>RSI ≤ {thr:g}</b> — فريم {tf} دقيقة\n"
        f"🕐 <b>{now:%H:%M}</b> بغداد · شمعة <code>{candle_open:%H:%M}–{candle_close:%H:%M}</code>\n"
        f"📊 وصلوا الحد: <b>{len(hits)}</b> من {len(rows)}\n\n"
    )

    if not hits:
        body = "😴 <b>ماكو ولا زوج تحت " + f"{thr:g}" + " هذي الشمعة</b>\n"
        if near:
            body += "\n🔎 الأقرب:\n" + "\n".join(
                f"• <code>{r['name']}</code> — RSI {r['rsi']:.1f}" for r in near)
        return head + body + "\n\n<i>TradingView · RSI-14</i>"

    header = f"{'#':>3}  {'PAIR':<8} {'RSI':>6} {'PRICE':>11} {'CHG':>7}"
    lines = [header, "─" * len(header)]
    for i, r in enumerate(hits, 1):
        chg = r["change"] if r["change"] is not None else 0.0
        lines.append(
            f"{i:>3}  {r['name']:<8} {r['rsi']:>6.1f} {fmt_price(r['close']):>11} {chg:>6.2f}%"
        )

    foot = ""
    if near:
        foot = "\n🔎 الأقرب: " + " · ".join(f"{r['name']} {r['rsi']:.1f}" for r in near)
    return head + "<pre>" + "\n".join(lines) + "</pre>" + foot + "\n\n<i>TradingView · RSI-14</i>"


# ------------------------------------------------------------------ handoff
def _api(method, url, body=None):
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("no token")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "User-Agent": "rsi-watch",
    })
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.status, json.loads(r.read().decode() or "{}")


def wait_for_older_instance(max_wait=480):
    repo = os.environ.get("GITHUB_REPOSITORY")
    try:
        my_id = int(os.environ.get("GITHUB_RUN_ID") or 0)
    except ValueError:
        my_id = 0
    if not repo or not my_id:
        return
    deadline = time.time() + max_wait
    while time.time() < deadline:
        try:
            _, data = _api("GET", f"https://api.github.com/repos/{repo}/actions/runs"
                                  "?status=in_progress&per_page=30")
        except Exception:
            return
        older = [r["id"] for r in data.get("workflow_runs", []) if r["id"] < my_id]
        if not older:
            return
        print(f"waiting for older instance(s): {older}", flush=True)
        time.sleep(10)


def dispatch_next(reason):
    repo = os.environ.get("GITHUB_REPOSITORY")
    wf = os.environ.get("WORKFLOW_FILE", "watch.yml")
    ref = os.environ.get("GITHUB_REF_NAME", "main")
    if not repo:
        return False
    try:
        status, _ = _api("POST", f"https://api.github.com/repos/{repo}/actions/workflows/{wf}/dispatches",
                         {"ref": ref})
        print(f"dispatch next: {status} ({reason})", flush=True)
        return True
    except Exception as e:  # noqa: BLE001
        print("dispatch failed:", repr(e), flush=True)
        return False


def main():
    cfg = load_cfg()
    duration = int(next((sys.argv[i + 1] for i, a in enumerate(sys.argv)
                         if a == "--duration"), cfg["duration"]))

    if "--once" in sys.argv:
        rows = scan(cfg)
        msg = build_message(cfg, rows, dt.datetime.now(TZ))
        if "--dry" in sys.argv:
            print(msg)
        else:
            send(cfg, msg)
            print("sent")
        return 0

    wait_for_older_instance()
    tf = int(cfg["timeframe"])
    started = time.time()
    stop_at = started + duration
    print(f"watch start duration={duration}s tf={tf}m", flush=True)

    handoff_at = stop_at - 90
    handed = False

    while time.time() < stop_at:
        now = dt.datetime.now(TZ)
        # next boundary: minute divisible by tf, + 20s
        nxt = now.replace(second=0, microsecond=0)
        nxt += dt.timedelta(minutes=(tf - now.minute % tf) % tf)
        if nxt <= now:
            nxt += dt.timedelta(minutes=tf)
        nxt += dt.timedelta(seconds=20)
        while time.time() < nxt.timestamp() and time.time() < stop_at:
            if not handed and time.time() >= handoff_at:
                handed = dispatch_next("handoff")
            time.sleep(5)
        if time.time() >= stop_at:
            break
        try:
            rows = scan(cfg)
            thr = float(cfg["threshold"])
            hits = [r for r in rows if r["rsi"] <= thr]
            if hits or cfg.get("send_when_empty"):
                msg = build_message(cfg, rows, dt.datetime.now(TZ))
                send(cfg, msg)
                print(f"sent at {dt.datetime.now(TZ):%H:%M:%S} hits={len(hits)}", flush=True)
            else:
                print(f"no hits at {dt.datetime.now(TZ):%H:%M:%S} - skipped "
                      f"(lowest {rows[0]['name']} {rows[0]['rsi']:.1f})", flush=True)
        except Exception as e:  # noqa: BLE001
            print("cycle error:", repr(e), flush=True)

    if not handed:
        dispatch_next("end")
    print("watch end", flush=True)


if __name__ == "__main__":
    sys.exit(main())

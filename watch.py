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
STATE_PATH = os.path.join(HERE, "state.json")
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


def load_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


def save_state(st):
    try:
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(st, f)
    except Exception:  # noqa: BLE001
        pass


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


def quote(tv_symbol, field, filter_field, atr_field):
    url = (f"https://scanner.tradingview.com/symbol?symbol={urllib.parse.quote(tv_symbol)}"
           f"&fields=close,change,{urllib.parse.quote(field)},"
           f"{urllib.parse.quote(filter_field)},{urllib.parse.quote(atr_field)}")
    d = get_json(url)
    return {
        "close": d.get("close"),
        "change": d.get("change"),
        "rsi": d.get(field),
        "filt": d.get(filter_field),
        "atr": d.get(atr_field),
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
    ff = cfg.get("filter_field", "RSI|240")
    af = cfg.get("atr_field", "ATR|60")
    out = []
    for mt5_name, tv in cfg["symbols"].items():
        try:
            q = quote(tv, field, ff, af)
            if q["rsi"] is None:
                continue
            out.append({"name": mt5_name, "tv": tv, **q})
        except Exception as e:  # noqa: BLE001
            print(f"{mt5_name}: fetch error {e!r}", flush=True)
        time.sleep(0.2)
    out.sort(key=lambda x: x["rsi"])
    return out


TV_CAL_URL = "https://economic-calendar.tradingview.com/events"
FIAT = {"EUR", "GBP", "USD", "JPY", "AUD", "NZD", "CAD", "CHF", "CNY"}
# currency -> tradingview country code
TV_COUNTRY = {"EUR": "EU", "USD": "US", "GBP": "GB", "JPY": "JP",
              "AUD": "AU", "NZD": "NZ", "CAD": "CA", "CHF": "CH", "CNY": "CN"}
TV_CODES = "US,EU,GB,JP,AU,NZ,CA,CH"
_cal_cache = {"at": 0.0, "events": [], "ok": False}


def fmt12(d):
    h = d.hour % 12 or 12
    suf = "ص" if d.hour < 12 else "م"
    return f"{h}:{d.minute:02d} {suf}"


def tv_events(max_age=3600):
    now = time.time()
    if now - _cal_cache["at"] < max_age and _cal_cache["events"]:
        _cal_cache["ok"] = True
        return _cal_cache["events"]
    day0 = dt.datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    frm = day0.astimezone(dt.timezone.utc)
    to = frm + dt.timedelta(days=1)
    url = (f"{TV_CAL_URL}?from={frm:%Y-%m-%dT%H:%M:%S}.000Z"
           f"&to={to:%Y-%m-%dT%H:%M:%S}.000Z&countries={TV_CODES}")
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode())
        evs = data.get("result", [])
        if evs:
            _cal_cache.update({"events": evs, "at": now, "ok": True})
        return _cal_cache["events"]
    except Exception as e:  # noqa: BLE001
        print("calendar fetch failed:", repr(e), flush=True)
        _cal_cache["ok"] = bool(_cal_cache["events"])
        return _cal_cache["events"]


def pair_currencies(name):
    base, quote = name[:3].upper(), name[3:6].upper()
    out = []
    if base in FIAT:
        out.append(base)
    if quote in FIAT:
        out.append(quote)
    return out or ["USD"]


def red_events_today(now):
    today = now.date()
    out = []
    evs = tv_events()
    for e in evs:
        if e.get("importance") != 1:      # TradingView: 1 = high impact
            continue
        try:
            d = dt.datetime.fromisoformat(str(e["date"]).replace("Z", "+00:00")).astimezone(TZ)
        except Exception:  # noqa: BLE001
            continue
        if d.date() != today:
            continue
        country = str(e.get("country", "")).upper()
        cur = next((c for c, code in TV_COUNTRY.items() if code == country), country)
        out.append({"cur": cur, "time": d, "title": e.get("title", "")})
    out.sort(key=lambda x: x["time"])
    return out, bool(evs)


def usd_strength(rows):
    vals = []
    for r in rows:
        n = r["name"]
        ch = r.get("change") or 0.0
        if n == "BCHUSD":
            continue
        if n.endswith("USD"):
            vals.append(-ch)      # EURUSD up => USD weak
        elif n.startswith("USD"):
            vals.append(ch)       # USDCHF up => USD strong
    if not vals:
        return None
    return sum(vals) / len(vals)


def verdict(r):
    r1, r4 = r.get("rsi60"), r.get("rsi240")
    if r1 is None or r4 is None:
        return "⚪", "بيانات ناقصة"
    if r1 <= 35 and r4 <= 35:
        return "📉", "هبوط قوي — كل الفريمات تحت 35 (لا تشتري ضده)"
    if r1 >= 45 and r4 >= 45:
        return "📈", "احتمال ارتداد صعود — الفريمات الكبيرة فوق 45"
    return "⚪", "محايد — الفريمات متضاربة (ناطر تأكيد)"


def build_message(cfg, rows, now, new_hits=None):
    thr = float(cfg["threshold"])
    tf = int(cfg["timeframe"])
    hits = [r for r in rows if r["rsi"] <= thr]
    if new_hits is None:
        new_hits = hits

    candle_close = now.replace(minute=(now.minute // tf) * tf, second=0, microsecond=0)
    candle_open = candle_close - dt.timedelta(minutes=tf)

    news, news_ok = red_events_today(now)
    news_cur = {}
    for n in news:
        news_cur.setdefault(n["cur"], []).append(n)

    def news_for(pair):
        out = []
        for c in pair_currencies(pair):
            for n in news_cur.get(c, []):
                out.append(n)
        return out

    us = usd_strength(rows)
    if us is None:
        us_txt = ""
    elif us > 0.05:
        us_txt = f"💵 الدولار: <b>قوي 📈</b> ({us:+.2f}%)\n"
    elif us < -0.05:
        us_txt = f"💵 الدولار: <b>ضعيف 📉</b> ({us:+.2f}%)\n"
    else:
        us_txt = f"💵 الدولار: <b>ثابت ➖</b> ({us:+.2f}%)\n"

    f1 = float(cfg.get("filter_min", 45))
    tp_mult = float(cfg.get("tp_mult", 1.75))
    signals = [r for r in new_hits if (r.get("filt") or 0) >= f1]
    rejected = [r for r in new_hits if (r.get("filt") or 0) < f1]

    tf_label = "الساعة" if tf >= 60 else f"{tf} دقيقة"
    head = (
        f"🔔 <b>إشارة RSI — فريم {tf_label}</b>\n"
        f"🕐 <b>{fmt12(now)}</b> · شمعة <code>{fmt12(candle_open)}–{fmt12(candle_close)}</code>\n"
        f"{us_txt}\n"
    )

    if not signals:
        body = "🚫 <b>ماكو إشارة مؤكدة هذي الشمعة</b>\n"
        if rejected:
            body += "\n<i>رفضها فلتر الاتجاه (4H أقل من " + f"{f1:g}" + "):</i>\n"
            body += "\n".join(
                f"• {r['name']} — RSI {r['rsi']:.1f} · فلتر {(r.get('filt') or 0):.1f}"
                for r in rejected)
        return head + body + news_block(news, news_ok) + "\n\n<i>TradingView</i>"

    cards = []
    star = "⭐ " if 0 <= now.astimezone(dt.timezone.utc).hour < 7 else ""
    for r in signals:
        atr = r.get("atr")
        price = r["close"]
        nf = news_for(r["name"])
        soon = None
        if nf:
            fut = [n for n in nf if n["time"] > now]
            if fut:
                soon = int((min(n["time"] for n in fut) - now).total_seconds() // 60)

        if soon is not None and soon <= 30:
            title = f"⏸ <b>{r['name']}</b> — خبر بعد {soon} دقيقة"
            hint = "\n   ⚠️ <i>لا تدخل قبل الخبر — ناطره يمر</i>"
        else:
            title = f"🟢 {star}<b>ادخل شراء — {r['name']}</b>"
            hint = ""

        card = [title]
        if atr and price:
            sl = price - atr
            tp = price + atr * tp_mult
            card.append(f"   دخول <code>{fmt_price(price)}</code>")
            card.append(f"   🛑 <code>{fmt_price(sl)}</code> · 🎯 <code>{fmt_price(tp)}</code>")
        else:
            card.append(f"   دخول <code>{fmt_price(price)}</code>")
        filt = r.get("filt")
        line = f"   📊 RSI {r['rsi']:.1f} · فلتر 4H"
        line += f" {filt:.1f}" if filt is not None else " -"
        card.append(line)
        if nf:
            card.append("   🔴 " + " · ".join(
                f"{n['cur']} {fmt12(n['time'])}" for n in nf[:2]))
        if hint:
            card.append(hint.strip("\n"))
        cards.append("\n".join(card))

    body = "\n\n".join(cards)

    foot = ""
    if rejected:
        foot = "\n\n🚫 <i>رفضها الفلتر: " + " · ".join(
            f"{r['name']} ({r.get('filt') or 0:.1f})" for r in rejected) + "</i>"

    return (head + body + foot + news_block(news, news_ok)
            + "\n\n<i>وقف 1×ATR · هدف " + f"{tp_mult:g}" + "×ATR</i>")


def news_block(news, ok=True):
    if not ok:
        return "\n\n⚠️ <i>ما قدرت أجيب تقويم الأخبار هذي اللحظة</i>"
    if not news:
        return "\n\n✅ <b>ماكو أخبار حمراء اليوم</b>"
    lines = ["", "", f"🔴 <b>أخبار حمراء اليوم ({len(news)}):</b>"]
    for n in news[:8]:
        lines.append(f"• <b>{fmt12(n['time'])}</b> — {n['cur']} — {html.escape(n['title'])}")
    if len(news) > 8:
        lines.append(f"• … و{len(news) - 8} غيرها")
    return "\n".join(lines)


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
            f1 = float(cfg.get("filter_min", 45))

            st = load_state()
            now_s = dt.datetime.now(TZ).isoformat(timespec="seconds")
            cands = []
            for r in rows:
                key = r["name"]
                below = r["rsi"] <= thr
                cur = st.get(key, {})
                if not below:
                    st[key] = {"below": False, "alerted": False, "rsi": round(r["rsi"], 1)}
                    continue
                already = cur.get("alerted", False)
                if (r.get("filt") or 0) >= f1 and not already:
                    cands.append(r)
                    st[key] = {"below": True, "alerted": True, "rsi": round(r["rsi"], 1),
                               "at": now_s}
                else:
                    st[key] = {"below": True, "alerted": already,
                               "rsi": round(r["rsi"], 1)}
            save_state(st)

            if cands:
                msg = build_message(cfg, rows, dt.datetime.now(TZ), cands)
                send(cfg, msg)
                print(f"sent at {dt.datetime.now(TZ):%H:%M:%S} "
                      f"[{', '.join(r['name'] for r in cands)}]", flush=True)
            else:
                print(f"no signal at {dt.datetime.now(TZ):%H:%M:%S}", flush=True)
        except Exception as e:  # noqa: BLE001
            print("cycle error:", repr(e), flush=True)

    if not handed:
        dispatch_next("end")
    print("watch end", flush=True)


if __name__ == "__main__":
    sys.exit(main())



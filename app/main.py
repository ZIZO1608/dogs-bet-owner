from datetime import datetime
import asyncio
import json
import os
import secrets
import urllib.error
import urllib.request

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app.runner import (
    LOGOS,
    bind,
    load_bots,
    load_deposits,
    load_offers,
    notify,
    save_bots,
    save_deposits,
    save_offers,
    start_all,
    start_bot,
)

app = FastAPI(title="Dogs Bet Owner Panel")
app.add_middleware(SessionMiddleware, secret_key=os.getenv("SECRET_KEY", "dogsbet-owner-secret-change-me"))
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

RATE_API_URL = os.getenv(
    "SYRIAN_RATE_API_URL",
    "https://aumzkcizwfnvnncggfpq.supabase.co/functions/v1/exchange-rates",
)
RATE_API_KEY = os.getenv("SYRIAN_RATE_API_KEY", "")
OWNERS = {"owner": os.getenv("OWNER_PASSWORD", "owner123")}
CASHIERS = {
    "cashier": {
        "password": os.getenv("CASHIER_PASSWORD", "cashier123"),
        "name": "Default cashier",
    }
}
BOTS = load_bots()
bind(BOTS)
SETTINGS = {
    "syp_rate": 13000,
    "min_deposit": 50,
    "max_deposit": 133000,
    "min_withdraw": 700,
    "max_withdraw": 133000,
    "withdraw_fee": 15,
    "shamcash_secret1": "",
    "shamcash_secret2": "",
    "shamcash_secret3": "",
}


def get_user(request: Request):
    return request.session.get("user")


def role_of(user: str | None) -> str | None:
    if user in OWNERS:
        return "owner"
    if user in CASHIERS:
        return "cashier"
    return None


def require_login(request: Request):
    user = get_user(request)
    if not user or not role_of(user):
        return None
    return user


def page(request: Request, name: str, active: str, **extra):
    user = get_user(request)
    role = role_of(user)
    ctx = {
        "request": request,
        "user": user,
        "role": role,
        "is_owner": role == "owner",
        "active": active,
    }
    ctx.update(extra)
    return templates.TemplateResponse(name, ctx)


def apply_rate(data: dict, side: str = "sell") -> float:
    value = float(data["buy"] if side == "buy" else data["sell"])
    SETTINGS["syp_rate"] = value
    SETTINGS["rate_buy"] = float(data["buy"])
    SETTINGS["rate_sell"] = float(data["sell"])
    SETTINGS["rate_side"] = side
    SETTINGS["rate_updated"] = data.get("timestamp") or ""
    for info in BOTS.values():
        info["syp_rate"] = value
        info["rate_buy"] = SETTINGS["rate_buy"]
        info["rate_sell"] = SETTINGS["rate_sell"]
    save_bots(BOTS)
    return value


async def refresh_loop():
    while True:
        try:
            data = await asyncio.to_thread(fetch_market_rate)
            apply_rate(data, SETTINGS.get("rate_side") or "sell")
        except Exception:
            pass
        await asyncio.sleep(300)


@app.on_event("startup")
async def startup():
    try:
        BOTS.clear()
        BOTS.update(load_bots())
        bind(BOTS)
    except Exception:
        pass
    try:
        data = await asyncio.to_thread(fetch_market_rate)
        apply_rate(data, SETTINGS.get("rate_side") or "sell")
    except Exception:
        pass
    try:
        await start_all(BOTS)
    except Exception:
        pass
    asyncio.create_task(refresh_loop())


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    user = require_login(request)
    if not user:
        return RedirectResponse("/login")
    return page(
        request,
        "dashboard.html",
        "home",
        bots=list(BOTS.values()),
        cashiers=list(CASHIERS.keys()),
        settings=SETTINGS,
    )


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


@app.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    ok = OWNERS.get(username) == password or (
        username in CASHIERS and CASHIERS[username]["password"] == password
    )
    if ok:
        request.session["user"] = username
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request, "error": "خطأ في البيانات"})


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login")


@app.get("/bots", response_class=HTMLResponse)
async def bots_page(request: Request):
    user = require_login(request)
    if not user or role_of(user) != "owner":
        return RedirectResponse("/")
    return page(request, "bots.html", "bots", bots=list(BOTS.values()))


@app.get("/bots/new", response_class=HTMLResponse)
async def new_bot_page(request: Request):
    user = require_login(request)
    if not user or role_of(user) != "owner":
        return RedirectResponse("/")
    return page(request, "new_bot.html", "bots")


@app.post("/bots/new")
async def create_bot(
    request: Request,
    name: str = Form(...),
    token: str = Form(...),
    cashier_user: str = Form(""),
    cashier_pass: str = Form(""),
    generate: str = Form(""),
    shamcash_secret1: str = Form(""),
    shamcash_secret2: str = Form(""),
    shamcash_secret3: str = Form(""),
):
    user = require_login(request)
    if not user or role_of(user) != "owner":
        return RedirectResponse("/")
    bot_id = secrets.token_hex(4)
    if generate or not cashier_user:
        cashier_user = f"cashier_{bot_id[:4]}"
        cashier_pass = secrets.token_urlsafe(8)
    if cashier_user in OWNERS:
        return page(request, "new_bot.html", "bots", error="هذا الاسم محجوز للمالك")
    CASHIERS[cashier_user] = {"password": cashier_pass, "name": name}
    BOTS[bot_id] = {
        "id": bot_id,
        "name": name,
        "token": token.strip(),
        "cashier_user": cashier_user,
        "cashier_pass": cashier_pass,
        "created": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "status": "starting",
        "logo": "",
        "shamcash_secret1": shamcash_secret1.strip(),
        "shamcash_secret2": shamcash_secret2.strip(),
        "shamcash_secret3": shamcash_secret3.strip(),
        "syp_rate": SETTINGS["syp_rate"],
        "min_deposit": SETTINGS["min_deposit"],
        "max_deposit": SETTINGS["max_deposit"],
        "min_withdraw": SETTINGS["min_withdraw"],
        "max_withdraw": SETTINGS["max_withdraw"],
        "withdraw_fee": SETTINGS["withdraw_fee"],
        "actions": [],
    }
    save_bots(BOTS)
    await start_bot(bot_id, BOTS[bot_id])
    save_bots(BOTS)
    return RedirectResponse(f"/bots/{bot_id}", status_code=303)


@app.post("/bots/{bot_id}/logo")
async def upload_logo(request: Request, bot_id: str, logo: UploadFile = File(...)):
    user = require_login(request)
    if not user or role_of(user) != "owner":
        return RedirectResponse("/")
    info = BOTS.get(bot_id)
    if not info:
        return RedirectResponse("/bots")
    data = await logo.read()
    path = LOGOS / f"{bot_id}.jpg"
    path.write_bytes(data)
    info["logo"] = str(path)
    save_bots(BOTS)
    await start_bot(bot_id, info)
    save_bots(BOTS)
    return RedirectResponse("/bots?logo=1", status_code=303)


@app.get("/bots/{bot_id}", response_class=HTMLResponse)
async def bot_page(request: Request, bot_id: str):
    user = require_login(request)
    if not user or role_of(user) != "owner":
        return RedirectResponse("/")
    info = BOTS.get(bot_id)
    if not info:
        return RedirectResponse("/bots")
    info.setdefault("actions", [])
    info.setdefault("syp_rate", SETTINGS["syp_rate"])
    info.setdefault("min_deposit", SETTINGS["min_deposit"])
    info.setdefault("max_deposit", SETTINGS["max_deposit"])
    info.setdefault("min_withdraw", SETTINGS["min_withdraw"])
    info.setdefault("max_withdraw", SETTINGS["max_withdraw"])
    info.setdefault("withdraw_fee", SETTINGS["withdraw_fee"])
    info.setdefault("shamcash_secret1", "")
    info.setdefault("shamcash_secret2", "")
    info.setdefault("shamcash_secret3", "")
    return page(request, "bot.html", "bots", bot=info)


@app.post("/bots/{bot_id}/settings")
async def save_bot_settings(
    request: Request,
    bot_id: str,
    shamcash_secret1: str = Form(""),
    shamcash_secret2: str = Form(""),
    shamcash_secret3: str = Form(""),
    syp_rate: float = Form(13000),
    min_deposit: float = Form(50),
    max_deposit: float = Form(133000),
    min_withdraw: float = Form(700),
    max_withdraw: float = Form(133000),
    withdraw_fee: float = Form(15),
):
    user = require_login(request)
    if not user or role_of(user) != "owner":
        return RedirectResponse("/")
    info = BOTS.get(bot_id)
    if not info:
        return RedirectResponse("/bots")
    info.update({
        "shamcash_secret1": shamcash_secret1.strip(),
        "shamcash_secret2": shamcash_secret2.strip(),
        "shamcash_secret3": shamcash_secret3.strip(),
        "syp_rate": syp_rate,
        "min_deposit": min_deposit,
        "max_deposit": max_deposit,
        "min_withdraw": min_withdraw,
        "max_withdraw": max_withdraw,
        "withdraw_fee": withdraw_fee,
    })
    save_bots(BOTS)
    return RedirectResponse(f"/bots/{bot_id}?saved=1", status_code=303)


@app.post("/bots/{bot_id}/deposits/{deposit_id}/approve")
async def approve_bot_deposit(request: Request, bot_id: str, deposit_id: str):
    user = require_login(request)
    if not user or role_of(user) != "owner":
        return RedirectResponse("/")
    info = BOTS.get(bot_id)
    if not info:
        return RedirectResponse("/bots")
    for row in info.get("bot_deposits") or []:
        if row.get("id") == deposit_id and row.get("status") == "pending":
            row["status"] = "approved"
            players = info.get("players") or {}
            player = players.get(str(row.get("telegram_id")))
            if player is not None:
                player["balance"] = float(player.get("balance") or 0) + float(row.get("amount") or 0)
            await notify(bot_id, int(row.get("telegram_id") or 0), f"تم قبول الشحن {row.get('amount')} USD")
    save_bots(BOTS)
    return RedirectResponse(f"/bots/{bot_id}", status_code=303)


@app.get("/offers", response_class=HTMLResponse)
async def offers_page(request: Request):
    user = require_login(request)
    if not user:
        return RedirectResponse("/login")
    return page(request, "offers.html", "offers", offers=load_offers())


@app.post("/offers")
async def create_offer(
    request: Request,
    title: str = Form(...),
    code: str = Form(...),
    kind: str = Form("percent"),
    value: float = Form(...),
    max_uses: int = Form(100),
    note: str = Form(""),
):
    user = require_login(request)
    if not user:
        return RedirectResponse("/login")
    offers = load_offers()
    offers.insert(0, {
        "id": secrets.token_hex(4),
        "title": title.strip(),
        "code": code.strip().upper(),
        "kind": "fixed" if kind.strip().lower() == "fixed" else "percent",
        "value": value,
        "max_uses": max_uses,
        "used": 0,
        "note": note.strip(),
        "active": True,
        "created_by": user,
        "created": datetime.now().strftime("%Y-%m-%d %H:%M"),
    })
    save_offers(offers)
    return RedirectResponse("/offers?saved=1", status_code=303)


@app.post("/offers/{offer_id}/toggle")
async def toggle_offer(request: Request, offer_id: str):
    user = require_login(request)
    if not user:
        return RedirectResponse("/login")
    offers = load_offers()
    for offer in offers:
        if offer["id"] == offer_id:
            offer["active"] = not offer.get("active", True)
    save_offers(offers)
    return RedirectResponse("/offers", status_code=303)


@app.post("/offers/{offer_id}/delete")
async def delete_offer(request: Request, offer_id: str):
    user = require_login(request)
    if not user:
        return RedirectResponse("/login")
    save_offers([o for o in load_offers() if o["id"] != offer_id])
    return RedirectResponse("/offers", status_code=303)


@app.get("/deposits", response_class=HTMLResponse)
async def deposits_page(request: Request):
    user = require_login(request)
    if not user:
        return RedirectResponse("/login")
    rows = load_deposits()
    if role_of(user) != "owner":
        rows = [d for d in rows if d.get("cashier") == user]
    return page(request, "deposits.html", "deposits", deposits=rows)


@app.post("/deposits")
async def add_deposit(
    request: Request,
    player: str = Form(...),
    amount: float = Form(...),
    currency: str = Form("USD"),
    tx_id: str = Form(...),
    method: str = Form("ShamCash"),
    note: str = Form(""),
):
    user = require_login(request)
    if not user:
        return RedirectResponse("/login")
    rows = load_deposits()
    tx = tx_id.strip()
    if any(d.get("tx_id") == tx for d in rows):
        return RedirectResponse("/deposits?dup=1", status_code=303)
    rows.insert(0, {
        "id": secrets.token_hex(4),
        "player": player.strip(),
        "amount": amount,
        "currency": currency.strip().upper() or "USD",
        "tx_id": tx,
        "method": method.strip() or "ShamCash",
        "note": note.strip(),
        "cashier": user,
        "status": "pending",
        "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
    })
    save_deposits(rows)
    return RedirectResponse("/deposits?saved=1", status_code=303)


def _set_deposit_status(deposit_id: str, status: str):
    rows = load_deposits()
    for row in rows:
        if row["id"] == deposit_id:
            row["status"] = status
    save_deposits(rows)


@app.post("/deposits/{deposit_id}/approve")
async def approve_deposit(request: Request, deposit_id: str):
    user = require_login(request)
    if not user or role_of(user) != "owner":
        return RedirectResponse("/")
    rows = load_deposits()
    for row in rows:
        if row["id"] == deposit_id:
            row["status"] = "approved"
            telegram_id = row.get("telegram_id")
            bot_id = row.get("bot_id")
            if telegram_id and bot_id:
                await notify(bot_id, int(telegram_id), f"تم قبول الشحن {row.get('amount')} {row.get('currency', 'USD')}")
    save_deposits(rows)
    return RedirectResponse("/deposits", status_code=303)


@app.post("/deposits/{deposit_id}/reject")
async def reject_deposit(request: Request, deposit_id: str):
    user = require_login(request)
    if not user or role_of(user) != "owner":
        return RedirectResponse("/")
    _set_deposit_status(deposit_id, "rejected")
    return RedirectResponse("/deposits", status_code=303)


@app.get("/prices", response_class=HTMLResponse)
async def prices_page(request: Request):
    user = require_login(request)
    if not user:
        return RedirectResponse("/login")
    return page(request, "prices.html", "prices", settings=SETTINGS)


def fetch_market_rate() -> dict:
    if not RATE_API_KEY:
        raise RuntimeError("ضع SYRIAN_RATE_API_KEY في إعدادات Coolify")
    req = urllib.request.Request(
        RATE_API_URL,
        headers={"x-api-key": RATE_API_KEY, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"API {exc.code}: {body[:180]}") from exc
    if not data.get("success"):
        raise RuntimeError(data.get("error") or "لم يرجع API سعراً")
    usd = (data.get("data") or {}).get("USD") or data
    if "buy" not in usd or "sell" not in usd:
        raise RuntimeError("الرد لا يحتوي سعر الدولار")
    return {
        "buy": usd["buy"],
        "sell": usd["sell"],
        "timestamp": data.get("last_updated") or data.get("timestamp") or "",
    }


@app.post("/prices/refresh")
async def refresh_prices(request: Request, side: str = Form("sell")):
    user = require_login(request)
    if not user:
        return RedirectResponse("/login")
    try:
        data = fetch_market_rate()
        value = apply_rate(data, side)
    except Exception as exc:
        return page(request, "prices.html", "prices", settings=SETTINGS, rate_error=str(exc))
    return RedirectResponse("/prices?saved=1", status_code=303)


@app.post("/prices")
async def save_prices(
    request: Request,
    syp_rate: float = Form(13000),
    min_deposit: float = Form(50),
    max_deposit: float = Form(133000),
    min_withdraw: float = Form(700),
    max_withdraw: float = Form(133000),
    withdraw_fee: float = Form(15),
):
    user = require_login(request)
    if not user:
        return RedirectResponse("/login")
    SETTINGS.update({
        "syp_rate": syp_rate,
        "min_deposit": min_deposit,
        "max_deposit": max_deposit,
        "min_withdraw": min_withdraw,
        "max_withdraw": max_withdraw,
        "withdraw_fee": withdraw_fee,
    })
    return RedirectResponse("/prices?saved=1", status_code=303)


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    user = require_login(request)
    if not user or role_of(user) != "owner":
        return RedirectResponse("/")
    return page(request, "settings.html", "settings", settings=SETTINGS)


@app.post("/settings")
async def save_settings(
    request: Request,
    shamcash_secret1: str = Form(""),
    shamcash_secret2: str = Form(""),
    shamcash_secret3: str = Form(""),
):
    user = require_login(request)
    if not user or role_of(user) != "owner":
        return RedirectResponse("/")
    SETTINGS.update({
        "shamcash_secret1": shamcash_secret1,
        "shamcash_secret2": shamcash_secret2,
        "shamcash_secret3": shamcash_secret3,
    })
    return RedirectResponse("/settings?saved=1", status_code=303)


@app.get("/users", response_class=HTMLResponse)
async def users_page(request: Request):
    user = require_login(request)
    if not user or role_of(user) != "owner":
        return RedirectResponse("/")
    users = []
    for bot in BOTS.values():
        for player in (bot.get("players") or {}).values():
            users.append({
                "bot": bot.get("name"),
                "username": player.get("username"),
                "balance": player.get("balance", 0),
                "accounts": len(player.get("accounts") or []),
                "status": "active",
            })
    return page(request, "users.html", "users", users=users)

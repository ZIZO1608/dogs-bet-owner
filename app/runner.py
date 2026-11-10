import asyncio
import json
import logging
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import FSInputFile, KeyboardButton, Message, ReplyKeyboardMarkup

log = logging.getLogger("dogs.runner")
DATA = Path("/app/data")
DATA.mkdir(parents=True, exist_ok=True)
DEPOSITS = DATA / "deposits.json"
LOGOS = DATA / "logos"
LOGOS.mkdir(parents=True, exist_ok=True)

_tasks: dict[str, asyncio.Task] = {}
_bots: dict[str, Bot] = {}
_store: dict = {}


def bind(bots: dict) -> None:
    global _store
    _store = bots


def log_action(info: dict, user: str, text: str) -> None:
    info.setdefault("actions", [])
    info["actions"].insert(0, {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "user": user,
        "text": text[:180],
    })
    info["actions"] = info["actions"][:100]
    if _store:
        save_bots(_store)


def load_bots() -> dict:
    if not STORE.exists():
        return {}
    try:
        return json.loads(STORE.read_text())
    except json.JSONDecodeError:
        return {}


def load_offers() -> list:
    if not OFFERS.exists():
        return []
    try:
        return json.loads(OFFERS.read_text())
    except json.JSONDecodeError:
        return []


def save_offers(offers: list) -> None:
    OFFERS.write_text(json.dumps(offers, ensure_ascii=False, indent=2))


def load_deposits() -> list:
    if not DEPOSITS.exists():
        return []
    try:
        return json.loads(DEPOSITS.read_text())
    except json.JSONDecodeError:
        return []


def save_deposits(rows: list) -> None:
    DEPOSITS.write_text(json.dumps(rows, ensure_ascii=False, indent=2))


def menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="إنشاء حساب")],
            [KeyboardButton(text="حساباتي")],
            [KeyboardButton(text="شحن"), KeyboardButton(text="سحب")],
            [KeyboardButton(text="الأرصدة")],
            [KeyboardButton(text="الدعم")],
        ],
        resize_keyboard=True,
    )


def _set_profile_photo(token: str, logo: str) -> str | None:
    boundary = uuid.uuid4().hex
    photo = Path(logo).read_bytes()
    payload = json.dumps({"type": "static", "photo": "attach://logo"}).encode()
    body = b"".join([
        f"--{boundary}\r\n".encode(),
        b'Content-Disposition: form-data; name="photo"\r\n\r\n',
        payload,
        b"\r\n",
        f"--{boundary}\r\n".encode(),
        b'Content-Disposition: form-data; name="logo"; filename="logo.jpg"\r\n',
        b"Content-Type: image/jpeg\r\n\r\n",
        photo,
        b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ])
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/setMyProfilePhoto",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
        if not data.get("ok"):
            return str(data.get("description") or data)
        return None
    except Exception as exc:
        return str(exc)


async def _apply_brand(bot: Bot, info: dict) -> None:
    try:
        await bot.set_my_name(info["name"])
        await bot.set_my_description(f"{info['name']} — Dogs Bet")
        await bot.set_my_short_description("Dogs Bet")
    except Exception as exc:
        log.warning("brand text failed: %s", exc)
    logo = info.get("logo")
    if logo and Path(logo).exists():
        err = await asyncio.to_thread(_set_profile_photo, info["token"], logo)
        info["logo_error"] = err or ""
        if err:
            log.warning("profile photo failed: %s", err)


async def start_bot(bot_id: str, info: dict) -> None:
    old = _tasks.get(bot_id)
    if old and not old.done():
        old.cancel()
    token = info.get("token")
    if not token or ":" not in token:
        info["status"] = "missing token"
        return
    bot = Bot(token)
    _bots[bot_id] = bot
    dp = Dispatcher()

    @dp.message(CommandStart())
    async def on_start(message: Message):
        user = message.from_user.username or str(message.from_user.id)
        log_action(info, user, "/start")
        text = f"مرحباً بك في {info['name']}\nDogs Bet\nسعر الدولار: {info.get('syp_rate', '—')} ل.س"
        logo = info.get("logo")
        if logo and Path(logo).exists():
            await message.answer_photo(FSInputFile(logo), caption=text, reply_markup=menu())
        else:
            await message.answer(text, reply_markup=menu())

    @dp.message()
    async def on_any(message: Message):
        user = message.from_user.username or str(message.from_user.id)
        log_action(info, user, message.text or "رسالة")
        await message.answer("القائمة جاهزة.", reply_markup=menu())

    async def run():
        try:
            info["status"] = "running"
            await dp.start_polling(bot, handle_signals=False)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            info["status"] = f"error: {exc}"
            log.exception("bot %s stopped", bot_id)
        finally:
            await bot.session.close()

    _tasks[bot_id] = asyncio.create_task(run())
    await _apply_brand(bot, info)


async def start_all(bots: dict) -> None:
    for bot_id, info in bots.items():
        await start_bot(bot_id, info)

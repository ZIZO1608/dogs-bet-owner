import asyncio
import json
import logging
import os
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import FSInputFile, KeyboardButton, Message, ReplyKeyboardMarkup

log = logging.getLogger("dogs.runner")
DATA = Path(os.getenv("DATA_DIR", "/app/data"))
try:
    DATA.mkdir(parents=True, exist_ok=True)
except OSError:
    DATA = Path("/tmp/dogs-panel")
    DATA.mkdir(parents=True, exist_ok=True)
STORE = DATA / "bots.json"
OFFERS = DATA / "offers.json"
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


def save_bots(bots: dict) -> None:
    STORE.write_text(json.dumps(bots, ensure_ascii=False, indent=2))


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
            [KeyboardButton(text="كوبون"), KeyboardButton(text="الأرصدة")],
            [KeyboardButton(text="الدعم")],
        ],
        resize_keyboard=True,
    )


_states: dict[tuple[str, int], dict] = {}


def _player(info: dict, message: Message) -> dict:
    info.setdefault("players", {})
    key = str(message.from_user.id)
    row = info["players"].setdefault(key, {
        "telegram_id": message.from_user.id,
        "username": message.from_user.username or key,
        "name": message.from_user.full_name,
        "balance": 0,
        "accounts": [],
        "lang": "ar",
    })
    row["username"] = message.from_user.username or row["username"]
    return row


def _limits(info: dict) -> tuple[float, float]:
    return float(info.get("min_deposit") or 1), float(info.get("max_deposit") or 100000)


async def notify(bot_id: str, telegram_id: int, text: str) -> bool:
    bot = _bots.get(bot_id)
    if not bot:
        return False
    try:
        await bot.send_message(telegram_id, text)
        return True
    except Exception as exc:
        log.warning("notify failed: %s", exc)
        return False


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

    def welcome() -> str:
        return (
            f"مرحباً بك في {info['name']}\nDogs Bet\n"
            f"سعر الدولار: {info.get('syp_rate', '—')} ل.س"
        )

    async def send_home(message: Message):
        logo = info.get("logo")
        text = welcome()
        if logo and Path(logo).exists():
            await message.answer_photo(FSInputFile(logo), caption=text, reply_markup=menu())
        else:
            await message.answer(text, reply_markup=menu())

    @dp.message(CommandStart())
    async def on_start(message: Message):
        user = message.from_user.username or str(message.from_user.id)
        _player(info, message)
        log_action(info, user, "/start")
        _states.pop((bot_id, message.chat.id), None)
        await send_home(message)

    @dp.message()
    async def on_any(message: Message):
        user = message.from_user.username or str(message.from_user.id)
        text = (message.text or "").strip()
        log_action(info, user, text or "رسالة")
        player = _player(info, message)
        state = _states.get((bot_id, message.chat.id), {})
        step = state.get("step")

        if text in {"إنشاء حساب", "حساباتي", "شحن", "سحب", "كوبون", "الأرصدة", "الدعم", "/start"}:
            _states.pop((bot_id, message.chat.id), None)
            step = None

        if step == "account_user":
            username = text.replace(" ", "")[:24] or f"user{message.from_user.id}"
            password = uuid.uuid4().hex[:8]
            player["accounts"].append({"username": username, "password": password})
            save_bots(_store)
            _states.pop((bot_id, message.chat.id), None)
            await message.answer(
                f"تم إنشاء الحساب\nالمستخدم: {username}\nكلمة المرور: {password}",
                reply_markup=menu(),
            )
            return
        if step == "deposit_amount":
            try:
                amount = float(text)
            except ValueError:
                await message.answer("اكتب مبلغاً رقمياً.")
                return
            lo, hi = _limits(info)
            if amount < lo or amount > hi:
                await message.answer(f"المبلغ يجب أن يكون بين {lo} و {hi}.")
                return
            rate = float(info.get("syp_rate") or 0)
            syp = round(amount * rate) if rate else 0
            secrets = [info.get("shamcash_secret1"), info.get("shamcash_secret2"), info.get("shamcash_secret3")]
            secrets = [s for s in secrets if s]
            _states[(bot_id, message.chat.id)] = {"step": "deposit_tx", "amount": amount}
            lines = [
                f"المبلغ: {amount} USD",
                f"سعر الدولار: {rate}",
                f"المطلوب تقريباً: {syp} ل.س" if syp else "سعر الدولار غير جاهز بعد",
                "حول عبر ShamCash ثم أرسل رقم العملية.",
            ]
            if secrets:
                lines.append("الأسرار:\n" + "\n".join(secrets))
            else:
                lines.append("لا توجد أسرار ShamCash لهذا البوت بعد. المالك يضيفها من الموقع.")
            await message.answer("\n".join(lines))
            return
        if step == "deposit_tx":
            info.setdefault("bot_deposits", [])
            info["bot_deposits"].insert(0, {
                "id": uuid.uuid4().hex[:8],
                "player": player["username"],
                "telegram_id": player["telegram_id"],
                "amount": state.get("amount"),
                "tx_id": text,
                "status": "pending",
                "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
            })
            save_bots(_store)
            _states.pop((bot_id, message.chat.id), None)
            await message.answer("تم استلام طلب الشحن. سيتم التأكيد من لوحة المالك.", reply_markup=menu())
            return
        if step == "withdraw_amount":
            try:
                amount = float(text)
            except ValueError:
                await message.answer("اكتب مبلغاً رقمياً.")
                return
            info.setdefault("withdraws", [])
            info["withdraws"].insert(0, {
                "player": player["username"],
                "telegram_id": player["telegram_id"],
                "amount": amount,
                "status": "pending",
                "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
            })
            save_bots(_store)
            _states.pop((bot_id, message.chat.id), None)
            await message.answer("تم تسجيل طلب السحب.", reply_markup=menu())
            return
        if step == "coupon":
            code = text.upper()
            offers = load_offers()
            offer = next((o for o in offers if o.get("code") == code and o.get("active")), None)
            _states.pop((bot_id, message.chat.id), None)
            if not offer:
                await message.answer("الكوبون غير صالح.", reply_markup=menu())
                return
            if offer.get("used", 0) >= offer.get("max_uses", 0):
                await message.answer("انتهى استخدام هذا الكوبون.", reply_markup=menu())
                return
            offer["used"] = offer.get("used", 0) + 1
            save_offers(offers)
            value = offer.get("value")
            kind = "نسبة" if offer.get("kind") == "percent" else "مبلغ"
            await message.answer(f"تم تفعيل {offer.get('title')}: {value} {kind}", reply_markup=menu())
            return
        if step == "support":
            info.setdefault("support", [])
            info["support"].insert(0, {
                "player": player["username"],
                "text": text,
                "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
            })
            save_bots(_store)
            _states.pop((bot_id, message.chat.id), None)
            await message.answer("وصلت رسالتك للدعم.", reply_markup=menu())
            return

        if text == "إنشاء حساب":
            _states[(bot_id, message.chat.id)] = {"step": "account_user"}
            await message.answer("أرسل اسم المستخدم للحساب الجديد.")
            return
        if text == "حساباتي":
            if not player["accounts"]:
                await message.answer("لا توجد حسابات. اضغط إنشاء حساب.", reply_markup=menu())
                return
            lines = [f"{a['username']} / {a['password']}" for a in player["accounts"]]
            await message.answer("حساباتك:\n" + "\n".join(lines), reply_markup=menu())
            return
        if text == "شحن":
            _states[(bot_id, message.chat.id)] = {"step": "deposit_amount"}
            lo, hi = _limits(info)
            await message.answer(f"أرسل مبلغ الشحن بالدولار. الحد {lo} - {hi}.")
            return
        if text == "سحب":
            _states[(bot_id, message.chat.id)] = {"step": "withdraw_amount"}
            await message.answer("أرسل مبلغ السحب بالدولار.")
            return
        if text == "كوبون":
            _states[(bot_id, message.chat.id)] = {"step": "coupon"}
            await message.answer("أرسل كود الكوبون.")
            return
        if text == "الأرصدة":
            await message.answer(
                f"الرصيد المحلي: {player.get('balance', 0)} USD\nسعر الدولار: {info.get('syp_rate', '—')}",
                reply_markup=menu(),
            )
            return
        if text == "الدعم":
            _states[(bot_id, message.chat.id)] = {"step": "support"}
            await message.answer("اكتب رسالة الدعم.")
            return
        await message.answer("اختر من القائمة.", reply_markup=menu())

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

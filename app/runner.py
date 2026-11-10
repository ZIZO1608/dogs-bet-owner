import asyncio
import json
import logging
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import (
    BufferedInputFile,
    FSInputFile,
    InputProfilePhotoStatic,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

log = logging.getLogger("dogs.runner")
DATA = Path("/app/data")
DATA.mkdir(parents=True, exist_ok=True)
STORE = DATA / "bots.json"
LOGOS = DATA / "logos"
LOGOS.mkdir(parents=True, exist_ok=True)

_tasks: dict[str, asyncio.Task] = {}
_bots: dict[str, Bot] = {}


def load_bots() -> dict:
    if not STORE.exists():
        return {}
    try:
        return json.loads(STORE.read_text())
    except json.JSONDecodeError:
        return {}


def save_bots(bots: dict) -> None:
    STORE.write_text(json.dumps(bots, ensure_ascii=False, indent=2))


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


async def _apply_brand(bot: Bot, info: dict) -> str | None:
    try:
        await bot.set_my_name(info["name"])
        await bot.set_my_description(f"{info['name']} — Dogs Bet")
        await bot.set_my_short_description("Dogs Bet")
    except Exception as exc:
        log.warning("brand text failed: %s", exc)
    logo = info.get("logo")
    if not logo or not Path(logo).exists():
        return None
    try:
        data = Path(logo).read_bytes()
        await bot.set_my_profile_photo(
            photo=InputProfilePhotoStatic(photo=BufferedInputFile(data, filename="logo.jpg"))
        )
        return None
    except Exception as exc:
        log.warning("profile photo failed: %s", exc)
        return str(exc)


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
        text = f"مرحباً بك في {info['name']}\nDogs Bet"
        logo = info.get("logo")
        if logo and Path(logo).exists():
            await message.answer_photo(FSInputFile(logo), caption=text, reply_markup=menu())
        else:
            await message.answer(text, reply_markup=menu())

    @dp.message()
    async def on_any(message: Message):
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

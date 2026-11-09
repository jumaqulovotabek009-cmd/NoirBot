"""
NoirKino Bot — Telegram orqali kino qidirish va yuborish boti.

Ishga tushirish:
  1. TELEGRAM_BOT_TOKEN ni Replit Secrets ichida belgilang.
  2. ADMIN_ID ni Replit Environment Variables ichida belgilang.
  3. python bot.py

Admin kino qo‘shganda Telegram yuborgan file_id SQLite bazasiga yoziladi.
"""

import asyncio
import html
import logging
import os
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)


# ── Sozlamalar ────────────────────────────────────────────────────────────────
# Bot tokenini hech qachon kodga yozmang; Replit Secret: TELEGRAM_BOT_TOKEN.
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
# Admin Telegram ID raqami: Replit Environment Variables ichidagi ADMIN_ID.
try:
    ADMIN_ID = int(os.getenv("ADMIN_ID", "0").strip() or "0")
except ValueError:
    ADMIN_ID = 0

DATABASE_PATH = Path(os.getenv("DATABASE_PATH", "noirkino.sqlite3"))
DAILY_BONUS = 2
REFERRAL_BONUS = 10
NEW_USER_REFERRAL_BONUS = 5
SUBSCRIPTION_CACHE_SECONDS = 15
STATUS_RANK = {"USER": 0, "VIP": 1, "PREMIUM": 2, "ADMIN": 3}
STATUS_LABEL = {
    "USER": "Oddiy foydalanuvchi",
    "VIP": "VIP",
    "PREMIUM": "PREMIUM",
    "ADMIN": "Admin",
}
STATUS_ALIASES = {
    "ODDIY": "USER",
    "ODDIY FOYDALANUVCHI": "USER",
    "USER": "USER",
    "VIP": "VIP",
    "PREMIUM": "PREMIUM",
}
LOCAL_TZ = ZoneInfo("Asia/Tashkent")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("noirkino")


# ── SQLite ───────────────────────────────────────────────────────────────────
DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
db = sqlite3.connect(DATABASE_PATH, timeout=30)
db.row_factory = sqlite3.Row
db.execute("PRAGMA journal_mode=WAL")
db.execute("PRAGMA foreign_keys=ON")
db.executescript(
    """
    CREATE TABLE IF NOT EXISTS users (
        user_id         INTEGER PRIMARY KEY,
        username        TEXT NOT NULL DEFAULT '',
        full_name       TEXT NOT NULL DEFAULT '',
        status          TEXT NOT NULL DEFAULT 'USER',
        bonus_points    INTEGER NOT NULL DEFAULT 0,
        referral_count  INTEGER NOT NULL DEFAULT 0,
        referred_by     INTEGER,
        joined_at       TEXT NOT NULL,
        daily_claimed   TEXT NOT NULL DEFAULT '',
        last_request_at REAL NOT NULL DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS movies (
        code          TEXT PRIMARY KEY,
        title         TEXT NOT NULL,
        description   TEXT NOT NULL DEFAULT '',
        category      TEXT NOT NULL DEFAULT 'Boshqa',
        required_status TEXT NOT NULL DEFAULT 'USER',
        file_type     TEXT NOT NULL,
        file_id       TEXT NOT NULL,
        views         INTEGER NOT NULL DEFAULT 0,
        created_at    TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS channels (
        channel_id  TEXT PRIMARY KEY,
        title       TEXT NOT NULL,
        join_url    TEXT NOT NULL,
        added_at    TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS ratings (
        user_id    INTEGER NOT NULL,
        movie_code TEXT NOT NULL,
        stars      INTEGER NOT NULL CHECK(stars BETWEEN 1 AND 5),
        rated_at   TEXT NOT NULL,
        PRIMARY KEY (user_id, movie_code),
        FOREIGN KEY (movie_code) REFERENCES movies(code) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS settings (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL DEFAULT ''
    );
    """
)
db.commit()


def db_execute(sql: str, values: tuple = ()) -> sqlite3.Cursor:
    """So‘rovni bajarib, SQLite o‘zgarishlarini saqlaydi."""
    cursor = db.execute(sql, values)
    db.commit()
    return cursor


def db_one(sql: str, values: tuple = ()):
    """Bitta SQLite qatorini qaytaradi."""
    return db.execute(sql, values).fetchone()


def db_all(sql: str, values: tuple = ()):
    """SQLite qatorlari ro‘yxatini qaytaradi."""
    return db.execute(sql, values).fetchall()


def now_iso() -> str:
    """Vaqtni Toshkent vaqti bilan saqlaydi."""
    return datetime.now(LOCAL_TZ).isoformat(timespec="seconds")


def local_date() -> str:
    """Bonus sanasini Toshkent vaqti bo‘yicha hisoblaydi."""
    return datetime.now(LOCAL_TZ).date().isoformat()


def is_admin(user_id: int) -> bool:
    """Bot egasining ID raqamini tekshiradi."""
    return ADMIN_ID > 0 and user_id == ADMIN_ID


def user_status(user_id: int) -> str:
    """Foydalanuvchi darajasini qaytaradi; admin ID har doim ADMIN."""
    if is_admin(user_id):
        return "ADMIN"
    row = db_one("SELECT status FROM users WHERE user_id = ?", (user_id,))
    if row and row["status"] in STATUS_RANK:
        return row["status"]
    return "USER"


def ensure_user(user, referrer_id: int | None = None) -> bool:
    """Foydalanuvchini ro‘yxatdan o‘tkazadi; yangi bo‘lsa True qaytaradi."""
    user_id = user.id
    username = user.username or ""
    full_name = " ".join(part for part in [user.first_name, user.last_name] if part)
    existing = db_one("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
    if existing:
        db_execute(
            "UPDATE users SET username = ?, full_name = ? WHERE user_id = ?",
            (username, full_name, user_id),
        )
        return False

    valid_referrer = (
        referrer_id
        if referrer_id and referrer_id != user_id and db_one(
            "SELECT user_id FROM users WHERE user_id = ?", (referrer_id,)
        )
        else None
    )
    db_execute(
        """
        INSERT INTO users (user_id, username, full_name, referred_by, joined_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (user_id, username, full_name, valid_referrer, now_iso()),
    )
    if valid_referrer:
        db_execute(
            """
            UPDATE users
            SET referral_count = referral_count + 1,
                bonus_points = bonus_points + ?
            WHERE user_id = ?
            """,
            (REFERRAL_BONUS, valid_referrer),
        )
        db_execute(
            "UPDATE users SET bonus_points = bonus_points + ? WHERE user_id = ?",
            (NEW_USER_REFERRAL_BONUS, user_id),
        )
    logger.info("Yangi foydalanuvchi ro‘yxatdan o‘tdi: %s", user_id)
    return True


def ensure_user_id(user_id: int) -> None:
    """Admin holat bermoqchi bo‘lgan, hali botga kirmagan IDni tayyorlaydi."""
    db_execute(
        """
        INSERT OR IGNORE INTO users (user_id, joined_at)
        VALUES (?, ?)
        """,
        (user_id, now_iso()),
    )


def setting_get(key: str) -> str:
    """Sozlama qiymatini qaytaradi."""
    row = db_one("SELECT value FROM settings WHERE key = ?", (key,))
    return row["value"] if row else ""


def setting_set(key: str, value: str) -> None:
    """Sozlamani yaratadi yoki yangilaydi."""
    db_execute(
        """
        INSERT INTO settings (key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )


# ── Klaviaturalar ────────────────────────────────────────────────────────────
def reply_menu(user_id: int) -> ReplyKeyboardMarkup:
    """Asosiy doimiy klaviaturani yaratadi."""
    rows = [
        [KeyboardButton(text="🔎 Kino qidirish"), KeyboardButton(text="🗂 Kategoriyalar")],
        [KeyboardButton(text="🔥 Mashhur"), KeyboardButton(text="🆕 Yangi kinolar")],
        [KeyboardButton(text="👤 Profil"), KeyboardButton(text="⭐ Holatim")],
        [KeyboardButton(text="🎁 Kunlik bonus"), KeyboardButton(text="🔗 Referal")],
    ]
    if is_admin(user_id):
        rows.append([KeyboardButton(text="👑 Admin paneli")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def main_inline_menu() -> InlineKeyboardMarkup:
    """Start xabari uchun inline menyuni yaratadi."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔎 Kino qidirish", callback_data="menu:search"),
                InlineKeyboardButton(text="🗂 Kategoriyalar", callback_data="menu:categories"),
            ],
            [
                InlineKeyboardButton(text="🔥 Mashhur", callback_data="menu:popular"),
                InlineKeyboardButton(text="🆕 Yangi kinolar", callback_data="menu:latest"),
            ],
            [
                InlineKeyboardButton(text="👤 Profil", callback_data="menu:profile"),
                InlineKeyboardButton(text="⭐ Holatim", callback_data="menu:status"),
            ],
        ]
    )


def subscription_keyboard(channels: list[sqlite3.Row]) -> InlineKeyboardMarkup:
    """Majburiy obuna kanallari va tekshirish tugmasini chiqaradi."""
    rows = []
    for index, channel in enumerate(channels, start=1):
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"📢 {index}-kanalga obuna bo‘lish",
                    url=channel["join_url"],
                )
            ]
        )
    rows.append(
        [InlineKeyboardButton(text="✅ Tekshirish", callback_data="sub:check")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def rating_keyboard(code: str) -> InlineKeyboardMarkup:
    """Kino uchun 1–5 yulduzli baholash tugmalarini yaratadi."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=f"{stars}⭐", callback_data=f"rate:{code}:{stars}")
                for stars in range(1, 6)
            ]
        ]
    )


def admin_keyboard() -> InlineKeyboardMarkup:
    """Admin panel tugmalarini yaratadi."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🎞 Kino qo‘shish", callback_data="admin:add"),
                InlineKeyboardButton(text="🗑 Kino o‘chirish", callback_data="admin:delete"),
            ],
            [
                InlineKeyboardButton(text="📊 Statistika", callback_data="admin:stats"),
                InlineKeyboardButton(text="👥 Status berish", callback_data="admin:status"),
            ],
            [
                InlineKeyboardButton(text="🎁 Bonus berish", callback_data="admin:bonus"),
                InlineKeyboardButton(text="📢 Kanallar", callback_data="admin:channels"),
            ],
            [
                InlineKeyboardButton(text="📣 Xabar yuborish", callback_data="admin:broadcast"),
                InlineKeyboardButton(text="🪧 Reklama matni", callback_data="admin:promo"),
            ],
        ]
    )


# ── Obunani tekshirish ───────────────────────────────────────────────────────
subscription_cache: dict[int, tuple[float, list[sqlite3.Row]]] = {}


async def missing_channels(
    bot: Bot, user_id: int, force: bool = False
) -> list[sqlite3.Row]:
    """Foydalanuvchi a’zo bo‘lmagan kanallarni qaytaradi."""
    if is_admin(user_id):
        return []
    cached = subscription_cache.get(user_id)
    if not force and cached and time.monotonic() - cached[0] < SUBSCRIPTION_CACHE_SECONDS:
        return cached[1]

    channels = db_all("SELECT * FROM channels ORDER BY added_at")
    missing: list[sqlite3.Row] = []
    for channel in channels:
        try:
            member = await bot.get_chat_member(
                chat_id=channel["channel_id"], user_id=user_id
            )
            is_member = member.status in {"creator", "administrator", "member"}
            if member.status == "restricted":
                is_member = bool(getattr(member, "is_member", False))
            if not is_member:
                missing.append(channel)
        except (TelegramBadRequest, TelegramForbiddenError) as error:
            # Yopiq kanalda bot admin bo‘lmasa, Telegram obunani tekshira olmaydi.
            logger.warning(
                "Kanal obunasi tekshmadi (kanal=%s): %s",
                channel["channel_id"],
                error,
            )
            missing.append(channel)
        except Exception:
            logger.exception("Obuna tekshiruvda noma’lum xatolik yuz berdi.")
            missing.append(channel)

    subscription_cache[user_id] = (time.monotonic(), missing)
    return missing


async def show_subscription_prompt(message: Message, channels: list[sqlite3.Row]) -> None:
    """Obuna bo‘lmagan foydalanuvchiga kanal havolalarini yuboradi."""
    await message.answer(
        "🔒 <b>Botdan foydalanish uchun kanallarimizga obuna bo‘ling.</b>\n"
        "Obuna bo‘lgach, <b>✅ Tekshirish</b> tugmasini bosing.",
        reply_markup=subscription_keyboard(channels),
    )


class SubscriptionGuard(BaseMiddleware):
    """Obuna shartini asosiy buyruq va menyulardan oldin tekshiradi."""

    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        if user is None:
            return await handler(event, data)

        event_text = getattr(event, "text", "") or ""
        if event_text.startswith("/start"):
            return await handler(event, data)
        ensure_user(user)

        if is_admin(user.id):
            return await handler(event, data)
        if event_text.startswith("/start"):
            return await handler(event, data)
        if isinstance(event, CallbackQuery) and event.data == "sub:check":
            return await handler(event, data)

        missing = await missing_channels(data["bot"], user.id)
        if missing:
            if isinstance(event, Message):
                await show_subscription_prompt(event, missing)
            elif isinstance(event, CallbackQuery):
                await event.answer("Avval kanallarga obuna bo‘ling.", show_alert=True)
                if event.message:
                    await show_subscription_prompt(event.message, missing)
            return None
        return await handler(event, data)


# ── Yordamchi funksiyalar ────────────────────────────────────────────────────
def status_text(user_id: int) -> str:
    """Foydalanuvchi darajasini o‘zbekcha ko‘rinishda qaytaradi."""
    status = user_status(user_id)
    return STATUS_LABEL.get(status, STATUS_LABEL["USER"])


def format_rating(code: str) -> str:
    """Kino reytingi va ovozlar sonini chiqaradi."""
    row = db_one(
        "SELECT AVG(stars) AS average, COUNT(*) AS amount FROM ratings WHERE movie_code = ?",
        (code,),
    )
    if not row or not row["amount"]:
        return "Hali baholanmagan"
    return f"{row['average']:.1f}/5 ({row['amount']} ta baho)"


def movie_access_allowed(user_id: int, required_status: str) -> bool:
    """Kino uchun zarur darajani tekshiradi."""
    if is_admin(user_id):
        return True
    return STATUS_RANK.get(user_status(user_id), 0) >= STATUS_RANK.get(
        required_status, 0
    )


def parse_movie_metadata(text: str):
    """Admin yuborgan kino qatorini tekshirib, maydonlarga ajratadi."""
    parts = [part.strip() for part in text.split("|")]
    if not 3 <= len(parts) <= 5:
        raise ValueError(
            "Format noto‘g‘ri. Namuna:\n"
            "<code>101 | Kino nomi | Qisqa tavsif | Drama | VIP</code>"
        )
    code, title, description = parts[:3]
    category = parts[3] if len(parts) >= 4 and parts[3] else "Boshqa"
    raw_status = parts[4].upper() if len(parts) == 5 else "USER"
    required_status = STATUS_ALIASES.get(raw_status)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", code):
        raise ValueError("Kino kodi 1–32 ta lotin harfi, raqam, _ yoki - bo‘lishi kerak.")
    if not title or len(title) > 100:
        raise ValueError("Kino nomi 1–100 belgi bo‘lishi kerak.")
    if len(description) > 1000:
        raise ValueError("Tavsif 1000 belgidan oshmasin.")
    if len(category) > 40:
        raise ValueError("Kategoriya nomi 40 belgidan oshmasin.")
    if required_status is None:
        raise ValueError("Daraja: oddiy, VIP yoki PREMIUM bo‘lishi kerak.")
    return {
        "code": code,
        "title": title,
        "description": description,
        "category": category,
        "required_status": required_status,
    }


async def send_movie(bot: Bot, chat_id: int, user_id: int, code: str) -> None:
    """Kino ma’lumotini, reklama shart bo‘lsa reklamani va faylni yuboradi."""
    user_level = user_status(user_id)
    cooldown = {"USER": 4.0, "VIP": 1.5, "PREMIUM": 0.25, "ADMIN": 0.0}.get(
        user_level, 4.0
    )
    user_row = db_one(
        "SELECT last_request_at FROM users WHERE user_id = ?", (user_id,)
    )
    current_time = time.time()
    if user_row and cooldown and user_row["last_request_at"]:
        remaining = cooldown - (current_time - user_row["last_request_at"])
        if remaining > 0:
            await bot.send_message(
                chat_id,
                f"⏳ Iltimos, {remaining:.1f} soniya kutib, qayta urinib ko‘ring.",
            )
            return
    db_execute(
        "UPDATE users SET last_request_at = ? WHERE user_id = ?",
        (current_time, user_id),
    )

    movie = db_one("SELECT * FROM movies WHERE code = ?", (code,))
    if not movie:
        await bot.send_message(
            chat_id,
            f"😕 <b>{html.escape(code)}</b> kodi bo‘yicha kino topilmadi.\n"
            "Kod yoki nomni tekshirib, qayta urinib ko‘ring.",
        )
        return

    required_status = movie["required_status"]
    if not movie_access_allowed(user_id, required_status):
        label = STATUS_LABEL.get(required_status, required_status)
        await bot.send_message(
            chat_id,
            f"🔐 Bu kino <b>{label}</b> foydalanuvchilar uchun.\n"
            "Sizning holatingizni <b>⭐ Holatim</b> tugmasi orqali ko‘rishingiz mumkin.",
        )
        return

    if user_level == "USER":
        promo = setting_get("promo_text")
        if promo:
            await bot.send_message(chat_id, f"📣 {html.escape(promo)}")

    title = html.escape(movie["title"])
    description = html.escape(movie["description"] or "Tavsif berilmagan.")
    category = html.escape(movie["category"])
    required_label = STATUS_LABEL.get(required_status, STATUS_LABEL["USER"])
    await bot.send_message(
        chat_id,
        f"🎬 <b>{title}</b>\n\n"
        f"📝 {description}\n\n"
        f"🗂 Kategoriya: {category}\n"
        f"🔐 Kirish: {required_label}\n"
        f"⭐ Reyting: {format_rating(code)}\n"
        f"🔢 Kod: <code>{html.escape(code)}</code>",
    )
    try:
        if movie["file_type"] == "video":
            await bot.send_video(
                chat_id,
                movie["file_id"],
                caption=f"🎞 <b>{title}</b> — <code>{html.escape(code)}</code>",
                reply_markup=rating_keyboard(code),
            )
        else:
            await bot.send_document(
                chat_id,
                movie["file_id"],
                caption=f"🎞 <b>{title}</b> — <code>{html.escape(code)}</code>",
                reply_markup=rating_keyboard(code),
            )
    except Exception:
        logger.exception("Kino yuborilmadi: %s", code)
        await bot.send_message(
            chat_id,
            "⚠️ Kino faylini yuborishda xatolik yuz berdi. Admin bilan bog‘laning.",
        )
    else:
        db_execute("UPDATE movies SET views = views + 1 WHERE code = ?", (code,))


async def send_movie_list(
    bot: Bot, chat_id: int, movies: list[sqlite3.Row], empty_text: str
) -> None:
    """Kino natijalarini inline tugmalar ko‘rinishida yuboradi."""
    if not movies:
        await bot.send_message(chat_id, empty_text)
        return
    buttons = [
        [
            InlineKeyboardButton(
                text=f"🎬 {movie['title'][:22]} · {movie['code']}",
                callback_data=f"movie:{movie['code']}",
            )
        ]
        for movie in movies
    ]
    await bot.send_message(
        chat_id,
        "Kerakli kinoni tanlang:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


def category_key(category: str) -> str:
    """Kategoriya uchun ixcham, barqaror callback identifikatori."""
    import hashlib

    return hashlib.sha1(category.encode("utf-8"), usedforsecurity=False).hexdigest()[:12]


# ── Bot va handlerlar ─────────────────────────────────────────────────────────
router = Router(name="noirkino")
admin_states: dict[int, dict] = {}
pending_broadcasts: dict[int, tuple[int, int]] = {}
bot_username = ""
router.message.outer_middleware(SubscriptionGuard())
router.callback_query.outer_middleware(SubscriptionGuard())


async def start_handler(message: Message, command: CommandObject, bot: Bot) -> None:
    """/start: ro‘yxatdan o‘tkazish, obuna va asosiy menyu."""
    referrer_id = None
    if command.args and command.args.startswith("ref_"):
        try:
            referrer_id = int(command.args[4:])
        except ValueError:
            referrer_id = None
    is_new = ensure_user(message.from_user, referrer_id)
    missing = await missing_channels(bot, message.from_user.id, force=True)
    welcome = (
        "🎬 <b>NoirKino Bot</b>ga xush kelibsiz!\n\n"
        "Kino kodini yuboring yoki menyudan qidiruvni tanlang. "
        "Kino nomi, tavsifi va fayli shu yerda ko‘rsatiladi."
    )
    referred_row = db_one(
        "SELECT referred_by FROM users WHERE user_id = ?", (message.from_user.id,)
    )
    if is_new and referrer_id and referred_row and referred_row["referred_by"]:
        welcome += f"\n\n🎁 Referal havola orqali kirganingiz uchun {NEW_USER_REFERRAL_BONUS} bonus ball berildi."
    await message.answer(welcome, reply_markup=reply_menu(message.from_user.id))
    if missing:
        await show_subscription_prompt(message, missing)
    else:
        await message.answer("👇 Kerakli bo‘limni tanlang:", reply_markup=main_inline_menu())


@router.message(Command("help"))
async def help_handler(message: Message) -> None:
    """Foydalanish yo‘riqnomasini yuboradi."""
    await message.answer(
        "ℹ️ <b>Foydalanish</b>\n\n"
        "• Kino kodini yuboring, masalan: <code>101</code>\n"
        "• Kino nomi bilan qidirish uchun <b>🔎 Kino qidirish</b> tugmasini bosing\n"
        "• Kategoriyalar, mashhur va yangi kinolar ro‘yxatini oching\n"
        "• Kinoni ⭐ 1 dan 5 gacha baholashingiz mumkin\n\n"
        "Kanal obunasi talab qilinsa, avval obuna bo‘lib, tekshirish tugmasini bosing."
    )


@router.message(Command("cancel"))
async def cancel_handler(message: Message) -> None:
    """Adminning kutilayotgan amalini bekor qiladi."""
    admin_states.pop(message.from_user.id, None)
    pending_broadcasts.pop(message.from_user.id, None)
    await message.answer("Bekor qilindi.", reply_markup=reply_menu(message.from_user.id))


@router.message(Command("admin"))
async def admin_command(message: Message) -> None:
    """Admin panelini faqat admin ID egasiga ochadi."""
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Bu bo‘lim faqat admin uchun.")
        return
    await message.answer("👑 <b>Admin paneli</b>", reply_markup=admin_keyboard())


@router.message(Command("status"))
async def status_command(message: Message) -> None:
    """Foydalanuvchining statusini ko‘rsatadi."""
    await send_user_status(message, message.from_user.id)


async def send_user_status(message: Message, user_id: int) -> None:
    """Berilgan foydalanuvchining statusi va bonuslarini ko‘rsatadi."""
    status = user_status(user_id)
    row = db_one(
        "SELECT bonus_points, referral_count FROM users WHERE user_id = ?",
        (user_id,),
    )
    points = row["bonus_points"] if row else 0
    referrals = row["referral_count"] if row else 0
    await message.answer(
        f"⭐ <b>Holatingiz:</b> {STATUS_LABEL.get(status, status)}\n"
        f"🎁 <b>Bonus ball:</b> {points}\n"
        f"🔗 <b>Taklif qilganlar:</b> {referrals}"
    )


@router.message(CommandStart())
async def start_route(message: Message, command: CommandObject, bot: Bot) -> None:
    """/start buyrug‘ini start handleriga uzatadi."""
    await start_handler(message, command, bot)


@router.callback_query(F.data == "sub:check")
async def subscription_check_callback(callback: CallbackQuery, bot: Bot) -> None:
    """Obuna tekshirish tugmasi."""
    subscription_cache.pop(callback.from_user.id, None)
    missing = await missing_channels(bot, callback.from_user.id, force=True)
    if missing:
        await callback.answer("Hali barcha kanallarga obuna bo‘linmagan.", show_alert=True)
        if callback.message:
            await show_subscription_prompt(callback.message, missing)
        return
    await callback.answer("Obuna tasdiqlandi!")
    if callback.message:
        await callback.message.answer(
            "✅ Obuna tasdiqlandi. Botdan foydalanishingiz mumkin.",
            reply_markup=reply_menu(callback.from_user.id),
        )
        await callback.message.answer(
            "👇 Kerakli bo‘limni tanlang:", reply_markup=main_inline_menu()
        )


@router.callback_query(F.data.startswith("menu:"))
async def menu_callback(callback: CallbackQuery, bot: Bot) -> None:
    """Inline menyudagi bo‘limlarni ochadi."""
    if not callback.message:
        await callback.answer()
        return
    action = callback.data.split(":", 1)[1]
    await callback.answer()
    if action == "search":
        await callback.message.answer("🔎 Kino kodini yoki nomini yuboring.")
    elif action == "categories":
        await show_categories(bot, callback.message.chat.id)
    elif action == "popular":
        movies = db_all("SELECT * FROM movies ORDER BY views DESC, created_at DESC LIMIT 10")
        await send_movie_list(bot, callback.message.chat.id, movies, "Hali kino qo‘shilmagan.")
    elif action == "latest":
        movies = db_all("SELECT * FROM movies ORDER BY created_at DESC LIMIT 10")
        await send_movie_list(bot, callback.message.chat.id, movies, "Hali kino qo‘shilmagan.")
    elif action == "profile":
        await show_profile(callback.message, bot, callback.from_user.id)
    elif action == "status":
        await send_user_status(callback.message, callback.from_user.id)


async def show_categories(bot: Bot, chat_id: int) -> None:
    """Mavjud kino kategoriyalarini inline ro‘yxat qilib yuboradi."""
    categories = db_all(
        "SELECT DISTINCT category FROM movies WHERE category != '' ORDER BY category COLLATE NOCASE"
    )
    if not categories:
        await bot.send_message(chat_id, "🗂 Hozircha kategoriya yo‘q.")
        return
    rows = [
        [
            InlineKeyboardButton(
                text=f"🗂 {row['category']}",
                callback_data=f"cat:{category_key(row['category'])}",
            )
        ]
        for row in categories[:40]
    ]
    await bot.send_message(
        chat_id, "🗂 <b>Kategoriyani tanlang:</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
    )


@router.callback_query(F.data.startswith("cat:"))
async def category_callback(callback: CallbackQuery, bot: Bot) -> None:
    """Tanlangan kategoriya bo‘yicha filmlarni ko‘rsatadi."""
    key = callback.data.split(":", 1)[1]
    categories = db_all(
        "SELECT DISTINCT category FROM movies WHERE category != '' ORDER BY category COLLATE NOCASE"
    )
    category = next(
        (row["category"] for row in categories if category_key(row["category"]) == key),
        None,
    )
    await callback.answer()
    if not category or not callback.message:
        return
    movies = db_all(
        "SELECT * FROM movies WHERE category = ? ORDER BY created_at DESC LIMIT 20",
        (category,),
    )
    await send_movie_list(
        bot,
        callback.message.chat.id,
        movies,
        f"“{html.escape(category)}” kategoriyasida kino topilmadi.",
    )


@router.callback_query(F.data.startswith("movie:"))
async def movie_callback(callback: CallbackQuery, bot: Bot) -> None:
    """Ro‘yxatdan tanlangan kinoni yuboradi."""
    code = callback.data.split(":", 1)[1]
    await callback.answer()
    chat_id = callback.message.chat.id if callback.message else callback.from_user.id
    await send_movie(bot, chat_id, callback.from_user.id, code)


@router.callback_query(F.data.startswith("rate:"))
async def rating_callback(callback: CallbackQuery) -> None:
    """Kino bahosini saqlaydi yoki avvalgi bahoni yangilaydi."""
    try:
        _, code, raw_stars = callback.data.split(":")
        stars = int(raw_stars)
    except (ValueError, AttributeError):
        await callback.answer("Bahoni saqlab bo‘lmadi.", show_alert=True)
        return
    if not 1 <= stars <= 5 or not db_one(
        "SELECT code FROM movies WHERE code = ?", (code,)
    ):
        await callback.answer("Kino topilmadi.", show_alert=True)
        return
    db_execute(
        """
        INSERT INTO ratings (user_id, movie_code, stars, rated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id, movie_code) DO UPDATE
        SET stars = excluded.stars, rated_at = excluded.rated_at
        """,
        (callback.from_user.id, code, stars, now_iso()),
    )
    await callback.answer(f"Rahmat! Bahoyingiz: {stars}⭐")
    if callback.message:
        try:
            await callback.message.edit_reply_markup(reply_markup=rating_keyboard(code))
        except TelegramBadRequest:
            pass


@router.callback_query(F.data.startswith("admin:"))
async def admin_callback(callback: CallbackQuery, bot: Bot) -> None:
    """Admin panelidagi amallarni bajaradi."""
    if not is_admin(callback.from_user.id):
        await callback.answer("Bu amal faqat admin uchun.", show_alert=True)
        return
    action = callback.data.split(":")[1]
    if action == "add":
        admin_states[callback.from_user.id] = {"step": "movie_metadata"}
        text = (
            "🎞 <b>Kino ma’lumotini yuboring:</b>\n"
            "<code>kod | nomi | tavsif | kategoriya | oddiy/VIP/PREMIUM</code>\n\n"
            "Namuna: <code>101 | Kino nomi | Qisqa tavsif | Drama | VIP</code>\n"
            "Daraja ixtiyoriy. Bekor qilish: /cancel"
        )
        await callback.answer()
        if callback.message:
            await callback.message.answer(text)
    elif action == "delete":
        admin_states[callback.from_user.id] = {"step": "delete_code"}
        await callback.answer()
        if callback.message:
            await callback.message.answer("🗑 O‘chiriladigan kino kodini yuboring. Bekor qilish: /cancel")
    elif action == "stats":
        await callback.answer()
        if callback.message:
            await send_statistics(callback.message)
    elif action == "status":
        admin_states[callback.from_user.id] = {"step": "set_status"}
        await callback.answer()
        if callback.message:
            await callback.message.answer(
                "👥 Foydalanuvchi ID va darajasini yuboring:\n"
                "<code>123456789 | VIP</code>\n"
                "Darajalar: ODDIY, VIP, PREMIUM. Bekor qilish: /cancel"
            )
    elif action == "bonus":
        admin_states[callback.from_user.id] = {"step": "give_bonus"}
        await callback.answer()
        if callback.message:
            await callback.message.answer(
                "🎁 Foydalanuvchi ID va bonus miqdorini yuboring:\n"
                "<code>123456789 | 20</code>\nBekor qilish: /cancel"
            )
    elif action == "channels":
        await callback.answer()
        if callback.message:
            await show_channel_settings(callback.message)
    elif action == "channel_add":
        admin_states[callback.from_user.id] = {"step": "add_channel"}
        await callback.answer()
        if callback.message:
            await callback.message.answer(
                "📢 Kanal ma’lumotini yuboring:\n"
                "<code>@kanal | https://t.me/kanal</code>\n"
                "Yopiq kanal uchun: <code>-1001234567890 | https://t.me/+taklif</code>\n"
                "Bot kanalda admin bo‘lishi kerak. Bekor qilish: /cancel"
            )
    elif action.startswith("channel_del_"):
        channel_id = action[len("channel_del_") :]
        db_execute("DELETE FROM channels WHERE channel_id = ?", (channel_id,))
        subscription_cache.clear()
        await callback.answer("Kanal ro‘yxatdan o‘chirildi.")
        if callback.message:
            await show_channel_settings(callback.message)
    elif action == "promo":
        admin_states[callback.from_user.id] = {"step": "set_promo"}
        await callback.answer()
        if callback.message:
            await callback.message.answer(
                "🪧 Oddiy foydalanuvchilarga ko‘rsatiladigan reklama matnini yuboring.\n"
                "Reklamani o‘chirish uchun <code>o‘chirish</code> deb yozing. Bekor qilish: /cancel"
            )
    elif action == "broadcast":
        admin_states[callback.from_user.id] = {"step": "broadcast_content"}
        await callback.answer()
        if callback.message:
            await callback.message.answer(
                "📣 Barcha foydalanuvchilarga yuboriladigan matn yoki media xabarni yuboring.\n"
                "Yuborishdan oldin tasdiqlash so‘raladi. Bekor qilish: /cancel"
            )
    elif action == "broadcast_send":
        await callback.answer("Xabar yuborilmoqda…")
        await run_broadcast(callback, bot)
    elif action == "broadcast_cancel":
        pending_broadcasts.pop(callback.from_user.id, None)
        await callback.answer("Xabar yuborish bekor qilindi.")
        if callback.message:
            await callback.message.answer("Xabar yuborilmadi.")
    elif action.startswith("delete_confirm_"):
        code = action[len("delete_confirm_") :]
        admin_states.pop(callback.from_user.id, None)
        cursor = db_execute("DELETE FROM movies WHERE code = ?", (code,))
        await callback.answer("Kino o‘chirildi." if cursor.rowcount else "Kino topilmadi.")
        if callback.message:
            await callback.message.answer(
                f"🗑 <code>{html.escape(code)}</code> kodi bo‘yicha "
                + ("kino o‘chirildi." if cursor.rowcount else "kino topilmadi.")
            )
    elif action == "delete_cancel":
        admin_states.pop(callback.from_user.id, None)
        await callback.answer("O‘chirish bekor qilindi.")
        if callback.message:
            await callback.message.answer("Kino o‘chirilmadi.")
    else:
        await callback.answer()


async def show_channel_settings(message: Message) -> None:
    """Admin uchun majburiy kanal ro‘yxatini va boshqaruv tugmalarini ko‘rsatadi."""
    channels = db_all("SELECT * FROM channels ORDER BY added_at")
    rows = []
    for channel in channels:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"❌ {channel['title'][:32]}",
                    callback_data=f"admin:channel_del_{channel['channel_id']}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="➕ Kanal qo‘shish", callback_data="admin:channel_add")])
    if channels:
        text = "📢 <b>Majburiy obuna kanallari:</b>\n" + "\n".join(
            f"• {html.escape(channel['title'])} — <code>{html.escape(channel['channel_id'])}</code>"
            for channel in channels
        )
    else:
        text = "📢 Majburiy obuna kanallari hozircha sozlanmagan."
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


async def show_profile(message: Message, bot: Bot, user_id: int | None = None) -> None:
    """Profil, bonus va referal havolasini chiqaradi."""
    user_id = user_id or message.from_user.id
    row = db_one(
        "SELECT bonus_points, referral_count FROM users WHERE user_id = ?", (user_id,)
    )
    if not row:
        ensure_user_id(user_id)
        row = db_one(
            "SELECT bonus_points, referral_count FROM users WHERE user_id = ?", (user_id,)
        )
    referral = f"https://t.me/{bot_username}?start=ref_{user_id}"
    await message.answer(
        "👤 <b>Profilingiz</b>\n\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"⭐ Status: {status_text(user_id)}\n"
        f"🎁 Bonus ball: {row['bonus_points']}\n"
        f"🔗 Referallar: {row['referral_count']}\n"
        f"🔗 Havola: {referral}"
    )


async def send_statistics(message: Message) -> None:
    """Botning asosiy statistikasini admin paneliga chiqaradi."""
    users = db_one("SELECT COUNT(*) AS amount FROM users")["amount"]
    movies = db_one("SELECT COUNT(*) AS amount FROM movies")["amount"]
    total_views = db_one("SELECT COALESCE(SUM(views), 0) AS amount FROM movies")["amount"]
    ratings = db_one("SELECT COUNT(*) AS amount FROM ratings")["amount"]
    channel_count = db_one("SELECT COUNT(*) AS amount FROM channels")["amount"]
    today = local_date()
    new_today = db_one(
        "SELECT COUNT(*) AS amount FROM users WHERE substr(joined_at, 1, 10) = ?",
        (today,),
    )["amount"]
    status_counts = {
        status: db_one(
            "SELECT COUNT(*) AS amount FROM users WHERE status = ?", (status,)
        )["amount"]
        for status in ("USER", "VIP", "PREMIUM")
    }
    await message.answer(
        "📊 <b>NoirKino statistikasi</b>\n\n"
        f"👥 Foydalanuvchilar: {users} (bugun: {new_today})\n"
        f"• Oddiy: {status_counts['USER']} | VIP: {status_counts['VIP']} "
        f"| PREMIUM: {status_counts['PREMIUM']}\n"
        f"🎞 Kinolar: {movies}\n"
        f"👁 Ko‘rish/yuborish so‘rovlari: {total_views}\n"
        f"⭐ Berilgan baholar: {ratings}\n"
        f"📢 Majburiy kanallar: {channel_count}"
    )


async def prepare_broadcast(message: Message) -> None:
    """Adminning xabarini barcha foydalanuvchilarga yuborishdan oldin tasdiqlatadi."""
    user_id = message.from_user.id
    pending_broadcasts[user_id] = (message.chat.id, message.message_id)
    admin_states.pop(user_id, None)
    users = db_one("SELECT COUNT(*) AS amount FROM users")["amount"]
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"📣 {users} foydalanuvchiga yuborish",
                    callback_data="admin:broadcast_send",
                ),
                InlineKeyboardButton(text="Bekor qilish", callback_data="admin:broadcast_cancel"),
            ]
        ]
    )
    await message.answer(
        f"Xabar {users} ta foydalanuvchiga yuboriladi. Tasdiqlaysizmi?",
        reply_markup=keyboard,
    )


async def process_admin_text(message: Message, state: dict, bot: Bot) -> bool:
    """Adminning bosqichma-bosqich matnli amallarini bajaradi."""
    user_id = message.from_user.id
    step = state.get("step")
    text = (message.text or "").strip()

    if step == "movie_metadata":
        try:
            movie = parse_movie_metadata(text)
        except ValueError as error:
            await message.answer(f"⚠️ {error}")
            return True
        existing = db_one("SELECT code FROM movies WHERE code = ?", (movie["code"],))
        if existing:
            await message.answer(
                "⚠️ Bu kod avval ishlatilgan. Avval eski kinoni o‘chiring yoki boshqa kod kiriting."
            )
            return True
        admin_states[user_id] = {"step": "movie_file", "movie": movie}
        await message.answer(
            "✅ Ma’lumot qabul qilindi. Endi kino faylini <b>video</b> yoki <b>hujjat</b> "
            "sifatida yuboring."
        )
    elif step == "delete_code":
        movie = db_one("SELECT title FROM movies WHERE code = ?", (text,))
        if not movie:
            await message.answer("😕 Bu kod bilan kino topilmadi. Kodni qayta yuboring yoki /cancel.")
            return True
        admin_states[user_id] = {"step": "delete_confirm", "code": text}
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🗑 Ha, o‘chirilsin",
                        callback_data=f"admin:delete_confirm_{text}",
                    ),
                    InlineKeyboardButton(text="Bekor qilish", callback_data="admin:delete_cancel"),
                ]
            ]
        )
        await message.answer(
            f"Quyidagi kinoni o‘chirishni tasdiqlaysizmi?\n"
            f"🎬 <b>{html.escape(movie['title'])}</b> — <code>{html.escape(text)}</code>",
            reply_markup=keyboard,
        )
    elif step == "set_status":
        parts = [part.strip() for part in text.split("|")]
        if len(parts) != 2:
            await message.answer("Format: <code>foydalanuvchi_ID | VIP</code>")
            return True
        try:
            target_id = int(parts[0])
        except ValueError:
            await message.answer("ID raqami noto‘g‘ri. Qayta yuboring.")
            return True
        target_status = STATUS_ALIASES.get(parts[1].upper())
        if not target_status:
            await message.answer("Daraja ODDIY, VIP yoki PREMIUM bo‘lishi kerak.")
            return True
        ensure_user_id(target_id)
        db_execute("UPDATE users SET status = ? WHERE user_id = ?", (target_status, target_id))
        admin_states.pop(user_id, None)
        await message.answer(
            f"✅ <code>{target_id}</code> foydalanuvchisining darajasi: "
            f"<b>{STATUS_LABEL[target_status]}</b>."
        )
    elif step == "give_bonus":
        parts = [part.strip() for part in text.split("|")]
        if len(parts) != 2:
            await message.answer("Format: <code>foydalanuvchi_ID | bonus_miqdori</code>")
            return True
        try:
            target_id, amount = int(parts[0]), int(parts[1])
        except ValueError:
            await message.answer("ID va bonus miqdori butun son bo‘lishi kerak.")
            return True
        if target_id <= 0 or not 1 <= amount <= 100_000:
            await message.answer("ID musbat, bonus esa 1–100000 oralig‘ida bo‘lishi kerak.")
            return True
        ensure_user_id(target_id)
        db_execute(
            "UPDATE users SET bonus_points = bonus_points + ? WHERE user_id = ?",
            (amount, target_id),
        )
        admin_states.pop(user_id, None)
        await message.answer(f"✅ {target_id} foydalanuvchisiga {amount} bonus ball berildi.")
    elif step == "add_channel":
        parts = [part.strip() for part in text.split("|", maxsplit=1)]
        if len(parts) != 2:
            await message.answer("Format: <code>@kanal | https://t.me/kanal</code>")
            return True
        channel_id, join_url = parts
        if not (channel_id.startswith("@") or channel_id.lstrip("-").isdigit()):
            await message.answer("Kanal IDsi @username yoki raqamli Telegram chat ID bo‘lishi kerak.")
            return True
        if not join_url.startswith("https://t.me/"):
            await message.answer("Kanal havolasi <code>https://t.me/</code> bilan boshlansin.")
            return True
        try:
            chat = await bot.get_chat(channel_id)
            bot_user = await bot.get_me()
            membership = await bot.get_chat_member(chat_id=chat.id, user_id=bot_user.id)
            if membership.status not in {"administrator", "creator"}:
                await message.answer(
                    "⚠️ Bot bu kanalda admin emas. Avval botni kanalga qo‘shib, "
                    "administrator qiling, so‘ng ma’lumotni qayta yuboring."
                )
                return True
        except Exception as error:
            logger.warning("Majburiy kanal tekshirilmadi: %s", error)
            await message.answer(
                "⚠️ Kanalni tekshirib bo‘lmadi. Kanal IDsi va havolasini tekshiring, "
                "bot kanalga qo‘shilib administrator qilinganiga ishonch hosil qiling."
            )
            return True
        canonical_channel_id = str(chat.id)
        title = chat.title or chat.username or channel_id
        try:
            db_execute(
                """
                INSERT INTO channels (channel_id, title, join_url, added_at)
                VALUES (?, ?, ?, ?)
                """,
                (canonical_channel_id, title, join_url, now_iso()),
            )
        except sqlite3.IntegrityError:
            await message.answer("Bu kanal ro‘yxatda bor.")
            return True
        subscription_cache.clear()
        admin_states.pop(user_id, None)
        await message.answer(
            f"✅ <b>{html.escape(title)}</b> majburiy obuna ro‘yxatiga qo‘shildi.\n"
            "Obunani tekshirish uchun bot kanal administratori bo‘lishi kerak."
        )
    elif step == "set_promo":
        if text.casefold() in {"o‘chirish", "ochirish", "delete", "clear"}:
            setting_set("promo_text", "")
            admin_states.pop(user_id, None)
            await message.answer("✅ Oddiy foydalanuvchilar uchun reklama o‘chirildi.")
        elif len(text) > 1000:
            await message.answer("Reklama matni 1000 belgidan oshmasin.")
        else:
            setting_set("promo_text", text)
            admin_states.pop(user_id, None)
            await message.answer("✅ Reklama matni saqlandi. Uni oddiy foydalanuvchilar ko‘radi.")
    elif step == "broadcast_content":
        await prepare_broadcast(message)
    else:
        return False
    return True


@router.message(F.video | F.document)
async def media_handler(message: Message, bot: Bot) -> None:
    """Admin yuborgan video yoki hujjatni kino sifatida saqlaydi."""
    state = admin_states.get(message.from_user.id)
    if state and state.get("step") == "broadcast_content" and is_admin(message.from_user.id):
        await prepare_broadcast(message)
        return
    if not state or state.get("step") != "movie_file":
        return
    if not is_admin(message.from_user.id):
        admin_states.pop(message.from_user.id, None)
        return
    if message.video:
        file_type = "video"
        file_id = message.video.file_id
    elif message.document:
        file_type = "document"
        file_id = message.document.file_id
    else:
        return
    movie = state["movie"]
    try:
        db_execute(
            """
            INSERT INTO movies
                (code, title, description, category, required_status, file_type, file_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                movie["code"],
                movie["title"],
                movie["description"],
                movie["category"],
                movie["required_status"],
                file_type,
                file_id,
                now_iso(),
            ),
        )
    except sqlite3.IntegrityError:
        await message.answer("⚠️ Bu kod bilan kino allaqachon mavjud. Boshqa kod tanlang.")
        admin_states.pop(message.from_user.id, None)
        return
    admin_states.pop(message.from_user.id, None)
    logger.info("Admin kino qo‘shdi: %s", movie["code"])
    await message.answer(
        f"✅ <b>{html.escape(movie['title'])}</b> kino bazasiga qo‘shildi.\n"
        f"🔢 Kod: <code>{html.escape(movie['code'])}</code>\n"
        f"🗂 Kategoriya: {html.escape(movie['category'])}\n"
        f"🔐 Kirish: {STATUS_LABEL[movie['required_status']]}"
    )


@router.message(F.text)
async def text_handler(message: Message, bot: Bot) -> None:
    """Tugmalarni, admin matnli jarayonlarini va kino qidiruvini boshqaradi."""
    text = (message.text or "").strip()
    user_id = message.from_user.id
    state = admin_states.get(user_id)
    if state:
        if is_admin(user_id) and await process_admin_text(message, state, bot):
            return
        if not is_admin(user_id):
            admin_states.pop(user_id, None)

    if text == "👑 Admin paneli":
        if is_admin(user_id):
            await message.answer("👑 <b>Admin paneli</b>", reply_markup=admin_keyboard())
        else:
            await message.answer("⛔ Bu bo‘lim faqat admin uchun.")
        return
    if text == "🔎 Kino qidirish":
        await message.answer("🔎 Kino kodini yoki nomini yuboring.")
        return
    if text == "🗂 Kategoriyalar":
        await show_categories(bot, message.chat.id)
        return
    if text == "🔥 Mashhur":
        movies = db_all("SELECT * FROM movies ORDER BY views DESC, created_at DESC LIMIT 10")
        await send_movie_list(bot, message.chat.id, movies, "Hali kino qo‘shilmagan.")
        return
    if text == "🆕 Yangi kinolar":
        movies = db_all("SELECT * FROM movies ORDER BY created_at DESC LIMIT 10")
        await send_movie_list(bot, message.chat.id, movies, "Hali kino qo‘shilmagan.")
        return
    if text == "👤 Profil":
        await show_profile(message, bot)
        return
    if text == "⭐ Holatim":
        await status_command(message)
        return
    if text == "🎁 Kunlik bonus":
        row = db_one("SELECT daily_claimed FROM users WHERE user_id = ?", (user_id,))
        today = local_date()
        if row and row["daily_claimed"] == today:
            await message.answer("🎁 Bugungi bonusingizni olib bo‘lgansiz. Ertaga yana keling.")
            return
        db_execute(
            "UPDATE users SET daily_claimed = ?, bonus_points = bonus_points + ? WHERE user_id = ?",
            (today, DAILY_BONUS, user_id),
        )
        await message.answer(f"🎉 Bugungi bonus: <b>+{DAILY_BONUS} ball</b>.")
        return
    if text == "🔗 Referal":
        await show_profile(message, bot)
        return

    if text.startswith("/"):
        await message.answer("Bu buyruq tanilmadi. Yo‘riqnoma uchun /help yuboring.")
        return
    if re.fullmatch(r"[A-Za-z0-9_-]{1,32}", text):
        await send_movie(bot, message.chat.id, user_id, text)
        return

    matches = db_all(
        """
        SELECT * FROM movies
        WHERE title LIKE ? OR description LIKE ? OR category LIKE ?
        ORDER BY views DESC, created_at DESC
        LIMIT 10
        """,
        (f"%{text}%", f"%{text}%", f"%{text}%"),
    )
    if matches:
        await send_movie_list(bot, message.chat.id, matches, "Kino topilmadi.")
    else:
        await message.answer(
            "😕 Kino topilmadi. Kodni tekshiring yoki <b>🔎 Kino qidirish</b> "
            "tugmasi orqali nom bilan qidiring."
        )


async def run_broadcast(callback: CallbackQuery, bot: Bot) -> None:
    """Admin tasdiqlagan xabarni foydalanuvchilarga nusxalaydi."""
    admin_id = callback.from_user.id
    pending = pending_broadcasts.pop(admin_id, None)
    if not pending:
        if callback.message:
            await callback.message.answer("Xabar topilmadi; uni qaytadan tanlang.")
        return
    from_chat_id, message_id = pending
    users = db_all("SELECT user_id FROM users ORDER BY user_id")
    sent = failed = 0
    for user in users:
        try:
            await bot.copy_message(
                chat_id=user["user_id"],
                from_chat_id=from_chat_id,
                message_id=message_id,
            )
            sent += 1
        except (TelegramBadRequest, TelegramForbiddenError):
            failed += 1
        except Exception:
            failed += 1
            logger.exception("Broadcast xabari yuborilmadi: %s", user["user_id"])
        await asyncio.sleep(0.05)
    if callback.message:
        await callback.message.answer(
            f"📣 Xabar yuborish tugadi.\n✅ Yetkazildi: {sent}\n⚠️ Yetkazilmadi: {failed}"
        )
    logger.info("Broadcast yakunlandi: yuborildi=%s, xato=%s", sent, failed)


@router.message()
async def unhandled_message(message: Message) -> None:
    """Matn va kino fayli bo‘lmagan xabarlarga yo‘riqnoma beradi."""
    if is_admin(message.from_user.id):
        state = admin_states.get(message.from_user.id)
        if state and state.get("step") == "broadcast_content":
            await prepare_broadcast(message)
            return
        if state and state.get("step") == "movie_file":
            await message.answer("Video yoki hujjat faylini yuboring. Bekor qilish: /cancel")
            return
    await message.answer("Kino kodi yoki nomini yuboring. Yo‘riqnoma: /help")


async def error_handler(event) -> bool:
    """Ishlov berilmagan xatolarni logga yozadi."""
    logger.error("Handler xatosi: %r", event.exception)
    update = event.update
    if update and update.message:
        try:
            await update.message.answer("⚠️ Kutilmagan xatolik yuz berdi. Qayta urinib ko‘ring.")
        except Exception:
            logger.exception("Xatolik javobini yuborib bo‘lmadi.")
    return True


async def main() -> None:
    """Botni ishga tushiradi va Telegram yangilanishlarini qabul qiladi."""
    if not BOT_TOKEN:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN topilmadi. Uni Replit Secrets ichida sozlang."
        )
    if ADMIN_ID <= 0:
        raise RuntimeError(
            "ADMIN_ID sozlanmagan. Telegram raqamli admin ID sini "
            "Environment Variables ichida belgilang."
        )

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    dispatcher.errors.register(error_handler)

    me = await bot.get_me()
    global bot_username
    bot_username = me.username or ""
    logger.info("NoirKino Bot ishga tushdi: @%s", bot_username)
    try:
        await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())
    finally:
        await bot.session.close()
        db.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot to‘xtatildi.")
    except Exception:
        logger.exception("Bot ishga tushmadi.")
        raise

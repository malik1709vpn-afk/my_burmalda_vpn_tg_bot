import os
import asyncio
import sqlite3
from datetime import datetime

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

if not TOKEN or not ADMIN_ID:
    raise RuntimeError("Set BOT_TOKEN and ADMIN_ID environment variables.")

bot = Bot(TOKEN)
dp = Dispatcher(storage=MemoryStorage())
DB = "vpn_bot.db"


def db():
    return sqlite3.connect(DB)


def init_db():
    with db() as con:
        con.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY)")
        con.execute("""CREATE TABLE IF NOT EXISTS draft_servers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL, link TEXT NOT NULL
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS releases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS release_servers (
            release_id INTEGER, name TEXT, link TEXT
        )""")


class AddServer(StatesGroup):
    waiting_for_server = State()


def main_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text="🆓 Бесплатные серверы", callback_data="servers")
    kb.button(text="📚 История обновлений", callback_data="history")
    kb.adjust(1)
    return kb.as_markup()


def admin_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Добавить сервер", callback_data="add_server")
    kb.button(text="📢 Опубликовать обновление", callback_data="publish")
    kb.button(text="📋 Черновик серверов", callback_data="draft")
    kb.button(text="👥 Количество пользователей", callback_data="users_count")
    kb.adjust(1)
    return kb.as_markup()


def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


@dp.message(CommandStart())
async def start(message: Message):
    with db() as con:
        con.execute("INSERT OR IGNORE INTO users(user_id) VALUES(?)",
                    (message.from_user.id,))
    await message.answer(
        "🌐 Добро пожаловать в VPN-сервис!\n\n"
        "Здесь будут публиковаться бесплатные серверы и обновления.",
        reply_markup=main_menu()
    )


@dp.message(Command("admin"))
async def admin(message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer("⚙️ Админ-панель", reply_markup=admin_menu())


@dp.callback_query(F.data == "add_server")
async def add_server(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AddServer.waiting_for_server)
    await callback.message.answer(
        "Отправь сервер одной строкой в формате:\n"
        "Название | VLESS-ссылка\n\n"
        "Пример:\n"
        "Germany 1 | vless://...\n\n"
        "Добавляй только серверы, которыми тебе разрешено делиться."
    )
    await callback.answer()


@dp.message(AddServer.waiting_for_server)
async def save_server(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    if "|" not in message.text:
        await message.answer("Неверный формат. Используй: Название | VLESS-ссылка")
        return
    name, link = [part.strip() for part in message.text.split("|", 1)]
    if not name or not link.startswith("vless://"):
        await message.answer("Проверь название и VLESS-ссылку. Ссылка должна начинаться с vless://")
        return
    with db() as con:
        con.execute("INSERT INTO draft_servers(name, link) VALUES(?, ?)", (name, link))
    await state.clear()
    await message.answer(f"✅ Сервер «{name}» добавлен в черновик.", reply_markup=admin_menu())


@dp.callback_query(F.data == "draft")
async def show_draft(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    with db() as con:
        rows = con.execute("SELECT name FROM draft_servers ORDER BY id").fetchall()
    if not rows:
        text = "Черновик пуст. Добавь серверы."
    else:
        text = "📋 Черновик:\n" + "\n".join(f"• {r[0]}" for r in rows)
    await callback.message.answer(text, reply_markup=admin_menu())
    await callback.answer()


@dp.callback_query(F.data == "publish")
async def publish(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    with db() as con:
        servers = con.execute("SELECT name, link FROM draft_servers ORDER BY id").fetchall()
        if not servers:
            await callback.answer("Сначала добавь серверы", show_alert=True)
            return
        cur = con.execute("INSERT INTO releases(created_at) VALUES(?)",
                          (datetime.now().strftime("%d.%m.%Y %H:%M"),))
        release_id = cur.lastrowid
        con.executemany(
            "INSERT INTO release_servers(release_id, name, link) VALUES(?, ?, ?)",
            [(release_id, name, link) for name, link in servers]
        )
        con.execute("DELETE FROM draft_servers")
        users = [r[0] for r in con.execute("SELECT user_id FROM users").fetchall()]

    sent = 0
    for user_id in users:
        try:
            await bot.send_message(
                user_id,
                "🆕 Вышло новое обновление бесплатных серверов!\n"
                "Открой бота и нажми «Бесплатные серверы».",
                reply_markup=main_menu()
            )
            sent += 1
        except Exception:
            pass
    await callback.message.answer(
        f"✅ Обновление №{release_id} опубликовано.\n"
        f"Уведомления отправлены: {sent} пользователям.",
        reply_markup=admin_menu()
    )
    await callback.answer()


async def send_release(message: Message, release_id: int):
    with db() as con:
        rows = con.execute(
            "SELECT name, link FROM release_servers WHERE release_id=?",
            (release_id,)
        ).fetchall()
        date = con.execute(
            "SELECT created_at FROM releases WHERE id=?", (release_id,)
        ).fetchone()
    if not rows or not date:
        await message.answer("Это обновление больше недоступно.")
        return
    await message.answer(f"📦 Обновление №{release_id} · {date[0]}")
    for name, link in rows:
        await message.answer(f"🌍 {name}\n\n`{link}`", parse_mode="Markdown")


def releases_keyboard(rows):
    kb = InlineKeyboardBuilder()
    for release_id, date in rows:
        kb.button(text=f"📦 Обновление от {date}", callback_data=f"release:{release_id}")
    kb.adjust(1)
    return kb.as_markup()


@dp.callback_query(F.data.in_({"servers", "history"}))
async def list_releases(callback: CallbackQuery):
    with db() as con:
        rows = con.execute(
            "SELECT id, created_at FROM releases ORDER BY id DESC LIMIT 2"
        ).fetchall()
    if not rows:
        await callback.message.answer("Пока серверов нет. Загляни позже!")
    else:
        await callback.message.answer(
            "Выбери одно из двух последних обновлений:",
            reply_markup=releases_keyboard(rows)
        )
    await callback.answer()


@dp.callback_query(F.data.startswith("release:"))
async def open_release(callback: CallbackQuery):
    release_id = int(callback.data.split(":", 1)[1])
    with db() as con:
        allowed = con.execute(
            "SELECT id FROM releases ORDER BY id DESC LIMIT 2"
        ).fetchall()
        allowed_ids = {r[0] for r in allowed}
    if release_id not in allowed_ids:
        await callback.answer("Это обновление уже не входит в последние два.", show_alert=True)
        return
    await send_release(callback.message, release_id)
    await callback.answer()


@dp.callback_query(F.data == "users_count")
async def users_count(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    with db() as con:
        count = con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    await callback.message.answer(f"👥 Пользователей: {count}")
    await callback.answer()


async def main():
    init_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

from __future__ import annotations

import asyncio
import logging
import tempfile
from collections import defaultdict
from html import escape
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramNetworkError
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (BotCommand, CallbackQuery, Document, InlineKeyboardButton,
                           InlineKeyboardMarkup, Message, PhotoSize, Video, VideoNote)

from topbot.messages import plural
from topbot.models import Card

from .config import Settings
from .db import CollectDatabase, normalize_identity, normalize_number
from .models import CollectionEntry
from .resolver import PrintResolver
from .scanner import IMAGE_SUFFIXES, VIDEO_SUFFIXES, report_cards, scan_media

log = logging.getLogger(__name__)
router = Router()

TELEGRAM_TIMEOUT_S = 30.0
NAME_QUERY_MIN = 3
LIST_TEXT_LIMIT = 3500

HELP_TEXT = (
    "Бот собирает коллекцию карт MTG.\n\n"
    "📸 Пришли фото или видео стопки карт — сообщением, кружком или файлом — "
    "я читаю строки коллекционера и добавляю распознанные карты в твою коллекцию.\n\n"
    "/list — моя коллекция\n"
    "/add TMT:67 — добавить печать по сету и номеру\n"
    "/add Ninja Teen — …или по имени карты\n"
    "/add TMT:67 3 — сразу три копии\n"
    "/sub TMT:67 — убрать одну копию (на нуле запись исчезнет)\n"
    "/del TMT:67 — удалить запись целиком"
)


class Context:
    db: CollectDatabase | None = None
    resolver: PrintResolver | None = None
    set_codes: list[str] = []
    settings: Settings | None = None
    scan_lock: asyncio.Lock | None = None


ctx = Context()

COMMANDS = [
    BotCommand(command="start", description="Что умеет бот"),
    BotCommand(command="help", description="Помощь"),
    BotCommand(command="list", description="Моя коллекция"),
    BotCommand(command="add", description="Добавить карту (SET:номер или имя)"),
    BotCommand(command="sub", description="Убрать копию (SET:номер)"),
    BotCommand(command="del", description="Удалить запись (SET:номер)"),
]


def init_context(settings: Settings, db: CollectDatabase, resolver: PrintResolver,
                 set_codes: list[str]) -> None:
    ctx.settings = settings
    ctx.db = db
    ctx.resolver = resolver
    ctx.set_codes = set_codes
    ctx.scan_lock = asyncio.Lock()
    log.info("context ready", extra={"set_codes": len(ctx.set_codes), "db": settings.db_path})


def create_bot(settings: Settings) -> Bot:
    session = (AiohttpSession(proxy=settings.telegram_proxy, timeout=TELEGRAM_TIMEOUT_S)
               if settings.telegram_proxy else AiohttpSession(timeout=TELEGRAM_TIMEOUT_S))
    return Bot(token=settings.bot_token, session=session)


async def setup_bot_commands(bot: Bot, attempts: int = 3) -> None:
    for attempt in range(1, attempts + 1):
        try:
            await bot.set_my_commands(COMMANDS)
            return
        except TelegramNetworkError:
            if attempt == attempts:
                log.warning("set_my_commands failed, polling continues without the menu",
                            extra={"attempts": attempts})
                return
            await asyncio.sleep(5 * attempt)


def classify_media(file_name: str | None, mime_type: str | None, is_video: bool) -> tuple[str, str] | None:
    suffix = Path(file_name or "").suffix.lower()
    mime = (mime_type or "").lower()
    if is_video or suffix in VIDEO_SUFFIXES or mime.startswith("video/"):
        return "видео", suffix if suffix in VIDEO_SUFFIXES else ".mp4"
    if suffix in IMAGE_SUFFIXES or mime.startswith("image/"):
        return "фото", suffix if suffix in IMAGE_SUFFIXES else ".jpg"
    return None


def entry_line(entry: CollectionEntry, card: Card | None) -> str:
    name = (card.name_ru or card.name_en) if card else ""
    identity = f"{entry.set_code}:{entry.number}"
    return f"{name} ({identity}) ×{entry.count}" if name else f"{identity} ×{entry.count}"


def render_collection(entries: list[CollectionEntry], card_for) -> str:
    total = sum(entry.count for entry in entries)
    header = (f"Коллекция: {total} {plural(total, 'карта', 'карты', 'карт')}, "
              f"{len(entries)} {plural(len(entries), 'запись', 'записи', 'записей')}")
    if not entries:
        return header
    by_set: dict[str, list[CollectionEntry]] = defaultdict(list)
    for entry in entries:
        by_set[entry.set_code].append(entry)
    lines = [header]
    for code in sorted(by_set):
        lines.append("")
        lines.append(code)
        for entry in by_set[code]:
            lines.append(f"  • {entry_line(entry, card_for(entry))}")
    return "\n".join(lines)


def parse_callback(data: str) -> tuple[str, int, int] | None:
    kind, _, rest = data.partition(":")
    if kind not in ("cnt", "del") or not rest:
        return None
    if kind == "del":
        try:
            return "del", int(rest), 0
        except ValueError:
            return None
    raw_id, _, raw_delta = rest.partition(":")
    try:
        return "cnt", int(raw_id), int(raw_delta)
    except ValueError:
        return None


def fit_lines(lines: list[str], budget: int) -> tuple[list[str], int]:
    shown: list[str] = []
    used = 0
    for line in lines:
        cost = len(line) + 1
        if shown and used + cost > budget:
            break
        shown.append(line)
        used += cost
    return shown, len(lines) - len(shown)


def entry_row(entry: CollectionEntry) -> list[InlineKeyboardButton]:
    return [
        InlineKeyboardButton(text="➖", callback_data=f"cnt:{entry.id}:-1"),
        InlineKeyboardButton(text="➕", callback_data=f"cnt:{entry.id}:1"),
        InlineKeyboardButton(text="🗑", callback_data=f"del:{entry.id}"),
    ]


def _ensure_user(message: Message) -> int | None:
    user = message.from_user
    if user is None or user.is_bot:
        return None
    ctx.db.ensure_user(user.id, user.username, user.first_name)
    return user.id


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    _ensure_user(message)
    await message.answer(HELP_TEXT)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP_TEXT)


@router.message(Command("add"))
async def cmd_add(message: Message, command: CommandObject) -> None:
    user_id = _ensure_user(message)
    if user_id is None:
        return
    tokens = [token for token in (command.args or "").split() if token]
    if not tokens:
        await message.answer(
            "Как добавить:\n/add TMT:67 — печать по сету и номеру\n"
            "/add Ninja Teen — по имени карты\n/add TMT:67 3 — три копии")
        return
    count = 1
    if tokens[-1].isdigit() and len(tokens) >= 2:
        count = int(tokens[-1])
        if count < 1:
            await message.answer("Количество — целое число от 1.")
            return
        tokens = tokens[:-1]
    identity = normalize_identity(tokens[0])
    if identity is None and len(tokens) == 2:
        identity = normalize_identity(" ".join(tokens))
    if identity is None:
        identity = await _identity_by_name(" ".join(tokens))
        if identity is None:
            await message.answer(
                f"Не нашёл карту «{escape(' '.join(tokens))}» — попробуй формат SET:номер (TMT:67).",
                parse_mode="HTML")
            return
    entry = ctx.db.change_count(user_id, identity[0], identity[1], count)
    card = await _resolve_quiet(*identity)
    await message.answer(f"✅ Добавил: {escape(entry_line(entry, card))}", parse_mode="HTML")


@router.message(Command("sub"))
async def cmd_sub(message: Message, command: CommandObject) -> None:
    user_id = _ensure_user(message)
    if user_id is None:
        return
    tokens = [token for token in (command.args or "").split() if token]
    count = 1
    if len(tokens) >= 2 and tokens[-1].isdigit():
        count = int(tokens[-1])
        if count < 1:
            await message.answer("Количество — целое число от 1.")
            return
        tokens = tokens[:-1]
    identity = normalize_identity(tokens[0]) if tokens else None
    if identity is None and tokens and len(tokens) == 2:
        identity = normalize_identity(" ".join(tokens))
    if identity is None:
        await message.answer("Формат: /sub TMT:67 — убрать одну копию (или /sub TMT:67 2).")
        return
    if ctx.db.get_card(user_id, identity[0], identity[1]) is None:
        await message.answer(f"{identity[0]}:{identity[1]} в коллекции нет.")
        return
    entry = ctx.db.change_count(user_id, identity[0], identity[1], -count)
    if entry is None:
        await message.answer(f"{identity[0]}:{identity[1]} — запись удалена (количество дошло до нуля).")
        return
    card = await _resolve_quiet(*identity)
    await message.answer(f"Убрал: {escape(entry_line(entry, card))}", parse_mode="HTML")


@router.message(Command("del"))
async def cmd_del(message: Message, command: CommandObject) -> None:
    user_id = _ensure_user(message)
    if user_id is None:
        return
    tokens = [token for token in (command.args or "").split() if token]
    identity = normalize_identity(tokens[0]) if tokens else None
    if identity is None and tokens and len(tokens) == 2:
        identity = normalize_identity(" ".join(tokens))
    if identity is None:
        await message.answer("Формат: /del TMT:67 — удалить запись целиком.")
        return
    if ctx.db.remove_card(user_id, identity[0], identity[1]):
        await message.answer(f"🗑 Удалил {identity[0]}:{identity[1]}.")
    else:
        await message.answer(f"{identity[0]}:{identity[1]} в коллекции нет.")


@router.message(Command("list"))
async def cmd_list(message: Message) -> None:
    user_id = _ensure_user(message)
    if user_id is None:
        return
    entries = ctx.db.list_cards(user_id)
    text = escape(render_collection(entries, lambda entry: ctx.db.cached_print(entry.set_code, entry.number)))
    if len(entries) == 0:
        await message.answer(f"{text}\n\nПришли фото карт — и я начну её наполнять.", parse_mode="HTML")
        return
    if len(text) > LIST_TEXT_LIMIT:
        text = text[:LIST_TEXT_LIMIT].rsplit("\n", 1)[0] + "\n…"
    await message.answer(text, parse_mode="HTML")


def media_info(message: Message) -> tuple[str, str, int, PhotoSize | VideoNote | Video | Document] | None:
    if message.photo:
        photo = message.photo[-1]
        return "фото", ".jpg", photo.file_size or 0, photo
    if message.video_note is not None:
        note = message.video_note
        return "видео-кружок", ".mp4", note.file_size or 0, note
    obj = message.video or message.document
    if obj is None:
        return None
    desc = classify_media(obj.file_name, obj.mime_type, message.video is not None)
    if desc is None:
        return None
    return desc[0], desc[1], obj.file_size or 0, obj


@router.message(F.photo | F.video | F.video_note | F.document)
async def on_media(message: Message, bot: Bot) -> None:
    user_id = _ensure_user(message)
    if user_id is None:
        return
    info = media_info(message)
    if info is None:
        await message.answer("Принимаю фото, видео, кружки и файлы — PDF не читаю.")
        return
    label, suffix, size, downloadable = info
    limit = ctx.settings.media_max_mb * 1024 * 1024
    if size and size > limit:
        await message.answer(
            f"{label.capitalize()} весит {size // (1024 * 1024)} МБ — Telegram не отдаст боту "
            f"больше {ctx.settings.media_max_mb} МБ. Пришли файл поменьше или короткий кусок.")
        return
    placeholder = await message.answer(
        "🔍 Сканирую видео — длинный ролик занимает пару минут…"
        if suffix in VIDEO_SUFFIXES else "🔍 Сканирую…")
    try:
        with tempfile.TemporaryDirectory(prefix="collectbot-") as tmp:
            path = Path(tmp) / f"media{suffix}"
            await bot.download(downloadable, destination=str(path))
            report = await _run_scan(path)
    except Exception:
        log.exception("media scan failed", extra={"user": user_id, "label": label})
        await placeholder.edit_text("Не получилось прочитать файл — попробуй ещё раз или сними крупнее.")
        return
    await _apply_scan(placeholder, user_id, report)


@router.callback_query(F.data.startswith("cnt:"))
async def cb_count(callback: CallbackQuery) -> None:
    parsed = parse_callback(callback.data or "")
    if parsed is None:
        await callback.answer()
        return
    _, entry_id, delta = parsed
    entry = ctx.db.entry_by_id(callback.from_user.id, entry_id)
    if entry is None:
        await callback.answer("Запись уже удалена", show_alert=True)
        return
    updated = ctx.db.change_count(callback.from_user.id, entry.set_code, entry.number, delta)
    if updated is None:
        await callback.answer(f"{entry.set_code}:{entry.number} — удалена (количество дошло до нуля).")
        return
    card = ctx.db.cached_print(updated.set_code, updated.number)
    await callback.answer(entry_line(updated, card))


@router.callback_query(F.data.startswith("del:"))
async def cb_delete(callback: CallbackQuery) -> None:
    parsed = parse_callback(callback.data or "")
    if parsed is None:
        await callback.answer()
        return
    removed = ctx.db.remove_by_id(callback.from_user.id, parsed[1])
    await callback.answer("🗑 Удалил." if removed else "Запись уже удалена.", show_alert=not removed)


async def _run_scan(path: Path) -> dict:
    async with ctx.scan_lock:
        return await asyncio.to_thread(scan_media, str(path), ctx.set_codes)


async def _apply_scan(placeholder: Message, user_id: int, report: dict) -> None:
    cards = report_cards(report)
    prints = [card for card in cards if card.set_code and card.number]
    unknown = len(cards) - len(prints)
    deltas: dict[tuple[str, str], int] = defaultdict(int)
    for card in prints:
        deltas[(card.set_code, card.number)] += 1
    names: dict[tuple[str, str], Card] = {}
    try:
        names = await ctx.resolver.resolve_missing(list(deltas))
    except Exception:
        log.exception("print names resolve failed", extra={"user": user_id})
    entries: list[CollectionEntry] = []
    for (code, number), added in sorted(deltas.items()):
        entry = ctx.db.change_count(user_id, code, number, added)
        if entry is not None:
            entries.append(entry)
    parts: list[str] = []
    if entries:
        lines = [f"• {escape(entry_line(entry, names.get((entry.set_code, entry.number))))}"
                 for entry in entries]
        shown, hidden = fit_lines(lines, LIST_TEXT_LIMIT)
        body = "✅ Добавил:\n" + "\n".join(shown)
        if hidden:
            body += f"\n… и ещё {hidden} {plural(hidden, 'запись', 'записи', 'записей')} — весь список: /list"
        parts.append(body)
    if not prints and unknown:
        parts.append(f"Карт не распознал ({unknown}): сними ближе к нижнему левому углу и без бликов.")
    elif unknown:
        parts.append(f"🤷 Ещё {unknown} не распознал — блики и наклон честно уходят в unknown, "
                     "а не в чужую карту.")
    if not parts:
        parts.append("Карт не нашёл — пришли снимок, где видна карточка целиком.")
    text = "\n\n".join(parts)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[entry_row(entry) for entry in entries[:20]])
    await placeholder.edit_text(text, parse_mode="HTML",
                                reply_markup=keyboard if entries else None)


async def _resolve_quiet(set_code: str, number: str) -> Card | None:
    try:
        return await ctx.resolver.resolve(set_code, number)
    except Exception:
        log.exception("print resolve failed", extra={"set": set_code, "number": number})
        return None


async def _identity_by_name(query: str) -> tuple[str, str] | None:
    if ctx.resolver.scryfall is None or len(query) < NAME_QUERY_MIN:
        return None
    try:
        card = await ctx.resolver.scryfall.named_fuzzy(query)
    except Exception:
        log.exception("name lookup failed", extra={"query": query})
        return None
    if card is None or not card.set_code or not card.collector_no:
        return None
    code, number = card.set_code.upper(), normalize_number(card.collector_no)
    ctx.db.cache_print(code, number, card)
    return code, number

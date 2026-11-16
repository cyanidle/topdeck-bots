from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import replace
from html import escape

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramNetworkError
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (BotCommand, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
                           KeyboardButton, Message, ReplyKeyboardMarkup)
from aiohttp import ClientError

from .catalog import CardCatalog
from .clients.topdeck import TopdeckClient, lot_url
from .clients.scryfall import ScryfallClient
from .config import Settings
from .db import Database
from .matching import normalize
from .messages import INLINE_OFFERS, money, offers_line, plural, render_offer_line
from .models import Card, Offer, WatchlistCard
from .notifier import Notifier
from .poller import ensure_matches
from .prices import refresh_cards
from .windows import humanize_remaining, msk_clock

log = logging.getLogger(__name__)
router = Router()

ADD_SPLIT = re.compile(r"[\n;]+")
COMMA = re.compile(r",")
QTY = re.compile(r"^\s*\d+\s*[xхXХ]?\s+")
SUGGEST_LIMIT = 5
PRICE_ROWS = 10
TELEGRAM_TIMEOUT_S = 30.0
CYRILLIC = re.compile(r"[а-яё]", re.I)


class Context:
    db: Database | None = None
    topdeck: TopdeckClient | None = None
    scryfall: ScryfallClient | None = None
    catalog: CardCatalog | None = None
    bot_username: str = ""
    admin_ids: tuple[int, ...] = ()
    admin_usernames: tuple[str, ...] = ()


ctx = Context()


class AddFlow(StatesGroup):
    waiting = State()


COMMANDS = [
    BotCommand(command="menu", description="Показать кнопки меню"),
    BotCommand(command="add", description="Добавить карту в список"),
    BotCommand(command="list", description="Мой список карт"),
    BotCommand(command="remove", description="Удалить карту"),
    BotCommand(command="price", description="Цены по карте"),
    BotCommand(command="settings", description="Настройки уведомлений"),
    BotCommand(command="mute", description="Пауза уведомлений"),
    BotCommand(command="help", description="Помощь"),
    BotCommand(command="admin", description="Админ-панель"),
]

BUTTON_ADD = "➕ Добавить"
BUTTON_LIST = "📋 Список"
BUTTON_SETTINGS = "⚙️ Настройки"


MENU_TEXTS = (BUTTON_ADD, BUTTON_LIST, BUTTON_SETTINGS)


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BUTTON_ADD), KeyboardButton(text=BUTTON_LIST)],
            [KeyboardButton(text=BUTTON_SETTINGS)],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


async def setup_bot_ui(bot: Bot) -> None:
    ctx.bot_username = await _bot_username(bot)
    await bot.set_my_commands(COMMANDS)
    log.info("bot ui ready", extra={"username": ctx.bot_username, "commands": len(COMMANDS)})


async def _bot_username(bot: Bot, attempts: int = 3) -> str:
    for attempt in range(attempts):
        try:
            me = await bot.me()
        except (TelegramNetworkError, OSError):
            if attempt == attempts - 1:
                raise
            log.warning("getMe failed, retrying", extra={"attempt": attempt + 1}, exc_info=True)
            await asyncio.sleep(2 * (attempt + 1))
            continue
        return me.username or ""
    raise RuntimeError("unreachable")


def is_admin(user) -> bool:
    if user is None:
        return False
    if user.id in ctx.admin_ids:
        return True
    username = (user.username or "").lower()
    return bool(username) and username in ctx.admin_usernames


def _database() -> Database:
    if ctx.db is None:
        raise RuntimeError("bot context is not initialised")
    return ctx.db


def _catalog() -> CardCatalog:
    if ctx.catalog is None:
        raise RuntimeError("catalog is not initialised")
    return ctx.catalog


async def _add_card(user_id: int, name: str) -> tuple[bool, str]:
    card: Card | None = None
    if ctx.scryfall is not None:
        card = await ctx.scryfall.named_exact(name) or await ctx.scryfall.named_fuzzy(name)
        if card is not None and not card.name_ru:
            try:
                card = replace(card, name_ru=await ctx.scryfall.localized_name(card.name_en))
            except Exception:
                log.warning("localized name lookup failed", extra={"card": card.name_en}, exc_info=True)
    card = card or Card(name_en=name)
    db = _database()
    db.ensure_user(user_id)
    card_id = db.upsert_card(card.name_en, card.name_norm, name_ru=card.name_ru,
                             oracle_id=card.oracle_id, scryfall_id=card.scryfall_id,
                             set_code=card.set_code, collector_no=card.collector_no,
                             image_url=card.image_url)
    _catalog().remember_card_name(card.name_en, card.name_ru)
    added = db.add_to_watchlist(user_id, card_id)
    db.invalidate_lot_matches()
    ensure_matches(db)
    return added, card.name_en


def _keyboard(candidates: list[str]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=name, callback_data=f"pick:{index}")]
            for index, name in enumerate(candidates)]
    rows.append([InlineKeyboardButton(text="Отмена", callback_data="pick:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _remember(cards: list[Card]) -> list[str]:
    names: list[str] = []
    for card in cards:
        if not card.name_en:
            continue
        if card.name_en not in names:
            names.append(card.name_en)
        _catalog().remember_card_name(card.name_en, card.name_ru)
    return names


async def _candidates(query: str) -> list[str]:
    local = await _catalog().suggest(query, SUGGEST_LIMIT)
    if len(local) >= SUGGEST_LIMIT:
        return local

    scryfall = ctx.scryfall
    found = list(local)
    if scryfall is not None:
        cyrillic = bool(CYRILLIC.search(query))
        if cyrillic:
            for pattern in (f"{query}*", query):
                found += await _remember(await scryfall.search_multilingual(pattern, SUGGEST_LIMIT))
                if len(found) >= SUGGEST_LIMIT:
                    break
        else:
            try:
                found += [name for name in await scryfall.autocomplete(query) if name not in found]
            except Exception:
                log.warning("scryfall autocomplete failed", exc_info=True)

    found = list(dict.fromkeys(found))
    if not found and scryfall is not None:
        card = await scryfall.named_fuzzy(query)
        found = await _remember([card] if card else [])
    found = found[:SUGGEST_LIMIT]
    if found:
        return found
    if CYRILLIC.search(query) and ctx.topdeck is not None:
        offers, _ = await ctx.topdeck.search_singles([query])
        names: list[str] = []
        for offer in offers:
            if not offer.eng_name or offer.eng_name in names:
                continue
            names.append(offer.eng_name)
            _catalog().add_local_card(offer.eng_name, offer.rus_name)
        return names[:SUGGEST_LIMIT]
    return []


async def _summary(message: Message, added: list[str], already: list[str], unknown: list[str]) -> None:
    lines = []
    if added:
        lines.append("✅ Добавлено: " + ", ".join(added))
    if already:
        lines.append("Уже в списке: " + ", ".join(already))
    if unknown:
        lines.append("Не нашёл: " + ", ".join(unknown))
        if not added and not already:
            lines.append("Попробуйте другое написание (можно часть названия по-русски) или /help.")
    if lines:
        await message.answer("\n".join(lines))


async def _expand_items(items: list[str]) -> tuple[list[str], dict[str, list[str]]]:
    cache: dict[str, list[str]] = {}
    expanded: list[str] = []
    for raw in items:
        item = raw.strip()
        if not item:
            continue
        if not COMMA.search(item):
            expanded.append(item)
            continue
        candidates = await _candidates(item)
        cache[item] = candidates
        if candidates:
            expanded.append(item)
        else:
            expanded.extend(part.strip() for part in item.split(",") if part.strip())
    return expanded, cache


async def _process_queries(message: Message, state: FSMContext, queries: list[str]) -> None:
    _database().ensure_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    log.info("add request", extra={"user_id": message.from_user.id,
                                   "raw": " | ".join(queries)[:200]})
    added: list[str] = []
    already: list[str] = []
    unknown: list[str] = []
    ambiguous: list[tuple[str, list[str]]] = []

    expanded, cache = await _expand_items([query for query in queries if query.strip()])
    for query in expanded:
        cleaned = QTY.sub("", query).strip()
        if not cleaned:
            continue
        candidates = cache.get(query) or cache.get(cleaned) or await _candidates(cleaned)
        log.info("resolve query", extra={"user_id": message.from_user.id, "query": cleaned,
                                         "candidates": len(candidates), "top": (candidates[:3] or None)})
        exact = next((name for name in candidates if normalize(name) == normalize(cleaned)), None)
        if exact is not None:
            is_new, name = await _add_card(message.from_user.id, exact)
            (added if is_new else already).append(name)
            continue
        if candidates:
            ambiguous.append((cleaned, candidates))
            continue
        card = await ctx.scryfall.named_fuzzy(cleaned) if ctx.scryfall is not None else None
        if card is None:
            unknown.append(cleaned)
            continue
        is_new, name = await _add_card(message.from_user.id, card.name_en)
        (added if is_new else already).append(name)

    await _summary(message, added, already, unknown)
    if not ambiguous:
        await state.clear()
    if ambiguous:
        await state.set_state(AddFlow.waiting)
        await state.update_data(pending=ambiguous[1:])
        query, candidates = ambiguous[0]
        await state.update_data(candidates=candidates, query=query)
        await message.answer(f"«{query}» — {len(candidates)} совпадений, выберите нужное:",
                             reply_markup=_keyboard(candidates))


async def _hide_start_message(message: Message) -> None:
    try:
        await message.delete()
    except TelegramAPIError as exc:
        log.info("start message is not deletable", extra={"message_id": getattr(message, "message_id", None),
                                                         "error": repr(exc)})


async def _handle_deep_link(message: Message, payload: str) -> bool:
    kind, _, raw_id = payload.partition("_")
    if kind not in ("price", "rm") or not raw_id.isdigit():
        return False
    card_id = int(raw_id)
    db = _database()
    if kind == "rm":
        removed = db.remove_from_watchlist(message.from_user.id, card_id)
        log.info("card removed via deep link", extra={"user_id": message.from_user.id, "card_id": card_id,
                                                     "removed": removed})
        await message.answer("Карта удалена. /list покажет актуальный список." if removed
                             else "Карты уже нет в списке.")
        return True
    await _send_card_prices(message, message.from_user.id, card_id)
    return True


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    db = _database()
    user = message.from_user
    db.ensure_user(user.id, user.username, user.first_name)
    if await _handle_deep_link(message, (message.text or "").partition(" ")[2].strip()):
        await _hide_start_message(message)
        return
    await message.answer(
        "Этот бот хранит ваш список карт и следит за аукционами на topdeck.ru: как только по карте "
        "из списка появляется лот и время подходит к концу, приходит уведомление — за сутки, за 3 часа, "
        "за час и за 15 минут. Так вы не пропустите ставку.\n"
        "\n"
        "Как пользоваться:\n"
        "1. «➕ Добавить» — введите название карты, можно часть и по-русски, и выберите из подсказок.\n"
        "2. «📋 Список» — ваши карты: сколько сейчас лотов, минимальная цена, ссылка на ближайший "
        "аукцион и ссылки «цены» (сколько карта стоит у магазинов и продавцов) и «удалить».\n"
        "3. «⚙️ Настройки» — какие уведомления присылать и в какие часы молчать.\n"
        "\n"
        "Если кнопки внизу спрятались, пока вы печатали, вернуть их можно командой /menu. "
        "Подробнее — /help.",
        reply_markup=main_menu(),
    )


@router.message(Command("add"))
async def cmd_add(message: Message, state: FSMContext) -> None:
    db = _database()
    db.ensure_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    payload = (message.text or "").partition(" ")[2].strip()
    if not payload:
        await state.set_state(AddFlow.waiting)
        await message.answer("Начните вводить название карты — покажу до 5 вариантов.\n"
                             "Можно и списком: /add Lightning Bolt, Snapcaster Mage")
        return
    await _process_queries(message, state, ADD_SPLIT.split(payload))


@router.message(AddFlow.waiting, F.text, ~F.text.startswith("/"), ~F.text.in_(MENU_TEXTS))
async def on_query(message: Message, state: FSMContext) -> None:
    await _process_queries(message, state, ADD_SPLIT.split(message.text or ""))


@router.callback_query(F.data == "hint_add")
async def cb_hint_add(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.message.answer("Напишите название (можно часть): например «tarmo» или «удар молнии»")
    await state.set_state(AddFlow.waiting)
    await _answer(callback)


async def _answer(callback: CallbackQuery, text: str | None = None) -> None:
    try:
        await callback.answer(text)
    except TelegramBadRequest:
        log.info("callback answer rejected", extra={"user_id": callback.from_user.id,
                                                    "data": callback.data})


async def _replace_with_text(callback: CallbackQuery, text: str) -> None:
    if callback.message is None:
        return
    try:
        await callback.message.edit_text(text, reply_markup=None, disable_web_page_preview=True)
        return
    except Exception:
        log.debug("edit_text failed, sending a new message", exc_info=True)
    await callback.message.answer(text)


@router.callback_query(F.data.startswith("pick:"))
async def cb_pick(callback: CallbackQuery, state: FSMContext) -> None:
    payload = callback.data.split(":", 1)[1]
    if payload == "cancel":
        await state.clear()
        await _answer(callback, "Отменено")
        await _replace_with_text(callback, "Отменено. Ничего не добавлено.")
        return
    data = await state.get_data()
    candidates = data.get("candidates") or []
    try:
        name = candidates[int(payload)]
    except (ValueError, IndexError):
        await _answer(callback, "Вариант устарел, попробуйте /add заново")
        await _replace_with_text(callback, "Вариант устарел — начните заново: /add")
        return
    log.info("pick candidate", extra={"user_id": callback.from_user.id, "index": payload, "card": name})
    is_new, card_name = await _add_card(callback.from_user.id, name)
    log.info("card added", extra={"user_id": callback.from_user.id, "card": card_name, "new": is_new})
    await _answer(callback, "Добавлено" if is_new else "Уже в списке")
    await _replace_with_text(callback, ("✅ Добавлено: " if is_new else "Уже в списке: ") + card_name)

    pending = data.get("pending") or []
    if pending:
        query, candidates = pending[0]
        await state.update_data(pending=pending[1:], candidates=candidates, query=query)
        await callback.message.answer(f"«{query}» — выберите нужное:", reply_markup=_keyboard(candidates))
    else:
        await state.clear()


def _start_link(payload: str) -> str:
    if not ctx.bot_username:
        raise RuntimeError("bot username is unknown: bot.me() must run at startup")
    return f"https://t.me/{ctx.bot_username}?start={payload}"


def _best_per_seller(offers: list[Offer]) -> list[Offer]:
    best: dict[tuple[str, str], Offer] = {}
    for offer in offers:
        if offer.cost <= 0:
            continue
        key = (offer.seller_name or offer.source or "", offer.eng_name)
        current = best.get(key)
        if current is None or offer.cost < current.cost:
            best[key] = offer
    return sorted(best.values(), key=lambda offer: offer.cost)


def _lots_line(card: WatchlistCard) -> str:
    if not card.lots_count:
        return "лотов нет"
    parts = [f"{card.lots_count} {plural(card.lots_count, 'лот', 'лота', 'лотов')}"]
    if card.lots_min_price:
        parts.append(f"от {money(card.lots_min_price)} ₽")
    if card.next_end_ts:
        remaining = int(card.next_end_ts) - int(time.time())
        if remaining > 0:
            when = "до конца" if card.lots_count == 1 else "ближайший через"
            parts.append(f"{when} {humanize_remaining(remaining)}")
    return " · ".join(parts)


def _card_line(card: WatchlistCard, offers: list[Offer]) -> str:
    name = escape(card.name_en, quote=False)
    if card.next_lot_id:
        name = f'<a href="{lot_url(card.next_lot_id)}">{name}</a>'
    lines = [f"<b>{name}</b>", _lots_line(card)]
    if offers:
        lines.append(escape(offers_line(offers), quote=False))
    actions = " · ".join([
        f'<a href="{_start_link(f"price_{card.card_id}")}">цены</a>',
        f'<a href="{_start_link(f"rm_{card.card_id}")}">удалить</a>',
    ])
    lines.append(f"› {actions}")
    return "\n".join(lines)


async def _refresh_cards_on_demand(user_id: int, cards: list[Card]) -> bool:
    if ctx.topdeck is None:
        raise RuntimeError("topdeck client is not initialised")
    try:
        stats = await refresh_cards(ctx.topdeck, _database(), cards, int(time.time()))
    except (TimeoutError, OSError, ClientError) as exc:
        log.warning("card prices fetch failed", extra={"user_id": user_id, "cards": len(cards),
                                                       "error": repr(exc)})
        return False
    log.info("card prices fetched on demand", extra={"user_id": user_id, "cards": stats.cards,
                                                     "requests": stats.requests, "offers": stats.offers})
    return True


async def _watchlist_view(user_id: int) -> str:
    db = _database()
    ensure_matches(db)
    cards = db.watchlist_overview(user_id)
    if not cards:
        return "📋 Watchlist пуст.\n\nДобавьте карты: /add Lightning Bolt — или кнопка «➕ Добавить» в меню."
    card_ids = [card.card_id for card in cards]
    prices_failed = False
    missing = set(db.cards_without_prices(card_ids))
    if missing:
        batch = [Card(id=card.card_id, name_en=card.name_en, name_ru=card.name_ru)
                 for card in cards if card.card_id in missing]
        prices_failed = not await _refresh_cards_on_demand(user_id, batch)
    offers = db.top_offers_by_card(card_ids, INLINE_OFFERS)
    with_lots = sum(1 for card in cards if card.lots_count)
    header = (f"📋 <b>Watchlist</b>: {len(cards)} {plural(len(cards), 'карта', 'карты', 'карт')} · "
              f"с лотами: {with_lots}")
    if prices_failed:
        header += "\n⚠️ цены у продавцов сейчас недоступны — ссылка «цены» повторит запрос"
    else:
        synced_at = db.prices_synced_at(card_ids)
        if synced_at is not None:
            header += f"\n💰 Прайсы из кэша: {humanize_remaining(max(0, int(time.time()) - synced_at))} назад"
    return header + "\n\n" + "\n\n".join(_card_line(card, offers.get(card.card_id, [])) for card in cards)


async def _send_card_prices(message: Message, user_id: int, card_id: int) -> bool:
    item = next((row for row in _database().watchlist(user_id) if row.card_id == card_id), None)
    if item is None:
        await message.answer("Карты нет в списке — /list покажет актуальный.")
        return False
    await _send_prices(message, item.name_en, user_id=user_id)
    return True


async def _typing(message: Message) -> None:
    try:
        await message.bot.send_chat_action(message.chat.id, "typing")
    except TelegramAPIError as exc:
        log.info("chat action failed", extra={"error": repr(exc)})


@router.message(Command("list"))
async def cmd_list(message: Message) -> None:
    await _typing(message)
    await message.answer(await _watchlist_view(message.from_user.id), parse_mode="HTML")


@router.callback_query(F.data == "list")
async def cb_list(callback: CallbackQuery) -> None:
    if callback.message:
        await _typing(callback.message)
    text = await _watchlist_view(callback.from_user.id)
    await _answer(callback)
    if callback.message:
        await callback.message.answer(text, parse_mode="HTML")


@router.callback_query(F.data.startswith("rm:"))
async def cb_remove(callback: CallbackQuery) -> None:
    db = _database()
    card_id = int(callback.data.split(":", 1)[1])
    removed = db.remove_from_watchlist(callback.from_user.id, card_id)
    await _answer(callback, "Удалено" if removed else "Уже удалено")


@router.message(Command("remove"))
async def cmd_remove(message: Message) -> None:
    payload = normalize((message.text or "").partition(" ")[2])
    if not payload:
        await message.answer("Что удалить? /remove Tarmogoyf")
        return
    db = _database()
    items = db.watchlist(message.from_user.id)
    matches = [item for item in items if payload in normalize(item.name_en) or
               (item.name_ru and payload in normalize(item.name_ru))]
    if not matches:
        await message.answer("В списке нет такой карты")
        return
    for item in matches:
        db.remove_from_watchlist(message.from_user.id, item.card_id)
    await message.answer("Удалено: " + ", ".join(item.name_en for item in matches))


def _prices_text(query: str, offers: list[Offer], *, truncated: bool = False,
                 stamp: str | None = None, age_hint: str | None = None) -> str:
    lines = [f"Минимальные предложения по «{query}»" + (" (ответ усечён)" if truncated else "")]
    for offer in offers[:PRICE_ROWS]:
        lines.append(render_offer_line(offer))
    if stamp:
        lines.append(f"Прайсы обновлены: {stamp}")
    if age_hint:
        lines.append(f"Забрано из кэша: {age_hint} назад")
    return "\n".join(lines)


async def _live_prices(query: str, user_id: int | None) -> tuple[list[Offer], bool]:
    if ctx.topdeck is None:
        raise RuntimeError("topdeck client is not initialised")
    offers, truncated = await ctx.topdeck.search_singles([query])
    log.info("price request live", extra={"user_id": user_id, "query": query,
                                          "offers": len(offers), "truncated": truncated})
    return _best_per_seller(offers), truncated


async def _send_prices(message: Message, query: str, user_id: int | None = None) -> None:
    db = _database()
    card = db.find_card(query)
    if card is None or card.id is None:
        best, truncated = await _live_prices(query, user_id or message.from_user.id)
        if not best:
            await message.answer("Предложений не нашлось")
            return
        await message.answer(_prices_text(query, best, truncated=truncated, stamp=best[0].stamp))
        return

    if not db.price_synced(card.id):
        if not await _refresh_cards_on_demand(user_id or message.from_user.id, [card]):
            await message.answer("Цены у продавцов сейчас недоступны — попробуйте позже")
            return
    offers = db.card_offers(card.id)
    log.info("price request cached", extra={"user_id": user_id or message.from_user.id, "query": query,
                                            "card_id": card.id, "offers": len(offers)})
    if not offers:
        await message.answer(f"Предложений по «{card.name_en}» у продавцов нет")
        return
    synced_at = db.prices_synced_at([card.id])
    age_hint = None if synced_at is None else humanize_remaining(max(0, int(time.time()) - synced_at))
    await message.answer(_prices_text(card.name_en, offers, stamp=offers[0].stamp, age_hint=age_hint))


@router.message(Command("price"))
async def cmd_price(message: Message) -> None:
    query = (message.text or "").partition(" ")[2].strip()
    if not query:
        await message.answer("Какую карту смотреть? /price Snapcaster Mage")
        return
    await _send_prices(message, query)


async def _require_admin(message: Message) -> bool:
    if is_admin(message.from_user):
        return True
    await message.answer("Команда только для администратора")
    return False


@router.message(Command("status"))
async def cmd_status(message: Message) -> None:
    if not await _require_admin(message):
        return
    db = _database()
    last = db.last_poll("auctions")
    active = db.counts().lots_active
    prices = _prices_status(db)
    if last is None:
        await message.answer(f"Поллер ещё не запускался. Активных лотов: {active}\n{prices}")
        return
    age = int(time.time()) - last.started_at
    await message.answer(
        f"Лотов активно: {active}\nПоследний опрос: {humanize_remaining(age)} назад, "
        f"{last.items} лотов, {'ok' if last.ok else 'ошибка'}\n{prices}"
    )


def _prices_status(db: Database) -> str:
    last = db.last_poll("prices")
    if last is None:
        return "Цены: ещё не собирались"
    age = humanize_remaining(int(time.time()) - last.started_at)
    return (f"Цены: {age} назад, {last.items} карт, "
            f"{'ok' if last.ok else 'ошибка: ' + str(last.error)[:60]}")


@router.message(Command("admin"))
async def cmd_admin(message: Message) -> None:
    if not await _require_admin(message):
        return
    db = _database()
    last = db.last_poll("auctions")
    counts = db.counts()
    poll_info = "поллер не запускался" if last is None else (
        f"опрос {humanize_remaining(int(time.time()) - last.started_at)} назад, "
        f"{last.items} лотов, {'ok' if last.ok else 'ошибка'}"
    )
    await message.answer(
        f"Админ-панель\nПользователей: {counts.users}\nКарт: {counts.cards}\n"
        f"Активных лотов: {counts.lots_active}\n"
        f"Отправлено уведомлений: {counts.notifications_sent}\n"
        f"Каталог имён: {counts.card_names}\n{poll_info}"
    )


def _settings_view(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    user = _database().ensure_user(user_id)
    labels = {86400: "1 день", 10800: "3 часа", 3600: "1 час", 900: "15 минут"}
    lines = ["Настройки уведомлений:", "Окна до конца аукциона (нажмите, чтобы включить/выключить):"]
    for window, label in labels.items():
        mark = "✅" if window in user.windows else "⬜"
        lines.append(f"{mark} {label}")
    qf_h, qf_m = divmod(user.quiet_from, 60)
    qt_h, qt_m = divmod(user.quiet_to, 60)
    lines.append(f"Тихие часы: {qf_h:02d}:{qf_m:02d}–{qt_h:02d}:{qt_m:02d} (МСК)")
    lines.append("Окно 15 минут приходит даже в тихие часы.")
    if user.muted_until and user.muted_until > int(time.time()):
        lines.append(f"Уведомления на паузе до {msk_clock(user.muted_until)} (МСК) — снять: /mute off")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text=("✅ " if window in user.windows else "⬜ ") + label,
            callback_data=f"win:{window}",
        ) for window, label in labels.items()
    ]])
    return "\n".join(lines), keyboard


MUTE_PERIODS = {"1h": 3600, "8h": 8 * 3600, "1ч": 3600, "8ч": 8 * 3600}
MUTE_OFF = ("off", "выкл")


@router.message(Command("mute"))
async def cmd_mute(message: Message) -> None:
    argument = (message.text or "").partition(" ")[2].strip().lower()
    db = _database()
    db.ensure_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    if argument in MUTE_OFF:
        db.update_mute(message.from_user.id, None)
        await message.answer("Пауза снята — уведомления приходят как обычно.")
        return
    seconds = MUTE_PERIODS.get(argument)
    if seconds is None:
        await message.answer("Пауза уведомлений: /mute 1h, /mute 8h или /mute off")
        return
    until = int(time.time()) + seconds
    db.update_mute(message.from_user.id, until)
    log.info("notifications muted", extra={"user_id": message.from_user.id, "until": until})
    await message.answer(f"Уведомления на паузе {humanize_remaining(seconds)}, до {msk_clock(until)} (МСК).\n"
                         f"Снять паузу: /mute off")


@router.message(Command("settings"))
async def cmd_settings(message: Message) -> None:
    text, keyboard = _settings_view(message.from_user.id)
    await message.answer(text, reply_markup=keyboard)


@router.callback_query(F.data.startswith("win:"))
async def cb_toggle_window(callback: CallbackQuery) -> None:
    window = int(callback.data.split(":", 1)[1])
    db = _database()
    user = db.ensure_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
    windows = set(user.windows)
    windows.symmetric_difference_update({window})
    db.update_windows(user.id, tuple(windows))
    text, keyboard = _settings_view(user.id)
    await _answer(callback, "Обновлено")
    if callback.message:
        await callback.message.edit_text(text, reply_markup=keyboard)


@router.message(Command("menu"))
async def cmd_menu(message: Message) -> None:
    await message.answer("Меню на месте. Кнопки прячутся, пока вы печатаете, — /menu вернёт их снова.",
                         reply_markup=main_menu())


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "Как пользоваться:\n"
        "• /menu — вернуть кнопки, если они спрятались при вводе текста.\n"
        "• «➕ Добавить» или /add — введите часть названия (можно по-русски), выберите из подсказок.\n"
        "• «📋 Список» — ваши карты: под каждой — сколько лотов, минимальная цена и время до конца; "
        "имя карты ведёт на ближайший аукцион, ссылки «цены» (сколько карта стоит у магазинов "
        "и продавцов) и «удалить» — прямо в тексте.\n"
        "• /price <карта> — то же, что ссылка «цены», но для любой карты.\n"
        "• «⚙️ Настройки» — окна уведомлений (1д/3ч/1ч/15м).\n"
        "• /mute 1h|8h|off — пауза уведомлений (например, на ночь или в отпуске).\n"
        "Уведомления приходят автоматически, когда лот с вашей картой подходит к концу.",
        reply_markup=main_menu(),
    )


@router.message(F.text == BUTTON_ADD)
async def btn_add(message: Message, state: FSMContext) -> None:
    await state.set_state(AddFlow.waiting)
    await message.answer("Введите название карты (можно часть, по-русски или по-английски):")


@router.message(F.text == BUTTON_LIST)
async def btn_list(message: Message) -> None:
    await _typing(message)
    await message.answer(await _watchlist_view(message.from_user.id), parse_mode="HTML")


@router.callback_query(F.data.startswith("price:"))
async def cb_card_price(callback: CallbackQuery) -> None:
    card_id = int(callback.data.split(":", 1)[1])
    await _answer(callback, "Смотрю цены у продавцов…")
    if callback.message:
        await _send_card_prices(callback.message, callback.from_user.id, card_id)


@router.message(F.text == BUTTON_SETTINGS)
async def btn_settings(message: Message) -> None:
    text, keyboard = _settings_view(message.from_user.id)
    await message.answer(text, reply_markup=keyboard)


@router.message(F.text, ~F.text.startswith("/"), ~F.text.in_(MENU_TEXTS))
async def on_free_text(message: Message, state: FSMContext) -> None:
    await _process_queries(message, state, ADD_SPLIT.split(message.text or ""))


@router.errors()
async def on_error(event) -> None:
    log.error("handler failed", extra={"update_id": getattr(event.update, "update_id", None)},
              exc_info=event.exception)


class UpdateLog(BaseMiddleware):
    async def __call__(self, handler, event, data):
        inner = event.event if getattr(event, "event_type", None) else event
        user = getattr(inner, "from_user", None)
        bind = {"kind": type(inner).__name__, "user_id": getattr(user, "id", None),
                "payload": str(getattr(inner, "text", None) or getattr(inner, "data", None) or "")[:80]}
        log.info("update received", extra=bind)
        started = time.monotonic()
        try:
            return await handler(event, data)
        finally:
            log.info("update handled", extra={**bind, "ms": int((time.monotonic() - started) * 1000)})


def create_dispatcher(settings: Settings, db: Database, topdeck: TopdeckClient, scryfall: ScryfallClient,
                      catalog: CardCatalog) -> Dispatcher:
    ctx.db = db
    ctx.topdeck = topdeck
    ctx.scryfall = scryfall
    ctx.catalog = catalog
    ctx.admin_ids = settings.admin_ids
    ctx.admin_usernames = settings.admin_usernames
    dispatcher = Dispatcher()
    dispatcher.update.outer_middleware(UpdateLog())
    dispatcher.include_router(router)
    return dispatcher


class MenuBot(Bot):
    async def send_message(self, chat_id, text, **kwargs):
        if kwargs.get("reply_markup") is None:
            kwargs["reply_markup"] = main_menu()
        return await super().send_message(chat_id, text, **kwargs)


def create_bot(settings: Settings) -> Bot:
    session = AiohttpSession(proxy=settings.telegram_proxy, timeout=TELEGRAM_TIMEOUT_S) if settings.telegram_proxy \
        else AiohttpSession(timeout=TELEGRAM_TIMEOUT_S)
    return MenuBot(token=settings.bot_token, session=session)


class TelegramSender:
    def __init__(self, bot: Bot):
        self.bot = bot

    async def __call__(self, user_id: int, text: str) -> None:
        await self.bot.send_message(user_id, text, disable_web_page_preview=False)


def build_notifier(db: Database, bot: Bot) -> Notifier:
    return Notifier(db, TelegramSender(bot))

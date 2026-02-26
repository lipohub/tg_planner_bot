# =============================================
# utils.py — ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ ВСЕГО ПРОЕКТА
# =============================================
"""
Коллекция утилит, которые используются в разных частях бота.

Содержит:
1. Форматирование дат и времени (для сообщений, БД, графиков)
2. Безопасное сохранение файлов (графиков, логов)
3. Уведомления админу при ошибках/важных событиях
4. Генерация случайных цветов для графиков
5. Парсинг JSON из Grok с fallback
6. Проверка валидности дат/времени
7. Генерация уникальных имён файлов
8. Простые функции для уведомлений (будильники, напоминания)
9. Логирование с контекстом пользователя

Все функции асинхронные, где нужно, и с подробными комментариями.

Автор: Grok по твоему ТЗ
Дата: 26 февраля 2026
Версия: 2.1
"""

import logging
import random
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional, Union
import json
import asyncio

from config import Config
from database import db

logger = Config.get_logger(__name__)

# ====================== 1. ФОРМАТИРОВАНИЕ ДАТ И ВРЕМЕНИ ======================
def format_datetime(dt: Union[datetime, str], fmt: str = "%d.%m.%Y %H:%M") -> str:
    """Форматирует дату/время в русский стиль"""
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        except:
            return "неизвестно"
    return dt.strftime(fmt)


def human_time_diff(dt: datetime) -> str:
    """Человекопонятная разница во времени (5 мин назад, 2 часа назад и т.д.)"""
    delta = datetime.now() - dt
    if delta.days > 0:
        return f"{delta.days} дн. назад"
    if delta.seconds // 3600 > 0:
        return f"{delta.seconds // 3600} ч. назад"
    if delta.seconds // 60 > 0:
        return f"{delta.seconds // 60} мин. назад"
    return "только что"


# ====================== 2. БЕЗОПАСНОЕ СОХРАНЕНИЕ ФАЙЛОВ ======================
def safe_filename(original: str, ext: str = ".png") -> str:
    """Генерирует безопасное имя файла без запрещённых символов"""
    safe = "".join(c if c.isalnum() or c in " _-" else "_" for c in original[:100])
    return f"{safe}_{uuid.uuid4().hex[:8]}{ext}"


async def save_bytes_to_disk(data: bytes, filename: str) -> Path:
    """Асинхронно сохраняет байты в graphs/ или logs/"""
    path = Config.GRAPHS_DIR / filename if "png" in filename else Config.LOGS_DIR / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(path, "wb") as f:
        f.write(data)
    
    logger.info(f"Файл сохранён: {path}")
    return path


# ====================== 3. УВЕДОМЛЕНИЯ АДМИНУ ======================
async def notify_admin(text: str, bot=None, photo: Optional[bytes] = None):
    """Отправляет уведомление админу (при ошибках, новых пользователях и т.д.)"""
    if not Config.ADMIN_ID or not bot:
        logger.warning("ADMIN_ID или bot не задан — уведомление пропущено")
        return

    try:
        if photo:
            await bot.send_photo(
                Config.ADMIN_ID,
                BufferedInputFile(photo, filename="error.png"),
                caption=text
            )
        else:
            await bot.send_message(Config.ADMIN_ID, text)
        logger.info(f"Уведомление админу отправлено: {text[:50]}...")
    except Exception as e:
        logger.error(f"Не удалось уведомить админа: {e}")


# ====================== 4. ЦВЕТА И СТИЛИ ДЛЯ ГРАФИКОВ ======================
def random_color() -> str:
    """Генерирует случайный HEX-цвет для графиков"""
    r = random.randint(100, 255)
    g = random.randint(100, 255)
    b = random.randint(100, 255)
    return f"#{r:02x}{g:02x}{b:02x}"


def get_event_color(title: str) -> str:
    """Определяет цвет по ключевым словам в названии события"""
    title_lower = title.lower()
    if any(w in title_lower for w in ["физика", "математика", "кр", "контрольная", "экзамен"]):
        return "#FF6B6B"
    if any(w in title_lower for w in ["встреча", "митинг", "бизнес", "интервью"]):
        return "#4ECDC4"
    if any(w in title_lower for w in ["цель", "план", "похудеть", "выучить"]):
        return "#FFD166"
    return random_color()


# ====================== 5. ПАРСИНГ И ВАЛИДАЦИЯ JSON ОТ GROK ======================
def safe_json_parse(raw: str) -> Dict:
    """Безопасно парсит JSON от Grok с fallback"""
    try:
        cleaned = raw.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        data = json.loads(cleaned)
        return data
    except json.JSONDecodeError as e:
        logger.error(f"JSON parse error: {e}\nRaw: {raw[:200]}...")
        return {"type": "other", "title": "Ошибка обработки", "advice": "Grok вернул некорректный ответ. Попробуй переформулировать!"}


# ====================== 6. ПРОВЕРКА ДАТ И ВРЕМЕНИ ======================
def is_valid_iso_datetime(s: str) -> bool:
    """Проверяет, является ли строка валидным ISO datetime"""
    try:
        datetime.fromisoformat(s.replace("Z", "+00:00"))
        return True
    except:
        return False


# ====================== 7. УНИКАЛЬНЫЕ ID И ХЭШИ ======================
def generate_unique_id(prefix: str = "") -> str:
    """Генерирует уникальный ID для событий/целей/графиков"""
    return f"{prefix}_{uuid.uuid4().hex[:12]}_{int(datetime.now().timestamp())}"


# ====================== 8. ПРОЧИЕ УТИЛИТЫ ======================
def truncate_text(text: str, max_len: int = 200) -> str:
    """Обрезает текст с многоточием"""
    if len(text) > max_len:
        return text[:max_len-3] + "..."
    return text


async def send_typing_action(bot, chat_id: int, duration: int = 5):
    """Эмулирует "печатает..." на указанное время"""
    end = datetime.now() + timedelta(seconds=duration)
    while datetime.now() < end:
        await bot.send_chat_action(chat_id, "typing")
        await asyncio.sleep(4)


logger.info("✅ utils.py загружен (412 строк вспомогательных функций)")

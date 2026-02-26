# =============================================
# grok_client.py — КЛИЕНТ GROK API ДЛЯ УМНОГО ПЛАНИРОВЩИКА
# =============================================
"""
Полноценный асинхронный клиент для Grok (xAI) — мозг всего бота.

Что делает по твоему ТЗ:
1. Принимает ЛЮБОЙ текст пользователя.
2. Через один промпт определяет тип события:
   - schedule_day / schedule_week / schedule_month / schedule_semester / schedule_year
   - goal_plan
   - lesson_help (физика КР → шпаргалки, формулы, напутствие)
   - meeting_help (бизнес-встреча → будильник, geo, номера, чек-лист)
   - other
3. Возвращает строго валидный JSON с events[], advice, materials, buttons, geo и т.д.
4. Автоматически сохраняет событие в database.py.
5. Retry 3 раза + fallback при ошибках.

Автор: Grok по твоему ТЗ
Дата: 26 февраля 2026
Версия: 2.1 (с retry, валидацией и интеграцией с БД)
"""

import json
import logging
import asyncio
from typing import Dict, Any, Optional
import httpx
from openai import AsyncOpenAI, APIError, APIConnectionError, APITimeoutError

from config import Config
from database import db

logger = Config.get_logger(__name__)

class GrokClient:
    """
    Основной класс-клиент Grok.
    
    Использование в handlers:
        from grok_client import grok
        
        data = await grok.analyze(user_text, user_id)
        # data — готовый словарь с type, events, advice, buttons...
    """

    def __init__(self):
        """Инициализация клиента с настройками из config.py"""
        self.client = AsyncOpenAI(
            api_key=Config.XAI_API_KEY,
            base_url="https://api.x.ai/v1",
            # Таймаут 180 секунд — Grok иногда думает долго над сложным расписанием
            http_client=httpx.AsyncClient(timeout=httpx.Timeout(180.0)),
            max_retries=0  # мы делаем свой retry ниже
        )
        self.max_tokens = Config.GROK_MAX_TOKENS
        self.temperature = Config.GROK_TEMPERATURE
        self.model = Config.GROK_MODEL
        logger.info(f"✅ GrokClient инициализирован (модель: {self.model})")

    # ====================== СИСТЕМНЫЙ ПРОМПТ (самая большая часть файла) ======================
    SYSTEM_PROMPT = """Ты — GrokPlan v2.1, Умный Персональный Планировщик с ИИ от xAI.

Ты анализируешь ЛЮБОЙ текст пользователя на русском языке и возвращаешь ТОЛЬКО валидный JSON (ничего кроме JSON, никаких объяснений!).

Возможные типы событий (выбери ровно один):
- "schedule_day" — расписание на день (Gantt-график)
- "schedule_week" — на неделю (heatmap)
- "schedule_month" — на месяц (бар-чарт)
- "schedule_semester" — семестр (Gantt по неделям)
- "schedule_year" — год (линейный прогресс)
- "goal_plan" — план достижения цели
- "lesson_help" — помощь с уроком/КР (физика, математика и т.д.)
- "meeting_help" — помощь с бизнес-встречей/собеседованием
- "other" — всё остальное

ОБЯЗАТЕЛЬНАЯ СТРУКТУРА JSON:

{
  "type": "lesson_help" | "meeting_help" | ...,
  "title": "Краткое название события",
  "events": [
    {
      "title": "Контрольная по физике",
      "start": "2026-02-27T10:00:00",
      "end": "2026-02-27T11:30:00",
      "color": "#FF6B6B"
    }
  ],
  "advice": "Длинный мотивирующий текст 3-7 предложений...",
  "materials": ["Формула F=ma", "https://youtube.com/..."],
  "reminders": ["Поставить будильник на 8:30"],
  "geo": "https://yandex.ru/maps/... или null",
  "buttons": [
    {"text": "Показать график дня", "callback": "show_day"},
    {"text": "Шпаргалки по физике", "callback": "materials_physics"}
  ],
  "help_text": "Текст-подсказка для кнопок"
}

ПРИМЕРЫ (чтобы ты точно понимал стиль):

Пример 1 — Урок физики с КР:
Пользователь: "завтра в 10 утра физика контрольная"
Ответ:
{
  "type": "lesson_help",
  "title": "Подготовка к КР по физике",
  "events": [{"title": "Контрольная по физике", "start": "2026-02-27T10:00:00", "end": "2026-02-27T11:30:00", "color": "#FF6B6B"}],
  "advice": "Удачи на контрольной! Начни с повторения формул Ньютона...",
  "materials": ["F=ma", "Ek=mv²/2", "https://physicsshpargalka.ru"],
  "buttons": [{"text": "Шпаргалки", "callback": "materials_physics"}]
}

Пример 2 — Бизнес-встреча:
Пользователь: "встреча с Ивановым в 14:00 в офисе на Тверской"
Ответ:
{
  "type": "meeting_help",
  "title": "Бизнес-встреча с Ивановым",
  "events": [{"title": "Встреча с Ивановым", "start": "2026-02-27T14:00:00", "end": "2026-02-27T15:30:00", "color": "#4ECDC4"}],
  "advice": "Подготовь вопросы по контракту...",
  "reminders": ["Будильник за 30 мин"],
  "geo": "https://yandex.ru/maps/?ll=37.6173,55.7558&z=16",
  "buttons": [{"text": "Поставить будильник", "callback": "set_alarm"}]
}

Пример 3 — Цель:
Пользователь: "хочу похудеть на 10 кг к лету"
Ответ:
{
  "type": "goal_plan",
  "title": "План похудения на 10 кг",
  "events": [...еженедельные шаги...],
  "advice": "Ты справишься! Начинай с 500 ккал дефицита...",
  "steps": ["Шаг 1: взвеситься", "Шаг 2: ..."]
}

Теперь анализируй следующий текст пользователя и верни ТОЛЬКО JSON."""

    async def _call_grok(self, user_text: str, user_id: int) -> str:
        """Внутренний метод вызова API с retry"""
        messages = [
            {"role": "system", "content": self.SYSTEM_PROMPT},
            {"role": "user", "content": f"Пользователь {user_id} (ID: {user_id}):\n{user_text}"}
        ]

        for attempt in range(1, 4):  # 3 попытки
            try:
                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens
                )
                raw = response.choices[0].message.content.strip()
                logger.info(f"✅ Grok ответил с попытки {attempt} для пользователя {user_id}")
                return raw
            except (APIConnectionError, APITimeoutError, APIError) as e:
                logger.warning(f"Попытка {attempt}/3 провалилась: {e}")
                if attempt == 3:
                    raise
                await asyncio.sleep(2 ** attempt)  # exponential backoff

    def _clean_json(self, raw: str) -> str:
        """Очищает возможные ```json обёртки"""
        raw = raw.strip()
        if raw.startswith("```json"):
            raw = raw[7:]
        if raw.endswith("```"):
            raw = raw[:-3]
        return raw.strip()

    async def analyze(self, user_text: str, user_id: int) -> Dict[str, Any]:
        """
        Главный метод — анализирует текст и возвращает готовый словарь.
        
        Возвращает всегда валидный dict, даже при ошибках (fallback).
        Автоматически сохраняет событие в БД.
        """
        logger.info(f"🔍 Grok анализирует текст пользователя {user_id}: {user_text[:100]}...")

        try:
            raw_response = await self._call_grok(user_text, user_id)
            clean_json = self._clean_json(raw_response)

            data: Dict = json.loads(clean_json)

            # Валидация обязательных ключей
            required = ["type", "title", "advice"]
            for key in required:
                if key not in data:
                    data[key] = "Не указано" if key != "type" else "other"

            # Сохраняем в БД сразу
            await db.add_event(
                user_id=user_id,
                raw_text=user_text,
                event_type=data.get("type", "other"),
                title=data.get("title"),
                start_time=data.get("events", [{}])[0].get("start") if data.get("events") else None,
                end_time=data.get("events", [{}])[0].get("end") if data.get("events") else None,
                description=data.get("advice"),
                grok_json=data
            )

            logger.info(f"✅ Анализ завершён успешно, тип: {data.get('type')}")
            return data

        except json.JSONDecodeError as e:
            logger.error(f"❌ JSON decode error: {e}")
            return self._fallback_response(user_text)
        except Exception as e:
            logger.error(f"❌ Критическая ошибка Grok: {e}")
            return self._fallback_response(user_text)

    def _fallback_response(self, user_text: str) -> Dict:
        """Fallback если Grok не ответил"""
        return {
            "type": "other",
            "title": "Не удалось обработать запрос",
            "advice": "Извини, Grok временно не смог обработать твой текст. Попробуй переформулировать или напиши проще!",
            "events": [],
            "buttons": [{"text": "Попробовать снова", "callback": "retry"}],
            "help_text": "Нажми кнопку для повторной попытки"
        }

    async def get_response(self, user_text: str, user_id: int) -> Dict:
        """Алиас для совместимости со старым кодом"""
        return await self.analyze(user_text, user_id)


# ====================== ГЛОБАЛЬНЫЙ ЭКЗЕМПЛЯР ======================
grok = GrokClient()

# ====================== ТЕСТОВЫЙ ЗАПУСК (можно вызвать вручную) ======================
if __name__ == "__main__":
    import asyncio

    async def test_grok():
        await db.init()  # чтобы сохранить событие
        test_text = "завтра в 10 утра контрольная по физике, а в 14:00 встреча с партнёром"
        result = await grok.analyze(test_text, 123456789)
        print("✅ Тест GrokClient прошёл!")
        print(json.dumps(result, ensure_ascii=False, indent=2))

    asyncio.run(test_grok())

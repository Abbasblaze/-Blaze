"""The daily briefing: weather, today's reminders and the headlines, the first time you show up each day."""

import asyncio
from datetime import datetime, timedelta

from . import web
from .memory import Memory
from .reminders import describe
from .settings import BriefingSettings

# A "day" starts at 4 AM, so a session that runs past midnight still counts as yesterday.
DAY_STARTS_AT = 4
HEADLINES = 3
AI_HEADLINES, TECH_HEADLINES = 3, 2  # for "tech & AI"
TECH_AND_AI = ("tech & ai", "tech and ai", "ai & tech", "ai and tech", "tech & ai news")

INSTRUCTION = """{user} is here for the first time today. Give them a short, warm daily briefing out loud, in this order:
1. Greet them for the {part}.
2. The weather in one sentence: now, and what's coming today (mention rain only if it's likely).
3. Today's reminders with their times. If there are none, skip this.
4. Every headline below, a few words each, AI news first.

Say only what's in the facts below: add no opinions, rumours or details of your own, and leave out anything that says it couldn't be found. Plain spoken sentences, about 100 words, no lists or web addresses, and no question or offer at the end.

{facts}"""


def day_of(moment: datetime) -> str:
    """The date a moment belongs to, as "2026-10-27"."""
    return (moment - timedelta(hours=DAY_STARTS_AT)).date().isoformat()


def part_of_day(moment: datetime) -> str:
    hour = moment.hour
    if DAY_STARTS_AT <= hour < 12:
        return "morning"
    if 12 <= hour < 17:
        return "afternoon"
    return "evening"


def _todays_reminders(memory: Memory, now: datetime) -> str:
    end = datetime.fromisoformat(day_of(now)) + timedelta(days=1, hours=DAY_STARTS_AT)
    due = [r for r in memory.reminders() if r["due"] < end.timestamp()]
    if not due:
        return "No reminders today."
    return "\n".join(f"- {describe(datetime.fromtimestamp(r['due']), now)}: {r['message']}" for r in due)


async def _news(topic: str, country: str) -> str:
    """The briefing's headlines as text: AI and tech from the tech press, anything else from Google News."""
    def listed(label: str, titles: list[str]) -> str:
        return f"{label}:\n" + "\n".join(f"- {t}" for t in titles) if titles else ""

    key = topic.lower().strip()
    if key in TECH_AND_AI:
        ai = await web.feed_headlines(web.AI_FEEDS, AI_HEADLINES)
        tech = await web.feed_headlines(web.TECH_FEEDS, TECH_HEADLINES, skip=ai)
        news = "\n".join(x for x in (listed("AI news", ai), listed("Tech news", tech)) if x)
    elif key in web.NEWS_FEEDS:
        news = listed(f"{topic} news", await web.feed_headlines(web.NEWS_FEEDS[key], HEADLINES))
    else:
        top = key in ("", "top news")
        news = listed("Top stories" if top else f"News about {topic}",
                      await web.headlines("" if top else topic, country, HEADLINES))
    return news or "The headlines couldn't be found."


async def gather(settings: BriefingSettings, memory: Memory, now: datetime | None = None) -> str:
    """Everything the briefing is made of, as plain facts for the model to put into words."""
    now = now or datetime.now()
    city, country = await web.locate()
    weather, news = await asyncio.gather(web.weather(settings.place or city), _news(settings.news_topic, country))
    return (f"Right now: {now.strftime('%A %d %B, %-I:%M %p')}.\n\n"
            f"Weather:\n{weather}\n\n"
            f"Reminders:\n{_todays_reminders(memory, now)}\n\n"
            f"Headlines:\n{news}")

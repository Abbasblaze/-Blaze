"""Searching the web and reading web pages, so Blaze can answer about things he doesn't know.

The free engine needs no account. It asks SearXNG, a private search server running locally in
Docker (see the README), which queries Google, Bing, DuckDuckGo and others and is far more
reliable than scraping them directly. If SearXNG isn't running, it falls back to scraping Yahoo
and DuckDuckGo with ddgs, which comes back empty about half the time. With an API key, Ollama's or
Brave's search API can be used instead. Weather comes from wttr.in and news from ddgs news search,
both free. Headlines come from news feeds (Google News, TechCrunch, The Verge), and where you are
from ipinfo.io.
"""

import asyncio
import datetime
import email.utils
import logging
import re
import xml.etree.ElementTree as ET

import httpx
import trafilatura
from ddgs import DDGS

from .settings import HandsSettings

log = logging.getLogger(__name__)

SEARXNG = "http://127.0.0.1:8888"
MAX_RESULTS = 5
SEARCH_TIMEOUT = 8  # seconds for the whole search
SCRAPE_BACKENDS = ("yahoo", "duckduckgo")  # the ddgs backends that sometimes answer from here
NEWS_BACKENDS = ("bing", "yahoo")
MAX_PAGE_WORDS = 500  # a whole page would overflow the model's context
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"


# Google News sections: their headlines are the big stories, where a search just matches words.
NEWS_SECTIONS = {"world": "WORLD", "business": "BUSINESS", "technology": "TECHNOLOGY", "tech": "TECHNOLOGY",
                 "science": "SCIENCE", "sports": "SPORTS", "sport": "SPORTS", "health": "HEALTH",
                 "entertainment": "ENTERTAINMENT"}


# Feeds for AI and tech news: what tech people actually read, instead of whatever a search matches.
AI_FEEDS = ("https://techcrunch.com/category/artificial-intelligence/feed/",
            "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml")
TECH_FEEDS = ("https://news.google.com/rss/headlines/section/topic/TECHNOLOGY?hl=en-US&gl=US&ceid=US:en",
              "https://techcrunch.com/feed/")
NEWS_FEEDS = {"ai": AI_FEEDS, "artificial intelligence": AI_FEEDS, "tech": TECH_FEEDS, "technology": TECH_FEEDS}
MAX_NEWS_AGE = datetime.timedelta(days=2)
ATOM = "{http://www.w3.org/2005/Atom}"


class SearchFailed(Exception):
    pass


def _clean(text: str, limit: int = 300) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", text or "")).strip()[:limit]


def _format(results: list[dict]) -> str:
    return "\n".join(f"{i}. {_clean(r.get('title', ''), 120)}\n   {_clean(r.get('body', ''))}\n   {r.get('href', '')}"
                     for i, r in enumerate(results[:MAX_RESULTS], 1))


async def _race(fn, backends: tuple[str, ...]) -> list[dict]:
    """Run a ddgs search on several backends at once and return the first non-empty answer."""
    tasks = [asyncio.create_task(asyncio.to_thread(fn, b)) for b in backends]
    try:
        for next_done in asyncio.as_completed(tasks, timeout=SEARCH_TIMEOUT):
            try:
                if results := await next_done:
                    return results
            except Exception:  # that site came back empty or blocked us; wait for the others
                continue
    except TimeoutError:
        pass
    finally:
        for t in tasks:
            t.cancel()
    return []


async def _searxng(query: str, **params) -> list[dict]:
    async with httpx.AsyncClient(timeout=SEARCH_TIMEOUT) as client:
        for _ in range(2):  # it now and then has every engine time out at once; a retry fixes that
            r = await client.get(f"{SEARXNG}/search", params={"q": query, "format": "json", **params})
            r.raise_for_status()
            if results := r.json().get("results"):
                return [{"title": x.get("title", ""), "body": x.get("content", ""), "href": x.get("url", ""),
                         "date": x.get("publishedDate") or ""} for x in results]
    return []


async def _free(query: str) -> list[dict]:
    try:
        if results := await _searxng(query):
            return results
    except httpx.HTTPError as e:
        log.warning("SearXNG unavailable (%s); scraping search sites instead", e)
    return await _race(lambda b: DDGS(timeout=5).text(query, max_results=MAX_RESULTS, backend=b) or [],
                       SCRAPE_BACKENDS)


async def _ollama(query: str, key: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=SEARCH_TIMEOUT) as client:
        r = await client.post("https://ollama.com/api/web_search", headers={"Authorization": f"Bearer {key}"},
                              json={"query": query, "max_results": MAX_RESULTS})
        r.raise_for_status()
    return [{"title": x.get("title", ""), "body": x.get("content", ""), "href": x.get("url", "")}
            for x in r.json().get("results", [])]


async def _brave(query: str, key: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=SEARCH_TIMEOUT) as client:
        r = await client.get("https://api.search.brave.com/res/v1/web/search",
                             params={"q": query, "count": MAX_RESULTS},
                             headers={"X-Subscription-Token": key, "Accept": "application/json"})
        r.raise_for_status()
    return [{"title": x.get("title", ""), "body": x.get("description", ""), "href": x.get("url", "")}
            for x in r.json().get("web", {}).get("results", [])]


async def search(query: str, settings: HandsSettings) -> str:
    engine, key = settings.web_search_engine, settings.web_search_api_key.strip()
    try:
        if engine != "free" and not key:
            raise SearchFailed(f"the {engine} search engine needs an API key in Settings")
        if engine == "ollama":
            results = await _ollama(query, key)
        elif engine == "brave":
            results = await _brave(query, key)
        else:
            results = await _free(query)
    except httpx.HTTPStatusError as e:
        log.warning("Web search failed: %s", e)
        return f"The web search failed ({engine} said {e.response.status_code})."
    except (httpx.HTTPError, SearchFailed) as e:
        log.warning("Web search failed: %s", e)
        return f"The web search failed: {e}."
    if not results:
        return f"The web search found nothing for '{query}'."
    return _format(results)


async def news(topic: str) -> str:
    feeds = NEWS_FEEDS.get(re.sub(r"\s+news$", "", topic.lower().strip()))
    if feeds:  # "AI news", "tech news": the tech press, not a word search
        titles = await feed_headlines(feeds, MAX_RESULTS)
        if titles:
            return "\n".join(f"{i}. {t}" for i, t in enumerate(titles, 1))
    results = await _race(lambda b: DDGS(timeout=5).news(topic, max_results=MAX_RESULTS, backend=b) or [],
                          NEWS_BACKENDS)
    if not results:
        try:
            results = [{**r, "source": ""} for r in await _searxng(topic, categories="news")]
        except httpx.HTTPError:
            pass
    if not results:
        return f"I couldn't find any news about '{topic}'."
    lines = []
    for i, r in enumerate(results[:MAX_RESULTS], 1):
        when = (r.get("date") or "")[:10]
        source = re.sub(r"\s*·.*$", "", r.get("source") or "")
        meta = ", ".join(x for x in (source, when) if x)
        lines.append(f"{i}. {_clean(r.get('title', ''), 150)}" + (f" ({meta})" if meta else "")
                     + f"\n   {_clean(r.get('body', ''), 250)}")
    return "\n".join(lines)


async def weather(place: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=8, headers={"User-Agent": "curl/8"}) as client:
            r = await client.get(f"https://wttr.in/{place.strip() or ''}", params={"format": "j1"})
            r.raise_for_status()
            data = r.json()
    except (httpx.HTTPError, ValueError) as e:
        return f"I couldn't get the weather: {e}"
    now = data["current_condition"][0]
    area = data.get("nearest_area", [{}])[0]
    # wttr.in names the nearest weather station's neighbourhood; the city asked about reads better.
    country = (area.get("country") or [{}])[0].get("value", "")
    where = ", ".join(x for x in (place.strip().title(), country) if x)
    lines = [f"Weather in {where} right now: {now['weatherDesc'][0]['value'].strip()}, {now['temp_C']}°C "
             f"(feels like {now['FeelsLikeC']}°C), humidity {now['humidity']}%, wind {now['windspeedKmph']} km/h."]
    today = datetime.date.today()
    for day in data.get("weather", [])[:3]:
        rain = max(int(h.get("chanceofrain", 0)) for h in day["hourly"])
        desc = day["hourly"][len(day["hourly"]) // 2]["weatherDesc"][0]["value"]
        date = datetime.date.fromisoformat(day["date"])
        # Name the days: small models get "2026-10-25" wrong when working out which day is tomorrow.
        weekday = date.strftime("%A")
        name = {0: f"Today ({weekday})", 1: f"Tomorrow ({weekday})"}.get((date - today).days, weekday)
        lines.append(f"{name}: {desc}, {day['mintempC']} to {day['maxtempC']}°C, "
                     f"up to {rain}% chance of rain.")
    return "\n".join(lines)


async def locate() -> tuple[str, str]:
    """Roughly where the laptop is, from its internet address: (city, two-letter country code)."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get("https://ipinfo.io/json")
            r.raise_for_status()
            data = r.json()
    except (httpx.HTTPError, ValueError):
        return "", ""
    return data.get("city", ""), data.get("country", "")


async def headlines(topic: str = "", country: str = "", count: int = 3) -> list[str]:
    """Today's headlines, from Google News: the top stories in your country, or the latest on a topic."""
    country = country.upper() if re.fullmatch(r"[A-Za-z]{2}", country) else "US"
    locale = {"hl": f"en-{country}", "gl": country, "ceid": f"{country}:en"}
    section = NEWS_SECTIONS.get(topic.lower().strip())
    if section:
        url, params = f"https://news.google.com/rss/headlines/section/topic/{section}", locale
    elif topic:
        url, params = "https://news.google.com/rss/search", {"q": f"{topic} when:1d", **locale}
    else:
        url, params = "https://news.google.com/rss", locale
    try:
        async with httpx.AsyncClient(timeout=8, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
        items = ET.fromstring(r.content).findall("./channel/item")
    except (httpx.HTTPError, ET.ParseError) as e:
        log.warning("Couldn't get headlines: %s", e)
        return []
    titles = []
    for item in items:
        title = re.sub(r"[\u200b-\u200f\ufeff]", "", item.findtext("title") or "")  # invisible characters
        title = title.rsplit(" - ", 1)[0].strip()  # drop " - Al Jazeera"
        if re.search(r"[~^#@™\[\]{}|]{2,}|[\uff01-\uff5e]", title):  # spam ("~^%#@™[[PPTV Live]]")
            continue
        if title and title not in titles:
            titles.append(title)
    return titles[:count]


def _clean_title(title: str) -> str:
    title = re.sub(r"[\u200b-\u200f\ufeff]", "", title)  # invisible characters
    return re.sub(r"\s+", " ", title).strip()


def _when(text: str | None) -> datetime.datetime | None:
    """A feed's date, in either format feeds use (RFC 822 or ISO 8601)."""
    if not text:
        return None
    try:
        return email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError):
        pass
    try:
        return datetime.datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


async def _read_feed(client: httpx.AsyncClient, url: str) -> list[tuple[datetime.datetime, str]]:
    """(published, title) for each recent story in an RSS or Atom feed."""
    try:
        r = await client.get(url)
        r.raise_for_status()
        root = ET.fromstring(r.content)
    except (httpx.HTTPError, ET.ParseError) as e:
        log.warning("Couldn't read the feed %s: %s", url, e)
        return []
    oldest = datetime.datetime.now(datetime.UTC) - MAX_NEWS_AGE
    stories = []
    for item in root.findall("./channel/item") or root.findall(f"{ATOM}entry"):
        title = _clean_title(item.findtext("title") or item.findtext(f"{ATOM}title") or "")
        if "news.google.com" in url:
            title = title.rsplit(" - ", 1)[0]  # Google adds " - The Guardian"
        when = _when(item.findtext("pubDate") or item.findtext(f"{ATOM}published") or item.findtext(f"{ATOM}updated"))
        if title and when and when.tzinfo and when >= oldest:
            stories.append((when, title))
    return stories


def _same_story(a: str, b: str) -> bool:
    words_a, words_b = set(re.findall(r"\w+", a.lower())), set(re.findall(r"\w+", b.lower()))
    return len(words_a & words_b) / max(min(len(words_a), len(words_b)), 1) >= 0.6


async def feed_headlines(feeds: tuple[str, ...], count: int, skip: list[str] = ()) -> list[str]:
    """The newest headlines across some feeds, without repeats (or anything in `skip`)."""
    async with httpx.AsyncClient(timeout=8, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
        stories = sum(await asyncio.gather(*(_read_feed(client, url) for url in feeds)), [])
    titles: list[str] = []
    for _, title in sorted(stories, reverse=True):
        if not any(_same_story(title, t) for t in [*titles, *skip]):
            titles.append(title)
        if len(titles) == count:
            break
    return titles


async def read_page(url: str) -> str:
    if not re.match(r"^https?://", url):
        return "That isn't a web address."
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as c:
            r = await c.get(url)
            r.raise_for_status()
    except httpx.HTTPError as e:
        return f"I couldn't open that page: {e}"
    text = await asyncio.to_thread(trafilatura.extract, r.text, url=url, include_comments=False,
                                   include_tables=True)
    if not text:
        return "I opened the page but couldn't find any readable text on it."
    words = text.split()
    return " ".join(words[:MAX_PAGE_WORDS]) + (" …(rest of the page cut off)" if len(words) > MAX_PAGE_WORDS else "")

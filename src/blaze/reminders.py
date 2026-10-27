"""Turning "at 5:30 pm", "in 20 minutes" or "tomorrow morning" into a moment in time.

The chat model passes the time the way the user said it and this does the arithmetic: small
models are unreliable at working out what "in an hour and a half" is from the current time.
"""

import re
from datetime import datetime, timedelta

NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40,
    "forty-five": 45, "fifty": 50, "sixty": 60, "ninety": 90, "a couple of": 2, "a few": 3, "an": 1, "a": 1,
}
UNITS = {"s": 1, "sec": 1, "second": 1, "m": 60, "min": 60, "minute": 60, "h": 3600, "hr": 3600, "hour": 3600,
         "d": 86400, "day": 86400}
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december"]
# Parts of the day: when they start, and whether a bare hour then means afternoon/evening.
DAY_PARTS = {"morning": (9, False), "afternoon": (14, True), "evening": (18, True), "tonight": (20, True),
             "night": (21, True), "noon": (12, None), "midday": (12, None), "midnight": (0, None)}

_DURATION = re.compile(r"(\d+(?:\.\d+)?)\s*(sec|second|min|minute|hr|hour|day|s|m|h|d)s?\b")
_CLOCK = re.compile(r"\b(?:at\s+)?(\d{1,2})(?:[:. ](\d{2}))?\s*(am|pm)?\b")
_MONTH = "(" + "|".join(m[:3] + r"\w*" for m in MONTHS) + ")"
_DATE = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?{_MONTH}\b|\b{_MONTH}\s+(\d{{1,2}})(?:st|nd|rd|th)?\b")


def _normalize(text: str) -> str:
    text = " " + text.lower().replace("a.m.", "am").replace("p.m.", "pm") + " "
    text = re.sub(r"\bo'?clock\b", "", text)
    text = re.sub(r"\bhalf an hour\b", "30 minutes", text)
    text = re.sub(r"\ban? hour and a half\b", "90 minutes", text)
    text = re.sub(r"\ba quarter of an hour\b|\ba quarter hour\b", "15 minutes", text)
    for word, n in sorted(NUMBER_WORDS.items(), key=lambda kv: -len(kv[0])):
        # "a"/"an" only count as one before a unit ("an hour"), not in "a meeting".
        unit = r"(?=\s+(?:sec|second|min|minute|hour|hr|day))" if word in ("a", "an") else ""
        text = re.sub(rf"\b{word}\b{unit}", str(n), text)
    return re.sub(r"\s+", " ", text)


def _relative(text: str) -> timedelta | None:
    """'in 20 minutes', '1 hour 30 min', '2.5 hours' → a duration. Clock times aren't durations."""
    found = _DURATION.findall(re.sub(r"\d{1,2}[:.]\d{2}", "", text))
    if not found:
        return None
    return timedelta(seconds=sum(float(n) * UNITS[unit] for n, unit in found))


def _day(text: str, now: datetime) -> tuple[datetime | None, bool]:
    """The date the user named (None if they didn't), and whether it's an explicit day."""
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if "day after tomorrow" in text:
        return today + timedelta(days=2), True
    if "tomorrow" in text:
        return today + timedelta(days=1), True
    for i, name in enumerate(WEEKDAYS):
        if re.search(rf"\b{name}\b", text):
            ahead = (i - now.weekday()) % 7 or 7  # "on Monday" said on a Monday means next week
            return today + timedelta(days=ahead), True
    if m := _DATE.search(text):
        day = int(m[1] or m[4])
        month = next(i for i, name in enumerate(MONTHS, 1) if name.startswith((m[2] or m[3])[:3]))
        try:
            date = today.replace(month=month, day=day)
            if date < today:
                date = date.replace(year=date.year + 1)
        except ValueError:
            return None, False
        return date, True
    if "today" in text or "tonight" in text:
        return today, True
    return None, False


def parse_when(text: str, now: datetime | None = None) -> datetime | None:
    """When a reminder is due, from how the user said it. None if it can't be made out."""
    now = now or datetime.now()
    text = _normalize(text)
    # Remove dates first, so "30th of October" isn't read as half past something or 30 minutes.
    date, explicit_day = _day(text, now)
    clock_text = _DATE.sub(" ", text)

    part = next((p for p in DAY_PARTS if re.search(rf"\b{p}\b", clock_text)), None)
    m = None
    for candidate in _CLOCK.finditer(clock_text):
        rest = clock_text[candidate.end():]
        if re.match(r"\s*(sec|second|min|minute|hr|hour|day|s|m|h|d)s?\b", rest):
            continue  # "20 minutes" is a duration, not a clock time
        if candidate[2] or candidate[3] or "at " in candidate[0] or explicit_day or part \
                or re.search(r"\b(past|to)\s*$", clock_text[:candidate.start()]):
            m = candidate
            break

    if m is None and part is None and explicit_day:
        part = "morning"  # "on Friday" with no time: in the morning
    if m is None and part is None:
        delta = _relative(clock_text)
        if delta is None or delta.total_seconds() <= 0:
            return None
        return now + delta

    if m is not None:
        hour, minute, meridiem = int(m[1]), int(m[2] or 0), m[3]
        if hour > 23 or minute > 59:
            return None
        if re.search(r"\bhalf past\b", clock_text[:m.start() + 4]):
            minute = 30
        elif re.search(r"\bquarter past\b", clock_text[:m.start() + 4]):
            minute = 15
        elif re.search(r"\bquarter to\b", clock_text[:m.start() + 4]):
            hour, minute = hour - 1, 45
    else:
        hour, minute, meridiem = DAY_PARTS[part][0], 0, None

    afternoon = DAY_PARTS[part][1] if part else None
    if meridiem == "pm" and hour < 12:
        hour += 12
    elif meridiem == "am" and hour == 12:
        hour = 0
    elif meridiem is None and 1 <= hour <= 12 and m is not None:
        if afternoon and hour < 12:
            hour += 12
        elif afternoon is None and not explicit_day and hour < 12:
            # "at 5" today: whichever 5 o'clock comes next.
            first = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if first <= now:
                hour += 12
        elif afternoon is None and explicit_day and hour <= 6:
            hour += 12  # "tomorrow at 3" means the afternoon, not 3 at night

    when = (date or now).replace(hour=hour % 24, minute=minute, second=0, microsecond=0)
    if when <= now and not explicit_day:
        when += timedelta(days=1)
    return when if when > now else None


def describe(when: datetime, now: datetime | None = None) -> str:
    """'5:30 PM today', '9 AM tomorrow', '3 PM on Friday 31 October'."""
    now = now or datetime.now()
    clock = when.strftime("%-I:%M %p").replace(":00", "")
    days = (when.date() - now.date()).days
    if days == 0:
        return f"{clock} today"
    if days == 1:
        return f"{clock} tomorrow"
    if days < 7:
        return f"{clock} on {when.strftime('%A')}"
    return f"{clock} on {when.strftime('%A %-d %B')}"

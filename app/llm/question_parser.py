"""Pure parsing rules for the rule-based provider. No I/O, no clock, no randomness.

Months are always resolved against the data actually present (the last month with sales),
never against today's date, so the same question on the same data always means the same thing.

Recognised period forms:
  "2026-03"                 ISO month
  "March 2026", "Mar 2026"  month name or abbreviation with a year
  "Q1 2026"                 quarter
  "March", "Q2"             no year: takes the only year mentioned in the question, if there is
                            exactly one ("February and April 2026" -> both 2026); otherwise the
                            most recent occurrence at or before the data's last month
  "2026"                    a bare year means the whole year, but only when no month or quarter
                            is mentioned (otherwise it just supplies the year for them)
"May" is also a verb, so it only counts as a month when a year follows it or a preposition
(in, for, of, ...) comes before it.
"""

import re
from typing import NamedTuple

MONTH_NAMES = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)  # fmt: skip

_MONTH_WORDS = {name.lower(): number for number, name in enumerate(MONTH_NAMES, start=1)}
_MONTH_WORDS |= {name.lower()[:3]: number for number, name in enumerate(MONTH_NAMES, start=1)}
_MONTH_WORDS["sept"] = 9
_MAY_PRECEDERS = {"in", "for", "of", "and", "vs", "versus", "to", "from", "since", "during", "until", "through", "between"}

# Longest alternatives first, so "september" wins over "sep".
_MONTH_ALTERNATION = "|".join(sorted(_MONTH_WORDS, key=len, reverse=True))
# [0-9], not \d: \d also matches non-ASCII digits. Alternatives are tried in order at each
# position, so "March 2026" is one month mention, not a month plus a bare year.
_PERIOD_TOKEN = re.compile(
    r"(?P<iso>\b(?P<iso_year>[0-9]{4})-(?P<iso_month>0[1-9]|1[0-2])\b)"
    r"|(?P<quarter>\bq(?P<q>[1-4])\b(?:\s+(?P<q_year>[0-9]{4})\b)?)"
    rf"|(?P<month>\b(?P<m>{_MONTH_ALTERNATION})\b\.?(?:,?\s+(?P<m_year>[0-9]{{4}})\b)?)"
    r"|(?P<year>\b(?:19|20)[0-9]{2}\b)",
    re.IGNORECASE,
)

DEFAULT_TOP_N = 5
_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "twenty": 20,
}  # fmt: skip

# Checked in this order; the first match wins. Change beats top because "top drivers of the
# drop" is a change question; top beats compare because "top 5 in March vs April" wants
# products for both months, not two totals.
_INTENT_RULES = (
    ("change", re.compile(
        r"\b(why|drop(s|ped)?|declin(e|ed|es|ing)|fell|fall(s|ing)?|decreas(e|ed|es|ing)|"
        r"increas(e|ed|es|ing)|chang(e|ed|es)|grew|grow(th)?|r(i|o)se|jump(ed)?|drivers?)\b", re.IGNORECASE)),
    ("top", re.compile(r"\btop\b|\bbest[- ]?sell(ing|ers?)\b", re.IGNORECASE)),
    ("compare", re.compile(r"\b(compar(e|ed|ing|ison)|vs|versus)\b", re.IGNORECASE)),
    ("monthly", re.compile(
        r"\b(monthly|trends?|over time|by month|per month|each month|month[- ]by[- ]month)\b", re.IGNORECASE)),
    ("breakdown", re.compile(
        r"\b(by|per|across)\s+(regions?|products?|categor(y|ies))\b|\bbreak\s?down\b|\bbroken down\b|\bsplit\b",
        re.IGNORECASE)),
)  # fmt: skip
_DIMENSION = re.compile(r"\b(?:by|per|across|for each)\s+(region|product|categor(?:y|ies))s?\b", re.IGNORECASE)
_TOP_N = re.compile(r"\btop\s+([0-9]{1,3}|" + "|".join(_NUMBER_WORDS) + r")\b", re.IGNORECASE)


class MonthSpan(NamedTuple):
    """Inclusive range of YYYY-MM months. Tuples sort by (start, end), i.e. chronologically."""

    start: str
    end: str


def _month(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def _index(month: str) -> int:
    return int(month[:4]) * 12 + int(month[5:7]) - 1


def _from_index(index: int) -> str:
    year, month0 = divmod(index, 12)
    return _month(year, month0 + 1)


def previous_span(span: MonthSpan) -> MonthSpan:
    """The span of the same length right before this one: March -> February, Q2 -> Q1."""
    length = _index(span.end) - _index(span.start) + 1
    return MonthSpan(_from_index(_index(span.start) - length), _from_index(_index(span.start) - 1))


def _year_for(first_month: int, last_data_month: str) -> int:
    """Most recent year in which first_month falls at or before the data's last month."""
    data_year, data_month = int(last_data_month[:4]), int(last_data_month[5:7])
    return data_year if first_month <= data_month else data_year - 1


def _preceding_word(text: str, position: int) -> str:
    words = text[:position].split()
    return words[-1].strip(".,;:!?()").lower() if words else ""


def parse_periods(question: str, last_data_month: str) -> list[MonthSpan]:
    """Every period mentioned in the question, in order of appearance, without duplicates."""
    mentions: list[tuple[int, int, int | None]] = []  # (first month, length in months, year or None)
    years: list[int] = []  # every year written in the question
    bare_years: list[int] = []
    for match in _PERIOD_TOKEN.finditer(question):
        if match["iso"]:
            year = int(match["iso_year"])
            mentions.append((int(match["iso_month"]), 1, year))
            years.append(year)
        elif match["quarter"]:
            year = int(match["q_year"]) if match["q_year"] else None
            mentions.append((int(match["q"]) * 3 - 2, 3, year))
            years += [year] if year else []
        elif match["month"]:
            word = match["m"].lower()
            if word == "may" and not match["m_year"] and _preceding_word(question, match.start()) not in _MAY_PRECEDERS:
                continue  # "may" used as a verb
            year = int(match["m_year"]) if match["m_year"] else None
            mentions.append((_MONTH_WORDS[word], 1, year))
            years += [year] if year else []
        else:
            bare_years.append(int(match["year"]))
            years.append(int(match["year"]))

    if not mentions:
        spans = [MonthSpan(_month(year, 1), _month(year, 12)) for year in bare_years]
    else:
        only_year = years[0] if len(set(years)) == 1 else None
        spans = []
        for first_month, length, year in mentions:
            year = year or only_year or _year_for(first_month, last_data_month)
            spans.append(MonthSpan(_month(year, first_month), _month(year, first_month + length - 1)))
    return list(dict.fromkeys(spans))  # dedupe, keep order


def detect_intent(question: str) -> str | None:
    """"change", "top", "compare", "monthly", "breakdown", or None if no rule matches."""
    for intent, pattern in _INTENT_RULES:
        if pattern.search(question):
            return intent
    return None


def detect_dimension(question: str) -> str | None:
    """"region", "product" or "category" from phrases like "by region"; None if not stated."""
    match = _DIMENSION.search(question)
    if match is None:
        return None
    word = match[1].lower()
    return "category" if word.startswith("categor") else word


def parse_top_n(question: str) -> int:
    """N from "top N" / "top three"; DEFAULT_TOP_N when no number is given."""
    match = _TOP_N.search(question)
    if match is None:
        return DEFAULT_TOP_N
    word = match[1].lower()
    return _NUMBER_WORDS[word] if word in _NUMBER_WORDS else int(word)


def find_names(question: str, names: list[str]) -> list[str]:
    """Known names mentioned in the question (case-insensitive, whole words), in order of appearance."""
    found = []
    for name in names:
        match = re.search(rf"(?<!\w){re.escape(name)}(?!\w)", question, re.IGNORECASE)
        if match:
            found.append((match.start(), name))
    return [name for _, name in sorted(found)]


# --- scope, clarification and premise rules --------------------------------------------
#
# Clarify vs out of scope: if a different period or name would make the question answerable,
# ask (Clarify); if the data can never answer it, refuse politely (out of scope).

_INSTRUCTION_LIKE = re.compile(
    r"\bignore\b.{0,40}\b(instructions?|prompts?|rules)\b|\bsystem prompt\b"
    # Statement shapes, not single words: "why did revenue drop from May" must not match.
    r"|\b(drop|truncate|alter|create)\s+(table|database|index|view)\b|\bdelete\s+from\b|\binsert\s+into\b"
    r"|\bupdate\s+\w+\s+set\b"
    r"|\bselect\b.*\bfrom\s+(sales|products|regions|sqlite_\w+)\b|\bunion\s+select\b|;\s*--|\bpragma\b",
    re.IGNORECASE,
)
_FORECAST = re.compile(
    r"\b(predict(s|ed|ion|ions)?|forecast(s|ed|ing)?|projection(s)?|projected|will|going to)\b"
    r"|\bnext\s+(month|quarter|year|week)\b",
    re.IGNORECASE,
)
# Metrics the data does not have, mapped to how an answer should name them.
_UNSUPPORTED_METRICS = (
    (re.compile(r"\bprofit(s|ability|able)?\b", re.IGNORECASE), "Profit"),
    (re.compile(r"\bmargins?\b", re.IGNORECASE), "Margin"),
    (re.compile(r"\b(costs?|cogs|expenses?|spend(ing)?)\b", re.IGNORECASE), "Cost data"),
    (re.compile(r"\b(customers?|clients?|buyers?)\b", re.IGNORECASE), "Customer data"),
    (re.compile(r"\b(employees?|staff|headcount|salar(y|ies))\b", re.IGNORECASE), "Employee data"),
    (re.compile(r"\b(inventory|stock)\b", re.IGNORECASE), "Inventory data"),
    (re.compile(r"\b(returns|refunds?|churn)\b", re.IGNORECASE), "Returns data"),
)
_SUPPORTED_METRIC = re.compile(r"\b(revenue|sales|sold|sell(s|ing)?|units?|orders?)\b", re.IGNORECASE)
_RELATIVE_TIME = re.compile(
    r"\b(last|this|past|current)\s+(month|quarter|year|week)s?\b"
    r"|\b(recent|recently|lately|ytd|year[- ]to[- ]date|yesterday|today)\b",
    re.IGNORECASE,
)
_DOWN_WORDS = re.compile(
    r"\b(drop(s|ped)?|declin(e|ed|es|ing)|fell|fall(s|ing)?|decreas(e|ed|es|ing)|dip(s|ped)?|"
    r"(went|go|gone|going) down)\b",
    re.IGNORECASE,
)
_UP_WORDS = re.compile(
    r"\b(increas(e|ed|es|ing)|grew|grow(s|ing|th)?|r(i|o)se|risen|jump(s|ed)?|(went|go|gone|going) up)\b",
    re.IGNORECASE,
)
_DIMENSION_WORDS = {"region", "regions", "product", "products", "category", "categories"}
# Words that may follow "in/for/from" without being a name ("in the West", "in total", "in revenue").
_FOLLOWER_STOPWORDS = {
    "the", "our", "my", "a", "an", "all", "each", "every", "total", "this", "that", "both",
    "terms", "general", "particular", "comparison", "contrast", "detail", "short", "which", "what",
    "revenue", "sales", "units", "orders", "last", "past", "current", "recent", "recently",
    # "in total for 2026": after skipping "total", the next word can be another preposition.
    "in", "for", "from", "to", "of", "and", "or", "vs", "versus", "with",
}  # fmt: skip
_PREPOSITION_FOLLOWER = re.compile(
    r"\b(?:in|for|from)\s+(?:(?:the|our|my|a|an|all|each|every|total|this|that)\s+)?([^\W_][\w&'-]*)",
    re.IGNORECASE,
)
_WORD = re.compile(r"[^\W\d_][\w&'-]*")


def detect_out_of_scope(question: str) -> str | None:
    """Questions the data can NEVER answer, decided from the text alone (no data needed).

    Returns "instructions", "forecast" or "metric", or None. "metric" only when an unsupported
    metric is asked for and no supported one: "revenue and profit" is a Clarify, not a refusal.
    """
    if _INSTRUCTION_LIKE.search(question):
        return "instructions"
    if _FORECAST.search(question):
        return "forecast"
    if find_unsupported_metric(question) and not mentions_supported_metric(question):
        return "metric"
    return None


def find_unsupported_metric(question: str) -> str | None:
    """How to name the first unsupported metric mentioned ("Profit", "Customer data"), or None."""
    for pattern, noun in _UNSUPPORTED_METRICS:
        if pattern.search(question):
            return noun
    return None


def mentions_supported_metric(question: str) -> bool:
    return _SUPPORTED_METRIC.search(question) is not None


def has_relative_time(question: str) -> bool:
    """"last month", "this year", "recently", ...: these depend on today's date, so we ask instead."""
    return _RELATIVE_TIME.search(question) is not None


def claimed_direction(question: str) -> str | None:
    """"down" or "up" if the question presumes a direction ("why did revenue drop"), else None."""
    down, up = _DOWN_WORDS.search(question), _UP_WORDS.search(question)
    if down and not up:
        return "down"
    if up and not down:
        return "up"
    return None


def _is_known_word(word: str, rest: str, known_names: list[str]) -> bool:
    """Could this word (followed by `rest` of the question) be something other than an unknown name?"""
    lower = word.lower()
    if lower in _MONTH_WORDS or re.fullmatch(r"q[1-4]", lower) or word[0].isdigit():
        return True
    if lower in _FOLLOWER_STOPWORDS or lower in _DIMENSION_WORDS:
        return True
    # A known name starts here ("Phone Max"), or the word is part of one ("StudioBook").
    rest_lower = rest.lower()
    for name in known_names:
        if rest_lower.startswith(name.lower()) or lower in name.lower().split():
            return True
    return False


def find_unknown_names(question: str, known_names: list[str]) -> list[str]:
    """Words that look like a product/region name but are not in the data, in order of appearance.

    Two heuristics, documented because they can miss things:
      1. The word after "in", "for" or "from" (skipping one article like "the"), any case:
         "revenue in europe" -> "europe". It is fine if it is a month, quarter, year, known
         name, dimension word, relative-time word or one of a few stopwords.
      2. A word containing an uppercase letter that is not the first word (unless it has an
         uppercase letter inside it, like "iPhone") and is not a month, quarter, "I", part of
         a known name or a word the rules already understand ("Top", "Revenue", "Compare").
    A lowercase unknown name elsewhere ("did europe do well") is not caught.
    """
    found: dict[str, int] = {}
    for match in _PREPOSITION_FOLLOWER.finditer(question):
        word = match[1]
        if not _is_known_word(word, question[match.start(1):], known_names):
            found.setdefault(word, match.start(1))
    for index, match in enumerate(_WORD.finditer(question)):
        word = match[0].split("'")[0]  # "West's" -> "West", "I'm" -> "I"
        has_upper = any(c.isupper() for c in (word if index else word[1:]))
        if not has_upper or word in ("I", "GroundedBI"):
            continue
        if _is_known_word(word, question[match.start():], known_names):
            continue
        if detect_intent(word) or mentions_supported_metric(word) or find_unsupported_metric(word):
            continue  # a word the rules understand, just capitalized
        found.setdefault(word, match.start())
    return sorted(found, key=found.__getitem__)


def has_analytics_vocabulary(question: str, known_names: list[str]) -> bool:
    """Does the question talk about anything this dataset covers at all?"""
    return bool(
        mentions_supported_metric(question)
        or find_unsupported_metric(question)
        or detect_intent(question)
        or has_relative_time(question)
        or _PERIOD_TOKEN.search(question)
        or any(word.lower() in _DIMENSION_WORDS for word in _WORD.findall(question))
        or find_names(question, known_names)
    )

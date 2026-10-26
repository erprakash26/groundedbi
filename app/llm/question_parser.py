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
    # "first quarter", "2nd quarter of 2026": the same as Q1..Q4.
    r"|(?P<ordinal_quarter>\b(?P<ordinal>first|second|third|fourth|1st|2nd|3rd|4th)\s+quarter\b"
    r"(?:\s+(?:of\s+)?(?P<oq_year>[0-9]{4})\b)?)"
    rf"|(?P<month>\b(?P<m>{_MONTH_ALTERNATION})\b\.?(?:,?\s+(?P<m_year>[0-9]{{4}})\b)?)"
    r"|(?P<year>\b(?:19|20)[0-9]{2}\b)",
    re.IGNORECASE,
)

_ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "1st": 1, "2nd": 2, "3rd": 3, "4th": 4}
DEFAULT_TOP_N = 5
_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "twenty": 20,
}  # fmt: skip

# Checked in this order; the first match wins. Change beats top because "what drove most of
# the drop" is a change question; top beats compare because "top 5 in March vs April" wants
# products for both months, not two totals.
_INTENT_RULES = (
    ("change", re.compile(
        r"\b(why|drop(s|ped)?|declin(e|ed|es|ing)|fell|fall(s|ing)?|decreas(e|ed|es|ing)|"
        r"increas(e|ed|es|ing)|chang(e|ed|es)|grew|grow(th)?|r(i|o)se|jump(ed)?|drivers?)\b"
        r"|\bwhat\s+happened\b"
        # "how did revenue go/move/change"; "how did X do" stays a comparison of performance.
        r"|\bhow\s+did\b[^?.!]{0,60}?\b(go|move|change)\b", re.IGNORECASE)),
    ("top", re.compile(
        r"\btop\b|\bbest\b|\bbest[- ]?sell(ing|ers?)\b|\bhighest\b|\bleading\b|\bmost\b", re.IGNORECASE)),
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
        elif match["quarter"] or match["ordinal_quarter"]:
            number = int(match["q"]) if match["quarter"] else _ORDINALS[match["ordinal"].lower()]
            raw_year = match["q_year"] if match["quarter"] else match["oq_year"]
            year = int(raw_year) if raw_year else None
            mentions.append((number * 3 - 2, 3, year))
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
# A command to change data, with or without SQL syntax: "Remove the February sales records".
# Only as the first word, and not as a noun: "Drop in revenue during March" is a question.
_DATA_CHANGE_COMMAND = re.compile(
    r"^\s*(delete|drop|update|insert|remove|truncate|alter)\b(?!\s+(in|of|on|off)\b)", re.IGNORECASE
)
# Creative writing is never a data question, even when it mentions a metric or a period.
# "summary", "overview" and "report" are deliberately NOT here: those are requests for data.
_CREATIVE = re.compile(r"\b(poems?|songs?|stor(y|ies)|jokes?|haikus?|essays?|limericks?|lyrics|rap|sonnets?)\b", re.IGNORECASE)
# Metrics the data does not have, mapped to how an answer should name them. Checked in order,
# so "average order value" is named before the plain "order" rule sees it.
_UNSUPPORTED_METRICS = (
    (re.compile(r"\bprofit(s|ability|able)?\b", re.IGNORECASE), "Profit"),
    (re.compile(r"\bmargins?\b", re.IGNORECASE), "Margin"),
    (re.compile(r"\b(average\s+order\s+value|aov)\b", re.IGNORECASE), "Average order value"),
    (re.compile(r"\bbasket\s+sizes?\b", re.IGNORECASE), "Basket size"),
    # The data counts sales rows, but answers never report orders, so they are not offered.
    (re.compile(r"\borders?\b", re.IGNORECASE), "Order data"),
    (re.compile(r"\b(costs?|cogs|expenses?|spend(ing)?)\b", re.IGNORECASE), "Cost data"),
    (re.compile(r"\b(customers?|clients?|buyers?)\b", re.IGNORECASE), "Customer data"),
    (re.compile(r"\b(employees?|staff|headcount|salar(y|ies))\b", re.IGNORECASE), "Employee data"),
    (re.compile(r"\b(inventory|stock)\b", re.IGNORECASE), "Inventory data"),
    (re.compile(r"\b(returns|refunds?|churn)\b", re.IGNORECASE), "Returns data"),
)
_SUPPORTED_METRIC = re.compile(r"\b(revenue|sales|sold|sell(s|ing)?|units?)\b", re.IGNORECASE)
# How the business is doing, with no analytics intent yet: ask what they want, don't refuse.
_BUSINESS_PERFORMANCE = re.compile(
    r"\b(business|performance|perform(s|ed|ing)?|doing|results?)\b|\bgoing\s+(well|badly|ok(ay)?)\b", re.IGNORECASE
)
# Rankings from the bottom: no tool returns them, so ask instead of answering something else.
_BOTTOM_RANKING = re.compile(r"\b(worst|lowest|bottom|least)\b", re.IGNORECASE)
# "Which product ..." / "What product ..." (singular) asks for exactly one.
_SINGLE_PRODUCT = re.compile(r"\b(which|what)\s+(?:[\w-]+\s+)?product\b(?!s)", re.IGNORECASE)
_RANKED_DIMENSION = re.compile(r"\b(regions?|categor(?:y|ies))\b", re.IGNORECASE)
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
_METRIC_WORDS = {"revenue", "sales", "sold", "sell", "sells", "selling", "units", "unit"}
# Common English words that can follow "in/for/from/during", or stand before "region/product",
# without being a name: articles and determiners, prepositions, pronouns, and frequent verbs,
# adverbs and adjectives. Kept small on purpose; tests pin it down.
COMMON_WORDS = {
    # articles, determiners, pronouns
    "the", "a", "an", "this", "that", "these", "those", "each", "every", "any", "all", "both",
    "which", "what", "whose", "our", "my", "your", "their", "its", "us", "we", "me", "it", "them",
    # prepositions and conjunctions
    "in", "for", "from", "during", "to", "of", "on", "at", "with", "between", "than", "by", "per",
    "and", "or", "vs", "versus",
    # frequent verbs
    "be", "is", "was", "are", "were", "do", "did", "does", "have", "had", "has", "get", "got",
    "go", "went", "come", "came", "bring", "brought", "make", "made", "see", "show", "earn",
    "earned", "generate", "generated", "sell", "sold",
    # adverbs, fillers and adjectives that often come before "product"
    "total", "overall", "general", "particular", "detail", "short", "terms", "comparison",
    "contrast", "now", "then", "there", "here", "popular", "new", "top", "best", "leading",
    "highest", "most", "single", "individual", "specific", "other", "same", "one",
    # relative-time words (handled by their own Clarify rule)
    "last", "past", "current", "recent", "recently",
    # parts of an ordinal quarter ("during the 3rd quarter"), which is parsed as a period
    "first", "second", "third", "fourth", "quarter", "quarters",
}  # fmt: skip
# Words that carry no meaning of their own in an amount question ("How much revenue came in
# during May 2026?"). The total intent is only used when every other word is understood.
_FILLER_WORDS = COMMON_WORDS | {
    "how", "much", "many", "please", "tell", "give", "show", "list", "summary", "summarize",
    "summarise", "overview", "recap", "report", "write", "s", "region", "regions",
}  # fmt: skip
_NAME_FOLLOWING = re.compile(
    r"\b(?:in|for|from|during)\s+(?:(?:the|our|my|a|an|all|each|every|total|this|that)\s+)?([^\W_][\w&'-]*)",
    re.IGNORECASE,
)
_NAME_BEFORE_DIMENSION = re.compile(r"\b([^\W\d_][\w&'-]*)\s+(?:region|product|category)s?\b", re.IGNORECASE)
_WORD = re.compile(r"[^\W\d_][\w&'-]*")
_TOKEN = re.compile(r"[^\W_][\w&'-]*")


def detect_out_of_scope(question: str) -> str | None:
    """Questions the data can NEVER answer, decided from the text alone (no data needed).

    Returns "data_change", "instructions", "creative", "forecast" or "metric", or None.
    "metric" only when an unsupported metric is asked for and no supported one: "revenue and
    profit" is a Clarify. A data-changing command is checked before every other rule.
    """
    if _DATA_CHANGE_COMMAND.search(question):
        return "data_change"
    if _INSTRUCTION_LIKE.search(question):
        return "instructions"
    if _CREATIVE.search(question):
        return "creative"
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


def asks_bottom_ranking(question: str) -> bool:
    """"worst", "lowest", "bottom", "least": no tool ranks from the bottom."""
    return _BOTTOM_RANKING.search(question) is not None


def asks_single_product(question: str) -> bool:
    """"Which product ..." (singular) means one product: limit 1."""
    return _SINGLE_PRODUCT.search(question) is not None


def ranked_dimension(question: str) -> str | None:
    """"region" or "category" if a ranking question ranks those instead of products."""
    match = _RANKED_DIMENSION.search(question)
    if match is None:
        return None
    return "category" if match[1].lower().startswith("categor") else "region"


def claimed_direction(question: str) -> str | None:
    """"down" or "up" if the question presumes a direction ("why did revenue drop"), else None."""
    down, up = _DOWN_WORDS.search(question), _UP_WORDS.search(question)
    if down and not up:
        return "down"
    if up and not down:
        return "up"
    return None


def _name_spans(question: str, known_names: list[str]) -> list[tuple[int, int]]:
    """Where known names occur as whole words: "Northeast" does not contain "North"."""
    spans = []
    for name in known_names:
        for match in re.finditer(rf"(?<!\w){re.escape(name)}(?!\w)", question, re.IGNORECASE):
            spans.append(match.span())
    return spans


def _covered(position: int, spans: list[tuple[int, int]]) -> bool:
    return any(start <= position < end for start, end in spans)


def _is_understood_word(word: str) -> bool:
    """Words that can never be a name: months, quarters, numbers, common words, rule vocabulary."""
    lower = word.lower()
    return bool(
        lower in _MONTH_WORDS
        or re.fullmatch(r"q[1-4]", lower)
        or word[0].isdigit()
        or lower in COMMON_WORDS
        or lower in _DIMENSION_WORDS
        or lower in _METRIC_WORDS
    )


def _candidate(question: str, start: int, word: str, known_names: list[str], spans: list[tuple[int, int]]) -> str | None:
    """The unknown name starting at `start`, or None if the word is understood or a known name.

    A word that belongs to a known multi-word name without the rest of it ("StudioBook 160")
    is returned together with the number after it, so the Clarify names exactly what was typed.
    """
    if _covered(start, spans) or _is_understood_word(word):
        return None
    if detect_intent(word) or asks_bottom_ranking(word):
        return None  # rule vocabulary such as "worst-selling" or "best", not a name
    name_tokens = {token.lower() for name in known_names for token in name.split()}
    if word.lower() in name_tokens:
        after = re.match(r"\s+([0-9]+)\b", question[start + len(word):])
        return f"{word}{after.group(0)}" if after else word
    return word


def find_unknown_names(question: str, known_names: list[str]) -> list[str]:
    """Words that look like a product/region name but are not in the data, in order of appearance.

    Heuristics, documented because they can miss things:
      1. The word after "in", "for", "from" or "during" (skipping one article like "the"), any
         case: "revenue in europe" -> "europe", unless it is a common English word (COMMON_WORDS),
         a month, quarter, year, dimension word or metric word.
      2. The word directly before "region", "product" or "category": "the Pacific region".
      3. A word containing an uppercase letter that is not the first word (unless it has an
         uppercase letter inside it, like "iPhone"), and is not a word the rules understand.
    Known names only match as whole words, so "Northeast" is unknown even though "North" exists.
    A lowercase unknown name elsewhere ("did europe do well") is not caught.
    """
    spans = _name_spans(question, known_names)
    found: dict[str, int] = {}
    for pattern in (_NAME_FOLLOWING, _NAME_BEFORE_DIMENSION):
        for match in pattern.finditer(question):
            name = _candidate(question, match.start(1), match[1], known_names, spans)
            if name:
                found.setdefault(name, match.start(1))
    for index, match in enumerate(_WORD.finditer(question)):
        word = match[0].split("'")[0]  # "West's" -> "West", "I'm" -> "I"
        has_upper = any(c.isupper() for c in (word if index else word[1:]))
        if not has_upper or word in ("I", "GroundedBI"):
            continue
        if detect_intent(word) or mentions_supported_metric(word) or find_unsupported_metric(word):
            continue  # a word the rules understand, just capitalized
        name = _candidate(question, match.start(), word, known_names, spans)
        if name and not any(name in other or other in name for other in found):
            found.setdefault(name, match.start())
    return sorted(found, key=found.__getitem__)


def unexplained_words(question: str, known_names: list[str]) -> list[str]:
    """Words left after removing periods, known names, metric words and filler words.

    The total intent ("Revenue in April 2026", "How much revenue came in during May 2026?") is
    only used when this is empty: every content word was understood, so nothing is guessed.
    """
    text = _PERIOD_TOKEN.sub(" ", question)
    for start, end in sorted(_name_spans(text, known_names), reverse=True):
        text = text[:start] + " " + text[end:]
    words = [w.lower().split("'")[0] for w in _TOKEN.findall(text)]
    return [w for w in words if w and not w[0].isdigit() and w not in _FILLER_WORDS and w not in _METRIC_WORDS]


def has_analytics_vocabulary(question: str, known_names: list[str]) -> bool:
    """Does the question talk about anything this dataset covers, or about how business is going?"""
    return bool(
        mentions_supported_metric(question)
        or find_unsupported_metric(question)
        or detect_intent(question)
        or has_relative_time(question)
        or _BUSINESS_PERFORMANCE.search(question)
        or _PERIOD_TOKEN.search(question)
        or any(word.lower() in _DIMENSION_WORDS for word in _WORD.findall(question))
        or find_names(question, known_names)
    )

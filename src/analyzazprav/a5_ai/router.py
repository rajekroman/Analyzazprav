from __future__ import annotations

from enum import Enum


class QueryRoute(str, Enum):
    DATA = "data"
    COMBINED = "combined"
    INTERPRETIVE = "interpretive"


_DATA_MARKERS = ("kolik", "kdo více", "kdo vic", "počet", "pocet", "průměr", "prumer", "median", "latency", "iniciace", "jak často", "jak casto")
_INTERPRETIVE_MARKERS = ("proč", "proc", "co znamená", "co znamena", "vzorec", "dynamika", "konflikt", "přibliž", "pribliz", "odtah")
_CHANGE_MARKERS = ("kdy se změn", "kdy se zmen", "změnila komunikace", "zmenila komunikace", "turning point")


def route_question(question: str) -> QueryRoute:
    normalized = " ".join(question.casefold().split())
    if any(marker in normalized for marker in _CHANGE_MARKERS):
        return QueryRoute.COMBINED
    has_data = any(marker in normalized for marker in _DATA_MARKERS)
    has_interpretive = any(marker in normalized for marker in _INTERPRETIVE_MARKERS)
    if has_data and has_interpretive:
        return QueryRoute.COMBINED
    if has_data:
        return QueryRoute.DATA
    return QueryRoute.INTERPRETIVE

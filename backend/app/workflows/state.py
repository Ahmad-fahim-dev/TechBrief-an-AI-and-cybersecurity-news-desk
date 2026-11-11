import operator
from typing import Annotated, TypedDict


class ResearchState(TypedDict, total=False):
    categories: set[str]
    raw_articles: Annotated[list[dict], operator.add]
    articles: list[dict]
    warnings: Annotated[list[str], operator.add]
    report: dict

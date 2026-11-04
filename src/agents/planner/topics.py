"""Topic vocabulary and keyword routing used by the Planner (client-independent, deterministic)."""
from __future__ import annotations

TOPICS = ["retirement_age", "retirement_income", "asset_allocation", "risk_tolerance", "liquidity_reserve", "education_funding",
          "housing_property", "debt_mortgage", "business_sale", "concentrated_position", "investment_trade", "tax_planning",
          "insurance_estate", "family_support", "care_costs", "income_cashflow", "other"]

# keyword lexicon: maps free text to topics
TOPIC_KEYWORDS: dict[str, list[str]] = {
    "retirement_age": ["retire", "retirement age", "retiring", "retirement date", "work until", "stop working"],
    "retirement_income": ["retirement income", "a month after tax", "drawdown", "withdraw", "social security", "pension", "required distribution", "deferred comp"],
    "asset_allocation": ["equity", "equities", "stocks", "allocation", "percent in", "bonds", "rebalanc", "portfolio mix", "risk level"],
    "risk_tolerance": ["risk", "volatility", "comfortable", "sleep", "drawdown", "tolerate", "anxious", "aggressive", "conservative", "capital preserv"],
    "liquidity_reserve": ["liquidity", "cash reserve", "emergency", "months of", "reserve", "cushion", "cash"],
    "education_funding": ["tuition", "college", "graduate school", "529", "education", "school"],
    "housing_property": ["house", "home purchase", "down payment", "downsiz", "maplewood", "entrance fee", "property"],
    "debt_mortgage": ["mortgage", "loan", "student loan", "pay off", "paid off", "interest rate"],
    "business_sale": ["sell the company", "selling the company", "whether to sell", "holloway", "sell to", "the company", "lakeshore", "sale proceeds", "offer", "employee ownership", "seller note", "earn-out", "rollover", "secondary", "tender", "founders' shares"],
    "concentrated_position": ["vantora", "northgate", "single company", "concentration", "vested shares", "company shares", "employer"],
    "investment_trade": ["sell", "buy", "purchase", "trim", "crypto", "fund", "step into", "bought"],
    "tax_planning": ["tax", "estimated", "withholding", "safe harbor", "safe-harbour", "qualified charitable"],
    "insurance_estate": ["insurance", "beneficiar", "estate", "annuity", "survivor", "trust", "disability"],
    "family_support": ["lend", "gift", "son", "daughter", "brian", "tobi", "folake", "yuki", "colleen", "help"],
    "care_costs": ["care reserve", "mother", "aide", "care costs", "memory care", "nursing", "parkinson", "care reserve", "frank", "dorothy"],
    "income_cashflow": ["spending", "income", "salary", "cash flow", "expenses", "parental leave", "bonus", "revenue"],
}


def _norm_text(text: str) -> str:
    return text.lower().replace("-", " ").replace("’", "'")


def _score(text: str, kws: list[str]) -> int:
    """Phrase hits weigh double so 'employee ownership' beats a stray 'cash'."""
    t = _norm_text(text)
    return sum((2 if " " in k else 1) for k in kws if k in t)


def topic_hits(text: str) -> list[str]:
    return [k for k, kws in TOPIC_KEYWORDS.items() if _score(text, kws)]


def infer_topics(text: str, limit: int = 4) -> list[str]:
    order = list(TOPIC_KEYWORDS)
    scored = sorted(((_score(text, kws), k) for k, kws in TOPIC_KEYWORDS.items()), key=lambda x: (-x[0], order.index(x[1])))
    return [k for n, k in scored if n > 0][:limit]

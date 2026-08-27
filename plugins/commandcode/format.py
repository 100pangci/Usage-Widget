"""金额与数量格式化：美元、token 自动换算（万/亿）、倒计时、计划名。插件内自包含。"""

PLAN_NAMES = {
    "individual-go": "Go",
    "individual-goat": "GOAT",
    "individual-pro": "Pro",
    "individual-pro-v1": "Pro",
    "individual-provider": "Provider",
    "individual-max": "Max 10×",
    "individual-ultra": "Max 20×",
}

PLAN_CREDITS = {
    "individual-go": 10,
    "individual-goat": 70,
    "individual-pro": 30,
    "individual-pro-v1": 80,
    "individual-provider": 15,
    "individual-max": 150,
    "individual-ultra": 300,
}


def format_usd(usd: float) -> str:
    """美元金额：<1 显示小数，<10000 两位小数，更大缩写 k。"""
    if usd >= 10000:
        return f"${usd / 1000:.1f}k"
    if usd < 1:
        return f"${usd:.2f}"
    return f"${usd:,.2f}"


def format_credits(credits: float) -> str:
    """剩余 credits：整数不带小数，小数保留 1 位。"""
    if credits >= 100:
        return f"{credits:.0f}"
    return f"{credits:.1f}"


def format_tokens(tokens: int) -> str:
    """自动换算单位：<1万 原样；<1亿 万；否则 亿。"""
    if tokens >= 1e8:
        return f"{tokens / 1e8:.2f}亿"
    if tokens >= 1e4:
        return f"{tokens / 1e4:.1f}万"
    return f"{tokens:,}"


def format_reset_time(seconds: int) -> str:
    """重置倒计时：X天X小时 / X小时X分钟 / X分钟 / 几秒（与网页一致）。"""
    days = seconds // 86400
    hours = seconds % 86400 // 3600
    minutes = seconds % 3600 // 60
    if days >= 1:
        return f"{days}天{hours}小时"
    if hours >= 1:
        return f"{hours}小时{minutes}分"
    if minutes == 0:
        return "几秒"
    return f"{minutes}分"


def format_plan_name(plan_id: str) -> str:
    return PLAN_NAMES.get(plan_id, plan_id)


def plan_total_credits(plan_id: str) -> int | None:
    """计划每月 credits 总额（前端订阅页 limits.totalCredits），未知计划返回 None。"""
    return PLAN_CREDITS.get(plan_id)
"""金额与数量格式化：美元、token 自动换算（万/亿）。插件内自包含。"""


def format_usd(cost_units: float | int) -> str:
    """cost_units / 1e8 为美元（opencode.ai 的计费单位）。"""
    usd = cost_units / 1e8
    if usd >= 10000:
        return f"${usd / 1000:.1f}k"
    return f"${usd:,.2f}"


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

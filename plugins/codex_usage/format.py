"""Codex 用量插件的格式化工具。"""


PLAN_NAMES = {
    "free": "Free",
    "plus": "Plus",
    "pro": "Pro",
    "team": "Team",
    "business": "Business",
    "enterprise": "Enterprise",
}


def format_plan_name(plan_type: str) -> str:
    """格式化订阅计划名；未知计划保留服务端原值。"""
    value = str(plan_type or "").strip()
    if not value:
        return "--"
    return PLAN_NAMES.get(value.lower(), value)


def format_reset_time(seconds: int) -> str:
    """格式化剩余时间：X天X小时 / X小时X分 / X分 / 几秒。"""
    seconds = max(0, int(seconds))
    days = seconds // 86400
    hours = seconds % 86400 // 3600
    minutes = seconds % 3600 // 60
    if days >= 1:
        return f"{days}天{hours}小时"
    if hours >= 1:
        return f"{hours}小时{minutes}分"
    if minutes >= 1:
        return f"{minutes}分"
    return "几秒"

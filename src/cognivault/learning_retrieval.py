"""Small transparent bilingual fallback for focused high-school queries.

This is lexical query expansion, not semantic ranking. Original query goes
first; at most two aliases are tried only after an empty result.
"""
ALIASES = {
    "物理": ("physics",), "目标": ("goal",), "近期学习": ("recent study", "学习"),
    "摩擦力": ("friction", "摩擦"), "摩擦": ("friction",),
    "受力分析": ("force diagram", "受力"), "牛顿第二定律": ("Newton", "F=ma"),
    "加速度": ("acceleration",), "选科": ("chose", "选择"),
}


def focused(query, function, result_key):
    result = function(query)
    if result[result_key]: return result
    for alias in ALIASES.get(query.strip(), ())[:2]:
        result = function(alias)
        if result[result_key]: return result
    return result

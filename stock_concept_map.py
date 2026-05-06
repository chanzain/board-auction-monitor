"""
根据板块→成分股映射，反查每只股票所属的全部板块/概念名称（与本地 JSON 一致，多为东财概念/行业名）。
"""

from __future__ import annotations

from typing import Dict, List


def build_stock_concept_index(board_map: Dict[str, List[str]]) -> Dict[str, List[str]]:
    """ts_code -> 去重排序后的概念/板块名称列表"""
    rev: Dict[str, List[str]] = {}
    for board_name, codes in board_map.items():
        for c in codes:
            rev.setdefault(c, []).append(board_name)
    return {k: sorted(set(v)) for k, v in rev.items()}


def format_concept_preview(names: List[str], max_chars: int = 100) -> str:
    """列表列展示用：顿号连接，超长截断"""
    if not names:
        return "暂无（本地映射中未包含该股所属概念）"
    s = "、".join(names)
    if len(s) <= max_chars:
        return s
    out: List[str] = []
    n = 0
    for part in names:
        add = part if not out else "、" + part
        if n + len(add) > max_chars - 1:
            break
        out.append(part)
        n += len(add)
    return "、".join(out) + "…"

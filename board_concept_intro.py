"""
单个概念/板块名称在同花顺「概念板块」详情页中的简介文案，带进程内短缓存。

用途：
- 成分股弹窗「详情」中，按股票列举其所属各概念并附题材说明（每个概念调用一次，结果按概念名缓存）。
- 东财/本地映射里的板块名与同花顺概念名可能不一致，可在 data/board_concept_ths_alias.json 中配置：
  {"锂矿概念": "盐湖提锂"}
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

DATA_DIR = Path(__file__).parent / "data"
_ALIAS_FILE = DATA_DIR / "board_concept_ths_alias.json"

_CACHE: dict[str, tuple[str, float]] = {}
_TTL_SEC = 3600
_ALIAS_MAP: Optional[Dict[str, str]] = None


def _load_alias_map() -> Dict[str, str]:
    global _ALIAS_MAP
    if _ALIAS_MAP is not None:
        return _ALIAS_MAP
    _ALIAS_MAP = {}
    if _ALIAS_FILE.exists():
        try:
            raw = json.loads(_ALIAS_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                _ALIAS_MAP = {str(k): str(v) for k, v in raw.items()}
        except Exception:
            _ALIAS_MAP = {}
    return _ALIAS_MAP


def _ths_try_symbols(board_name: str) -> List[str]:
    aliases = _load_alias_map()
    out: List[str] = []
    if board_name in aliases:
        out.append(aliases[board_name])
    if board_name not in out:
        out.append(board_name)
    return out


def _parse_ths_info_df(df) -> str:
    if df is None or df.empty:
        return ""
    for _, row in df.iterrows():
        k = str(row.get("项目", "")).strip()
        v = str(row.get("值", "")).strip()
        if not v:
            continue
        if k == "简介" or k.endswith("简介"):
            return v
    parts: list[str] = []
    for _, row in df.iterrows():
        k = str(row.get("项目", "")).strip()
        v = str(row.get("值", "")).strip()
        if not v and not k:
            continue
        parts.append(f"{k}：{v}" if k else v)
    return "\n".join(parts)


def _fetch_intro_from_ths(board_name: str) -> str:
    import akshare as ak

    for sym in _ths_try_symbols(board_name):
        try:
            df = ak.stock_board_concept_info_ths(symbol=sym)
        except Exception:
            continue
        text = _parse_ths_info_df(df)
        if text.strip():
            return text
    return ""


def get_board_concept_intro(board_name: str) -> Tuple[str, str]:
    """
    返回 (完整正文, 列表用短预览)。
    同花顺板块名称需与本地板块名一致；行业类板块可能无对应概念页，则返回空字符串。
    """
    now = time.time()
    hit = _CACHE.get(board_name)
    if hit and now - hit[1] < _TTL_SEC:
        full = hit[0]
    else:
        full = _fetch_intro_from_ths(board_name)
        _CACHE[board_name] = (full, now)

    one_line = " ".join(full.split())
    max_len = 72
    if len(one_line) <= max_len:
        preview = one_line
    else:
        preview = one_line[:max_len] + "…"
    return full, preview


def clear_board_concept_intro_cache():
    _CACHE.clear()

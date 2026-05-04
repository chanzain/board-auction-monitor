"""
从东方财富API构建 概念板块+行业板块→成分股 映射
数据源: push2.eastmoney.com (公开API，无需token)
并发请求 + 断点续传 + 健壮容错
输出: data/concept_board_stock_map.json
"""

import requests
import json
import time
import os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

DATA_DIR = Path(__file__).parent / "data"
OUTPUT_FILE = DATA_DIR / "concept_board_stock_map.json"
PROGRESS_FILE = DATA_DIR / "concept_map_progress.json"

BASE_URL = "https://push2.eastmoney.com/api/qt/clist/get"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://data.eastmoney.com/",
}

# 跳过不适合竞价监控的板块关键词
SKIP_KEYWORDS = [
    "昨日", "连续", "涨停", "首板", "连板", "跌停", "破板", "炸板",
    "季报", "年报", "中报", "业绩预告", "业绩大增", "业绩扭亏",
    "龙虎榜", "融资融券", "融券", "沪股通", "深股通", "北向", "机构",
    "ST", "退市", "*",
]


def should_skip(name: str) -> bool:
    return any(kw in name for kw in SKIP_KEYWORDS)


def fetch_json(url: str, params: dict, timeout: int = 20) -> dict:
    """安全获取JSON，返回data字段或None"""
    try:
        resp = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        if data.get("rc") == 0 and data.get("data"):
            return data["data"]
    except Exception:
        pass
    return None


def fetch_board_list(board_type: str) -> list:
    """
    获取板块列表（概念或行业）
    board_type: "concept"(t:3) 或 "industry"(t:2)
    """
    type_name = "概念" if board_type == "concept" else "行业"
    fs_param = "m:90+t:3" if board_type == "concept" else "m:90+t:2"

    print(f"\n{'='*60}")
    print(f"获取东方财富{type_name}板块列表")
    print(f"{'='*60}")

    boards = []
    page = 1
    total = None

    while True:
        params = {
            "pn": page, "pz": 200, "po": 1, "np": 1,
            "fltt": 2, "invt": 2, "fid": "f3",
            "fs": fs_param,
            "fields": "f12,f14",
        }
        data = None
        for attempt in range(3):
            data = fetch_json(BASE_URL, params, timeout=20)
            if data and data.get("diff"):
                break
            time.sleep(1)

        if not data or not data.get("diff"):
            print(f"  第{page}页: 重试3次仍失败，中断")
            break

        if total is None:
            total = data["total"]
            print(f"  共计 {total} 个{type_name}板块")

        for item in data["diff"]:
            boards.append({"code": item["f12"], "name": item["f14"]})

        if len(boards) >= total:
            break
        page += 1
        time.sleep(0.3)

    print(f"  获取 {len(boards)} 个{type_name}板块")
    return boards


def normalize_code(code: str) -> str:
    """将纯数字代码转为ts_code格式"""
    code = str(code).strip()
    if code.endswith((".SH", ".SZ", ".BJ")):
        return code
    if code.startswith("6"):
        return code + ".SH"
    elif code.startswith(("0", "3")):
        return code + ".SZ"
    elif code.startswith(("8", "4")):
        return code + ".BJ"
    return code + ".SZ"


def fetch_board_stocks(board_code: str) -> list:
    """获取单个板块的全部成分股（分页）"""
    all_codes = []
    page = 1
    while True:
        params = {
            "pn": page, "pz": 500, "po": 1, "np": 1,
            "fltt": 2, "invt": 2, "fid": "f12",
            "fs": f"b:{board_code}",
            "fields": "f12",
        }
        data = fetch_json(BASE_URL, params, timeout=20)
        if not data or not data.get("diff"):
            break

        total = data["total"]
        for item in data["diff"]:
            all_codes.append(normalize_code(item["f12"]))

        if len(all_codes) >= total:
            break
        page += 1
        time.sleep(0.05)

    return all_codes


def fetch_board_task(board: dict) -> tuple:
    """单个板块的采集任务，返回 (name, codes_or_None)"""
    name = board["name"]
    code = board["code"]

    if should_skip(name):
        return (name, None)

    try:
        stocks = fetch_board_stocks(code)
        return (name, stocks if stocks else None)
    except Exception:
        return (name, None)


def build_board_map(boards: list, desc: str) -> dict:
    """并发获取所有板块成分股"""
    print(f"\n{'='*60}")
    print(f"并发获取{desc}成分股（max_workers=10）")
    print(f"{'='*60}")

    board_map = {}
    fail_list = []
    skip_count = 0
    total = len(boards)

    # 断点续传
    done_names = set()
    if PROGRESS_FILE.exists():
        try:
            progress = json.load(open(PROGRESS_FILE, encoding="utf-8"))
            done_names = set(progress.get("done", []))
            output_path = DATA_DIR / "concept_board_stock_map_temp.json"
            if output_path.exists():
                board_map = json.load(open(output_path, encoding="utf-8"))
            print(f"  断点续传: 已恢复 {len(done_names)} 个已完成板块")
        except Exception:
            done_names = set()

    pending = [b for b in boards if b["name"] not in done_names]
    print(f"  总计 {total} 个 | 已完成 {len(done_names)} | 待处理 {len(pending)}\n")

    def save_progress():
        progress = {"done": list(done_names), "total": total}
        with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
            json.dump(progress, f, ensure_ascii=False, indent=2)
        with open(DATA_DIR / "concept_board_stock_map_temp.json", "w", encoding="utf-8") as f:
            json.dump(board_map, f, ensure_ascii=False, indent=2)

    start = time.time()
    completed = len(done_names)

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(fetch_board_task, b): b for b in pending}
        for future in as_completed(futures):
            board = futures[future]
            completed += 1
            try:
                name, stocks = future.result()
                if name is None:
                    skip_count += 1
                elif stocks is not None:
                    board_map[name] = stocks
                    done_names.add(name)
                else:
                    fail_list.append(board["name"])
                    done_names.add(board["name"])
            except Exception:
                fail_list.append(board["name"])
                done_names.add(board["name"])

            if completed % 20 == 0 or completed == total:
                save_progress()
                elapsed = time.time() - start
                speed = completed / elapsed if elapsed > 0 else 0
                eta = (total - completed) / speed if speed > 0 else 0
                print(f"  [{completed:>3}/{total}] 成功 {len(board_map):>3} | "
                      f"跳过 {skip_count:>3} | 失败 {len(fail_list):>3} | "
                      f"速度 {speed:.1f}/s | 预计剩余 {eta:.0f}s")

    if PROGRESS_FILE.exists():
        os.remove(PROGRESS_FILE)
    temp_file = DATA_DIR / "concept_board_stock_map_temp.json"
    if temp_file.exists():
        os.remove(temp_file)

    print(f"\n{desc}完成！成功 {len(board_map)} 个 | 跳过 {skip_count} 个 | 失败 {len(fail_list)} 个")
    if fail_list:
        print(f"  失败: {fail_list[:10]}{'...' if len(fail_list)>10 else ''}")
    return board_map


def print_statistics(board_map: dict):
    """打印统计信息"""
    print(f"\n{'='*60}")
    print("板块成分股统计")
    print(f"{'='*60}")

    items = sorted(board_map.items(), key=lambda x: len(x[1]), reverse=True)
    counts = [len(v) for v in board_map.values()]

    print(f"\n板块总数: {len(board_map)}")
    print(f"覆盖股票总数(去重): {len(set(c for v in board_map.values() for c in v))}")

    print(f"\n成分股数量 Top 20:")
    for name, codes in items[:20]:
        print(f"  {name}: {len(codes):>4} 只")

    print(f"\n成分股数量 Bottom 10:")
    for name, codes in items[-10:]:
        print(f"  {name}: {len(codes):>4} 只")

    ranges = [(1,5), (6,10), (11,20), (21,50), (51,100), (101,200), (201,500)]
    print(f"\n成分股数量分布:")
    for lo, hi in ranges:
        n = sum(1 for c in counts if lo <= c <= hi)
        bar = "█" * min(n // 5, 40)
        print(f"  {lo:>3}-{hi:<3}只: {n:>3} 个  {bar}")

    # 验证关键板块
    print(f"\n关键板块验证:")
    check_names = [
        "CPO", "光模块", "华为概念", "人工智能", "芯片",
        "有色金属", "小金属", "电力", "化工", "银行",
        "煤炭", "钢铁", "汽车", "医药",
    ]
    for name in check_names:
        matches = [(k, len(v)) for k, v in board_map.items() if name in k]
        if matches:
            for k, c in matches[:3]:
                print(f"  ✓ {k}: {c} 只")
        else:
            print(f"  ✗ {name}: 未找到")


def main():
    os.makedirs(DATA_DIR, exist_ok=True)

    # Step1: 获取概念板块
    concepts = fetch_board_list("concept")
    # Step2: 获取行业板块
    industries = fetch_board_list("industry")

    all_boards = concepts + industries
    print(f"\n合计: 概念{len(concepts)} + 行业{len(industries)} = {len(all_boards)} 个板块\n")

    if not all_boards:
        print("获取板块列表失败")
        return

    # Step3: 并发获取成分股（合并处理）
    board_map = build_board_map(all_boards, "全部板块")

    if not board_map:
        print("未获取到任何板块映射")
        return

    # Step4: 保存
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(board_map, f, ensure_ascii=False, indent=2)
    print(f"\n映射已保存到: {OUTPUT_FILE}")

    # Step5: 统计
    print_statistics(board_map)


if __name__ == "__main__":
    import traceback
    try:
        main()
    except Exception as e:
        print(f"\n[FATAL] {e}", flush=True)
        traceback.print_exc()
        sys.exit(1)

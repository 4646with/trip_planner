"""
重构质量验证 + 报告生成脚本

测试目标：评估旅游攻略质量

运行方式：
    cd backend
    python tests/test_refactor_quality.py

输出：
    - backend/tests/output/prev.json     # 上个版本（首次运行无此文件）
    - backend/tests/output/current.json   # 当前版本
    - backend/tests/output/REPORT.md     # 质量评估报告
"""

import asyncio
import json
import logging
import os
import re
import sys
import io
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# Windows UTF-8 支持
if sys.platform == "win32":
    import codecs

    sys.stdout = codecs.getwriter("utf-8")(sys.stdout.buffer, "strict")
    sys.stderr = codecs.getwriter("utf-8")(sys.stderr.buffer, "strict")

# 配置 logging
logging.basicConfig(level=logging.INFO, format="%(message)s")

# 设置路径
sys.path.insert(0, str(Path(__file__).parent.parent))

# 测试数据
TEST_CASE = {
    "city": "深圳",
    "start_date": "2026-03-23",
    "end_date": "2026-03-24",
    "travel_days": 2,
    "transportation": "自驾",
    "accommodation": "豪华型酒店",
    "preferences": ["休闲", "美食"],
    "free_text_input": "极致奢华的体验",
}

# 输出目录
OUTPUT_DIR = Path(__file__).parent / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

PREV_FILE = OUTPUT_DIR / "prev.json"
CURRENT_FILE = OUTPUT_DIR / "current.json"


def save_json(data: Dict, filepath: Path):
    """保存 JSON 文件"""
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# 日志捕获器
class LogCapture:
    def __init__(self):
        self.stream = io.StringIO()
        self.handler = logging.StreamHandler(self.stream)
        self.handler.setLevel(logging.DEBUG)
        self.handler.setFormatter(logging.Formatter("%(message)s"))

    def __enter__(self):
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.DEBUG)
        root_logger.addHandler(self.handler)
        return self

    def __exit__(self, *args):
        root_logger = logging.getLogger()
        root_logger.removeHandler(self.handler)
        self.handler.close()

    def get_output(self) -> str:
        return self.stream.getvalue()


def evaluate_plan_quality(result: Dict) -> Dict:
    """评估攻略质量"""
    quality = {
        "timestamp": datetime.now().isoformat(),
        "test_case": TEST_CASE,
        "dimensions": {},
        "summary": {},
    }

    if not result:
        quality["summary"]["error"] = "无结果数据"
        return quality

    # 1. 景点质量
    attractions = []
    for day in result.get("days", []):
        attractions.extend(day.get("attractions", []))

    luxury_keywords = [
        "米其林",
        "奢华",
        "顶级",
        "直升机",
        "游艇",
        "私人",
        "贵宾",
        "总统",
        "云端",
        "无边",
    ]
    attraction_descriptions = [a.get("description", "") for a in attractions]
    luxury_count = sum(
        1 for desc in attraction_descriptions for kw in luxury_keywords if kw in desc
    )

    quality["dimensions"]["attractions"] = {
        "count": len(attractions),
        "avg_description_length": sum(len(d) for d in attraction_descriptions)
        // max(len(attractions), 1),
        "luxury_keyword_count": luxury_count,
        "with_location_count": sum(1 for a in attractions if a.get("location")),
        "location_rate": round(
            sum(1 for a in attractions if a.get("location"))
            / max(len(attractions), 1)
            * 100,
            1,
        ),
        "list": [
            {"name": a.get("name"), "description": a.get("description", "")[:80]}
            for a in attractions[:3]
        ],
    }

    # 2. 酒店质量
    hotels = result.get("days", [{}])[0].get("attractions", [])
    hotel_names = [
        h.get("name", "")
        for h in hotels
        if "酒店" in h.get("category", "") or "酒店" in h.get("name", "")
    ]

    luxury_hotels = [
        "莱佛士",
        "瑞吉",
        "文华东方",
        "四季",
        "柏悦",
        "洲际",
        "华尔道夫",
        "半岛",
    ]
    is_luxury = any(hl in name for name in hotel_names for hl in luxury_hotels)

    quality["dimensions"]["hotels"] = {
        "count": len(hotel_names),
        "is_luxury_brand": is_luxury,
        "luxury_keywords": [h for h in luxury_hotels if h in " ".join(hotel_names)],
        "list": hotel_names[:3],
    }

    # 3. 餐厅质量
    meals = []
    for day in result.get("days", []):
        meals.extend(day.get("meals", []))

    michelin_keywords = ["米其林", "黑珍珠", "星级"]
    has_michelin = any(
        mk in m.get("name", "") or mk in m.get("description", "")
        for m in meals
        for mk in michelin_keywords
    )
    avg_cost = sum(m.get("estimated_cost", 0) for m in meals) // max(len(meals), 1)

    quality["dimensions"]["restaurants"] = {
        "count": len(meals),
        "has_michelin": has_michelin,
        "avg_cost": avg_cost,
        "list": [
            {"name": m.get("name"), "cost": m.get("estimated_cost", 0)}
            for m in meals[:3]
        ],
    }

    # 4. 行程逻辑
    districts = set()
    for day in result.get("days", []):
        for a in day.get("attractions", []):
            addr = a.get("address", "")
            for district in ["南山区", "福田区", "罗湖区", "宝安区", "龙华区"]:
                if district in addr:
                    districts.add(district)

    quality["dimensions"]["itinerary"] = {
        "days": len(result.get("days", [])),
        "unique_districts": len(districts),
        "districts": list(districts),
        "has_transportation": any(
            day.get("transportation") for day in result.get("days", [])
        ),
    }

    # 5. 总体建议
    suggestions = result.get("overall_suggestions", "")
    has_booking = (
        "预约" in suggestions or "预订" in suggestions or "提前" in suggestions
    )
    has_weather = (
        "天气" in suggestions or "薄外套" in suggestions or "雨" in suggestions
    )
    has_budget = "预算" in suggestions or "备用" in suggestions or "消费" in suggestions

    quality["dimensions"]["suggestions"] = {
        "length": len(suggestions),
        "has_booking_tips": has_booking,
        "has_weather_tips": has_weather,
        "has_budget_tips": has_budget,
        "preview": suggestions[:150] + "..." if len(suggestions) > 150 else suggestions,
    }

    # 6. 预算
    budget = result.get("budget", {})
    quality["dimensions"]["budget"] = {
        "total": budget.get("total", 0),
        "attractions": budget.get("total_attractions", 0),
        "hotels": budget.get("total_hotels", 0),
        "meals": budget.get("total_meals", 0),
        "transportation": budget.get("total_transportation", 0),
    }

    # 汇总
    quality["summary"] = {
        "total_budget": budget.get("total", 0),
        "attraction_count": len(attractions),
        "restaurant_count": len(meals),
        "has_michelin": has_michelin,
        "is_luxury": is_luxury,
        "suggestion_length": len(suggestions),
    }

    return quality


def compare_values(current, prev, key, default=0):
    """比较数值变化"""
    if not prev:
        return "-"
    prev_dim = prev.get("dimensions", {})
    prev_val = prev_dim.get(key, {}).get("count") or prev_dim.get(key, {}).get(
        key, default
    )
    curr_val = current.get("count") or current.get(key, default)
    if isinstance(prev_val, (int, float)) and isinstance(curr_val, (int, float)):
        if curr_val > prev_val:
            return f"↑ {curr_val}"
        elif curr_val < prev_val:
            return f"↓ {curr_val}"
        return "→"
    return "→"


def generate_report(current_quality: Dict, prev_quality: Optional[Dict] = None) -> str:
    """生成 MD 报告"""

    d = current_quality["dimensions"]
    s = current_quality["summary"]

    prev_att = prev_quality["dimensions"]["attractions"] if prev_quality else None
    prev_rest = prev_quality["dimensions"]["restaurants"] if prev_quality else None
    prev_sug = prev_quality["dimensions"]["suggestions"] if prev_quality else None

    report = f"""# 旅游攻略质量报告

## 测试信息
- **时间**: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
- **用例**: {TEST_CASE["city"]} {TEST_CASE["travel_days"]}天游
- **偏好**: {", ".join(TEST_CASE["preferences"])}
- **住宿**: {TEST_CASE["accommodation"]}
- **特色**: {TEST_CASE["free_text_input"]}

---

## 质量维度

### 1. 景点质量

| 指标 | 数值 | 对比 |
|------|------|------|
| 景点数量 | {d["attractions"]["count"]} | {compare_values(d["attractions"], prev_att, "count") if prev_att else "-"} |
| 平均描述字数 | {d["attractions"]["avg_description_length"]} | {compare_values(d["attractions"], prev_att, "avg_description_length") if prev_att else "-"} |
| 奢华关键词数 | {d["attractions"]["luxury_keyword_count"]} | {compare_values(d["attractions"], prev_att, "luxury_keyword_count") if prev_att else "-"} |
| 坐标完整率 | {d["attractions"]["location_rate"]}% | {compare_values(d["attractions"], prev_att, "location_rate") if prev_att else "-"} |

**景点列表**:
"""
    for a in d["attractions"]["list"]:
        report += f"- **{a['name']}**: {a['description']}...\n"

    prev_hotels = prev_quality["dimensions"]["hotels"] if prev_quality else None

    report += f"""
### 2. 酒店质量

| 指标 | 数值 | 对比 |
|------|------|------|
| 酒店数量 | {d["hotels"]["count"]} | {compare_values(d["hotels"], prev_hotels, "count") if prev_hotels else "-"} |
| 奢华品牌 | {"是" if d["hotels"]["is_luxury_brand"] else "否"} | - |
| 品牌关键词 | {", ".join(d["hotels"]["luxury_keywords"]) or "无"} | - |

**酒店列表**:
"""
    for h in d["hotels"]["list"]:
        report += f"- {h}\n"

    report += f"""
### 3. 餐厅质量

| 指标 | 数值 | 对比 |
|------|------|------|
| 餐厅数量 | {d["restaurants"]["count"]} | {compare_values(d["restaurants"], prev_rest, "count") if prev_rest else "-"} |
| 米其林 | {"是" if d["restaurants"]["has_michelin"] else "否"} | - |
| 平均餐费 | {d["restaurants"]["avg_cost"]}元 | {compare_values(d["restaurants"], prev_rest, "avg_cost") if prev_rest else "-"} |

**餐厅列表**:
"""
    for m in d["restaurants"]["list"]:
        report += f"- {m['name']} ({m['cost']}元)\n"

    report += f"""
### 4. 行程逻辑

| 指标 | 数值 |
|------|------|
| 游玩天数 | {d["itinerary"]["days"]} |
| 涉及区域 | {", ".join(d["itinerary"]["districts"]) or "未识别"} |
| 有交通安排 | {"是" if d["itinerary"]["has_transportation"] else "否"} |

### 5. 总体建议

| 指标 | 数值 | 对比 |
|------|------|------|
| 字数 | {d["suggestions"]["length"]} | {compare_values(d["suggestions"], prev_sug, "length") if prev_sug else "-"} |
| 预订提示 | {"有" if d["suggestions"]["has_booking_tips"] else "无"} | - |
| 天气提示 | {"有" if d["suggestions"]["has_weather_tips"] else "无"} | - |
| 预算提示 | {"有" if d["suggestions"]["has_budget_tips"] else "无"} | - |

**建议内容**:
> {d["suggestions"]["preview"]}

---

## 预算汇总

| 项目 | 金额(元) |
|------|----------|
| 景点门票 | {d["budget"]["attractions"]} |
| 酒店住宿 | {d["budget"]["hotels"]} |
| 餐饮 | {d["budget"]["meals"]} |
| 交通 | {d["budget"]["transportation"]} |
| **总计** | **{d["budget"]["total"]}** |

---

## 快速评估

| 维度 | 评分 |
|------|------|
| 景点奢华度 | {"⭐⭐⭐⭐⭐" if d["attractions"]["luxury_keyword_count"] >= 5 else "⭐⭐⭐⭐" if d["attractions"]["luxury_keyword_count"] >= 3 else "⭐⭐⭐"} |
| 酒店匹配度 | {"⭐⭐⭐⭐⭐" if d["hotels"]["is_luxury_brand"] else "⭐⭐⭐⭐" if d["hotels"]["count"] > 0 else "⭐⭐⭐"} |
| 餐厅质量 | {"⭐⭐⭐⭐⭐" if d["restaurants"]["has_michelin"] else "⭐⭐⭐⭐" if d["restaurants"]["count"] > 0 else "⭐⭐⭐"} |
| 建议完整性 | {"⭐⭐⭐⭐⭐" if all([d["suggestions"]["has_booking_tips"], d["suggestions"]["has_weather_tips"], d["suggestions"]["has_budget_tips"]]) else "⭐⭐⭐⭐" if any([d["suggestions"]["has_booking_tips"], d["suggestions"]["has_weather_tips"], d["suggestions"]["has_budget_tips"]]) else "⭐⭐⭐"} |

---

*报告生成: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}*
"""

    return report


async def run_test():
    """运行测试"""
    print("=" * 60)
    print("        重构质量验证测试")
    print("=" * 60)
    print(f"测试用例: {TEST_CASE['city']} {TEST_CASE['travel_days']}天游")
    print(f"偏好: {TEST_CASE['preferences']}")
    print(f"住宿: {TEST_CASE['accommodation']}")

    # 检查是否有 prev
    has_prev = PREV_FILE.exists()
    print(f"上个版本: {'有' if has_prev else '无 (首次运行)'}")
    print()

    # 捕获日志并调用系统
    with LogCapture() as log_capture:
        from app.agents.main import MapAgentsSystem
        from app.models.schemas import TripRequest

        print("正在初始化 MapAgentsSystem...")

        try:
            system = MapAgentsSystem()
            await system.initialize()
            print("初始化完成\n")

            print("正在调用 plan_trip_async...")
            request = TripRequest(**TEST_CASE)
            result = await system.plan_trip_async(request)
            print("调用完成\n")

            result_dict = (
                result.model_dump() if hasattr(result, "model_dump") else result
            )
        except Exception as e:
            print(f"调用失败: {e}")
            import traceback

            traceback.print_exc()
            return None, None, None

    log_output = log_capture.get_output()

    # 提取日志中的关键信息
    print("解析日志...")
    web_search_injected = "[attraction] 已注入增强版 web_search" in log_output
    geo_strategy = "地理信息透传策略" in log_output
    route_fix = "必须使用 attractions 中已获取的景点地址" in log_output

    print(f"  - 增强工具注入: {'是' if web_search_injected else '否'}")
    print(f"  - 地理透传策略: {'是' if geo_strategy else '否'}")
    print(f"  - Route 起点纠正: {'是' if route_fix else '否'}")

    # 评估质量
    print("\n评估攻略质量...")
    quality = evaluate_plan_quality(result_dict)

    # 保存
    if has_prev:
        prev_quality = json.loads(PREV_FILE.read_text(encoding="utf-8"))
        prev_quality = prev_quality.get("quality", {})
    else:
        prev_quality = None

    save_json(
        {
            "result": result_dict,
            "quality": quality,
            "timestamp": datetime.now().isoformat(),
        },
        CURRENT_FILE,
    )

    if not has_prev:
        save_json(
            {
                "result": result_dict,
                "quality": quality,
                "timestamp": datetime.now().isoformat(),
            },
            PREV_FILE,
        )
        print(f"\n首次运行，已保存为 prev.json")
    else:
        print(f"\n已保存为 current.json (prev.json 保持不变)")

    # 生成报告
    print("\n生成报告...")
    report = generate_report(quality, prev_quality)
    report_file = OUTPUT_DIR / "REPORT.md"
    report_file.write_text(report, encoding="utf-8")
    print(f"报告已保存: {report_file}")

    return quality, prev_quality, report


def main():
    """主入口"""
    quality, prev_quality, report = asyncio.run(run_test())

    if report:
        print("\n" + "=" * 60)
        print("        报告预览")
        print("=" * 60)
        print(report)

    # 提示用户
    print("\n" + "=" * 60)
    print("提示:")
    print("- current.json: 当前版本")
    print("- prev.json: 上个版本 (用于对比)")
    print("- REPORT.md: 质量评估报告")
    print("")
    print("如需更新 prev.json 为当前版本，删除 prev.json 后重新运行")
    print("=" * 60)


if __name__ == "__main__":
    main()

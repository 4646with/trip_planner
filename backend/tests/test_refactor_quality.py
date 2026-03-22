"""
重构质量验证测试脚本（简化版）

运行方式：
    cd backend
    python tests/test_refactor_quality.py

输出：
    - backend/tests/output/current.json   # 当前版本
    - backend/tests/output/REPORT.md     # 原始数据摘要
"""

import asyncio
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

# Windows UTF-8 支持
if sys.platform == "win32":
    import codecs

    sys.stdout = codecs.getwriter("utf-8")(sys.stdout.buffer, "strict")

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

CURRENT_FILE = OUTPUT_DIR / "current.json"


async def run_test():
    print("=" * 60)
    print("        重构质量验证测试")
    print("=" * 60)
    print(f"测试用例: {TEST_CASE['city']} {TEST_CASE['travel_days']}天游")
    print(f"偏好: {TEST_CASE['preferences']}")
    print(f"住宿: {TEST_CASE['accommodation']}")
    print()

    from app.agents.main import MapAgentsSystem
    from app.models.schemas import TripRequest

    print("初始化 MapAgentsSystem...")

    try:
        system = MapAgentsSystem()
        await system.initialize()
        print("初始化完成\n")

        print("调用 plan_trip_async...")
        request = TripRequest(**TEST_CASE)
        result = await system.plan_trip_async(request)
        print("调用完成\n")

        result_dict = result.model_dump() if hasattr(result, "model_dump") else result
    except Exception as e:
        print(f"调用失败: {e}")
        import traceback

        traceback.print_exc()
        return None

    # 保存结果
    save_data = {
        "timestamp": datetime.now().isoformat(),
        "test_case": TEST_CASE,
        "result": result_dict,
    }

    with open(CURRENT_FILE, "w", encoding="utf-8") as f:
        json.dump(save_data, f, ensure_ascii=False, indent=2)

    print(f"结果已保存: {CURRENT_FILE}")

    # 生成简单报告
    generate_report(result_dict)

    return save_data


def generate_report(result: dict):
    """生成简单报告"""
    report = f"""# 测试结果

## 测试信息
- 时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
- 用例: {TEST_CASE["city"]} {TEST_CASE["travel_days"]}天游
- 偏好: {", ".join(TEST_CASE["preferences"])}

## 景点

| 名称 | 描述 | 地址 |
|------|------|------|
"""

    for day in result.get("days", []):
        for attr in day.get("attractions", []):
            name = attr.get("name", "")
            desc = attr.get("description", "")[:60]
            addr = attr.get("address", "")[:40]
            report += f"| {name} | {desc}... | {addr}... |\n"

    # 餐厅
    report += """
## 餐厅

| 餐次 | 名称 | 预估费用 |
|------|------|----------|
"""
    for day in result.get("days", []):
        for meal in day.get("meals", []):
            meal_type = meal.get("type", "")
            name = meal.get("name", "")
            cost = meal.get("estimated_cost", 0)
            report += f"| {meal_type} | {name} | {cost}元 |\n"

    # 酒店
    hotels = result.get("days", [{}])[0].get("hotel")
    if hotels:
        report += f"""
## 酒店
- 名称: {hotels.get("name", "")}
- 价格: {hotels.get("price_range", hotels.get("estimated_cost", ""))}
"""

    # 预算
    budget = result.get("budget") or {}
    report += f"""
## 预算
- 景点: {budget.get("total_attractions", 0)}元
- 酒店: {budget.get("total_hotels", 0)}元
- 餐饮: {budget.get("total_meals", 0)}元
- 交通: {budget.get("total_transportation", 0)}元
- **总计**: {budget.get("total", 0)}元

## 总体建议
{result.get("overall_suggestions", "")}

---
*详细数据见 current.json*
"""

    report_file = OUTPUT_DIR / "REPORT.md"
    report_file.write_text(report, encoding="utf-8")
    print(f"报告已保存: {report_file}")


def main():
    asyncio.run(run_test())


if __name__ == "__main__":
    main()

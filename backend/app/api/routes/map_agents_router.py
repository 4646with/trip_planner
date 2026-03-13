"""基于LangGraph的地图智能体API路由 - MCP集成版"""

from fastapi import APIRouter, HTTPException
from ...models.schemas import (
    TripRequest,
    TripPlanResponse,
    ErrorResponse
)
from ...agents import (
    get_map_agents_system,
    initialize_map_agents_system,
    cleanup_map_agents_system
)

router = APIRouter(prefix="/map-agents", tags=["地图智能体"])


# /**
#  * @api {post} /api/map-agents/plan 生成旅行计划
#  * @apiName PlanTrip
#  * @apiGroup MapAgents
#  * @apiParam {String} city 目标城市
#  * @apiParam {String} start_date 开始日期
#  * @apiParam {String} end_date 结束日期
#  * @apiParam {Number} travel_days 旅行天数
#  * @apiParam {String} transportation 交通方式
#  * @apiSuccess {Boolean} success 是否成功
#  * @apiSuccess {String} message 响应消息
#  * @apiSuccess {Object} data 旅行计划数据
#  */

# /**
#  * @api {get} /api/map-agents/health 健康检查
#  * @apiName HealthCheck
#  * @apiGroup MapAgents
#  * @apiSuccess {String} status 服务状态
#  */

# /**
#  * @api {post} /api/map-agents/initialize 初始化MCP工具
#  * @apiName InitializeMCP
#  * @apiGroup MapAgents
#  * @apiSuccess {String} status 状态
#  */

# /**
#  * @api {post} /api/map-agents/cleanup 清理MCP资源
#  * @apiName CleanupMCP
#  * @apiGroup MapAgents
#  * @apiSuccess {String} status 状态
#  */

# /**
#  * @api {post} /api/photo/generate 生成旅行照片
#  * @apiName GeneratePhoto
#  * @apiGroup Photo
#  * @apiParam {String} city 城市名称
#  * @apiParam {String} location 地点描述
#  * @apiSuccess {String} url 照片URL
#  */





@router.post(
    "/plan",
    response_model=TripPlanResponse,
    summary="生成旅行计划",
    description="基于LangGraph的地图智能体生成详细的旅行计划 - 使用MCP集成高德地图服务"
)
async def plan_trip(request: TripRequest):
    """
    生成旅行计划
    
    Args:
        request: 旅行请求参数
        
    Returns:
        旅行计划响应
    """
    try:
        print(f"\n{'='*60}")
        print(f"📥 收到地图智能体旅行规划请求:")
        print(f"   城市: {request.city}")
        print(f"   日期: {request.start_date} - {request.end_date}")
        print(f"   天数: {request.travel_days}")
        print(f"   交通方式: {request.transportation}")
        print(f"{'='*60}\n")
        
        print("🔄 初始化地图智能体系统（含MCP工具）...")
        import time
        start_time = time.time()
        system = await initialize_map_agents_system()
        init_time = time.time() - start_time
        print(f"✅ 地图智能体系统初始化完成，耗时: {init_time:.2f}秒")
        
        print("🚀 开始生成旅行计划...")
        start_time = time.time()
        trip_plan = await system.plan_trip_async(request)
        plan_time = time.time() - start_time
        print(f"✅ 旅行计划生成成功,耗时: {plan_time:.2f}秒")
        print("✅ 准备返回响应\n")
        
        return TripPlanResponse(
            success=True,
            message="旅行计划生成成功",
            data=trip_plan
        )
        
    except Exception as e:
        print(f"❌ 生成旅行计划失败: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"生成旅行计划失败: {str(e)}"
        )


@router.get(
    "/health",
    summary="健康检查",
    description="检查地图智能体服务是否正常"
)
async def health_check():
    """健康检查"""
    try:
        from ...services.mcp_tools import get_mcp_manager
        mcp_manager = get_mcp_manager()
        
        # 避免在健康检查中执行可能阻塞的操作
        tools_count = 0
        try:
            if mcp_manager.is_initialized:
                tools = mcp_manager.list_available_tools()
                tools_count = len(tools)
        except:
            pass
        
        return {
            "status": "healthy",
            "service": "map-agents",
            "graph": "StateGraph",
            "mcp_initialized": mcp_manager.is_initialized,
            "mcp_tools_count": tools_count
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "service": "map-agents",
            "error": str(e)
        }


@router.post(
    "/initialize",
    summary="初始化MCP工具",
    description="手动初始化MCP工具和高德地图服务"
)
async def initialize_mcp():
    """手动初始化 MCP 工具"""
    try:
        system = await initialize_map_agents_system()
        from ...services.mcp_tools import get_mcp_manager
        mcp_manager = get_mcp_manager()
        
        return {
            "status": "success",
            "message": "MCP工具初始化成功",
            "mcp_initialized": mcp_manager.is_initialized,
            "available_tools": mcp_manager.list_available_tools()
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"MCP工具初始化失败: {str(e)}"
        )


@router.post(
    "/cleanup",
    summary="清理MCP资源",
    description="清理MCP服务器进程和资源"
)
async def cleanup_mcp():
    """手动清理 MCP 资源"""
    try:
        await cleanup_map_agents_system()
        return {
            "status": "success",
            "message": "MCP资源已清理"
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"MCP资源清理失败: {str(e)}"
        )

"""POI相关API路由"""

from fastapi import APIRouter, Query
from typing import Optional
from ...services.unsplash_service import get_unsplash_service

router = APIRouter(prefix="/photo", tags=["景点图片"])


# /**
#  * @api {get} /api/photo/search 搜索景点图片
#  * @apiName SearchPhoto
#  * @apiGroup Photo
#  * @apiParam {String} name 景点名称
#  * @apiSuccess {Boolean} success 是否成功
#  * @apiSuccess {String} photo_url 图片URL
#  */





@router.get(
    "/search",
    summary="搜索景点图片",
    description="根据景点名称从Unsplash获取相关图片"
)
async def search_poi_photo(name: str = Query(..., description="景点名称")):
    """
    搜索景点图片
    
    Args:
        name: 景点名称
        
    Returns:
        包含图片URL的响应
    """
    try:
        unsplash_service = get_unsplash_service()
        photo_url = unsplash_service.get_photo_url(name)
        
        if photo_url:
            return {
                "success": True,
                "data": {
                    "photo_url": photo_url,
                    "name": name
                }
            }
        else:
            return {
                "success": False,
                "message": "未找到相关图片",
                "data": {
                    "photo_url": None,
                    "name": name
                }
            }
            
    except Exception as e:
        print(f"❌ 获取景点图片失败: {str(e)}")
        return {
            "success": False,
            "message": f"获取图片失败: {str(e)}",
            "data": {
                "photo_url": None,
                "name": name
            }
        }

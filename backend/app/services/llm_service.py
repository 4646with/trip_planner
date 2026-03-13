from langchain_openai import ChatOpenAI
from ..config import get_settings
from pydantic import SecretStr

import os

def get_llm():
    """
    获取大语言模型实例
    使用 langchain_openai 原生接入智谱AI，完美支持 LangGraph
    """
    settings = get_settings()
    
    api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    
    if not api_key:
        raise ValueError("未找到大模型 API Key，请检查 .env 配置文件！")
    
    base_url = os.getenv("LLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
    model = os.getenv("LLM_MODEL_ID", "glm-4-flash")
    
    llm = ChatOpenAI(
        api_key=SecretStr(api_key),
        base_url=base_url,
        model=model,
        temperature=0.7,
        max_retries=3,
        timeout=120
    )
    
    return llm

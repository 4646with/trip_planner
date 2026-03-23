from langchain_openai import ChatOpenAI
from ..config import get_settings
from pydantic import SecretStr

import os

# 设置代理（如果配置了）
_http_proxy = os.getenv("HTTP_PROXY") or os.getenv("http_proxy")
_https_proxy = os.getenv("HTTPS_PROXY") or os.getenv("https_proxy")
if _http_proxy:
    os.environ["HTTP_PROXY"] = _http_proxy
if _https_proxy:
    os.environ["HTTPS_PROXY"] = _https_proxy
if _https_proxy:
    print(f"[LLM] 使用代理: {_https_proxy}")


def get_llm():
    """
    获取大语言模型实例

    支持的模型类型：
    1. Kimi (推荐)：设置 LLM_MODEL_ID 包含 "kimi" 或 "moonshot"
       - API Key: LLM_API_KEY 或 OPENAI_API_KEY
       - Base URL: Kimi API 地址
       - Model: moonshot-v1 系列

    2. 智谱AI (默认)：设置 LLM_PROVIDER=zhipu 或不设置
       - API Key: LLM_API_KEY 或 OPENAI_API_KEY
       - Base URL: https://open.bigmodel.cn/api/paas/v4 (默认)
       - Model: glm-4-flash (默认)
    """
    provider = os.getenv("LLM_PROVIDER", "zhipu").lower()

    if provider == "gemini":
        raise ValueError("Gemini 已下线，只支持 Kimi/智谱AI")

    api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("未找到 LLM_API_KEY，请检查 .env 配置文件！")

    base_url = os.getenv("LLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
    model = os.getenv("LLM_MODEL_ID", "glm-4-flash")

    is_kimi = "kimi" in model.lower() or "moonshot" in model.lower()
    default_temp = 0.6 if is_kimi else 0.7
    print(f"[LLM] 使用 {model}, temperature={default_temp}")

    extra_body = None
    if is_kimi:
        extra_body = {"thinking": {"type": "disabled"}}
        print(f"[LLM] 禁用 {model} 的思考功能")

    llm = ChatOpenAI(
        api_key=SecretStr(api_key),
        base_url=base_url,
        model=model,
        temperature=default_temp,
        max_retries=3,
        timeout=120,
        extra_body=extra_body,
    )

    return llm
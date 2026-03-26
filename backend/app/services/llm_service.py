from langchain_openai import ChatOpenAI
from ..config import get_settings
from pydantic import SecretStr

import os

_http_proxy = os.getenv("HTTP_PROXY") or os.getenv("http_proxy")
_https_proxy = os.getenv("HTTPS_PROXY") or os.getenv("https_proxy")
if _http_proxy:
    os.environ["HTTP_PROXY"] = _http_proxy
if _https_proxy:
    os.environ["HTTPS_PROXY"] = _https_proxy
if _https_proxy:
    print(f"[LLM] 使用代理: {_https_proxy}")


def get_llm():
    api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("未找到 LLM_API_KEY，请检查 .env 配置文件！")

    base_url = os.getenv("LLM_BASE_URL", "https://api.moonshot.cn/api/v1")
    model = os.getenv("LLM_MODEL_ID", "moonshot-v1-8k")

    print(f"[LLM] 使用 {model}, temperature=0.6")

    extra_body = {"thinking": {"type": "disabled"}}
    print(f"[LLM] 禁用 {model} 的思考功能")

    llm = ChatOpenAI(
        api_key=SecretStr(api_key),
        base_url=base_url,
        model=model,
        temperature=0.6,
        max_retries=3,
        timeout=120,
        extra_body=extra_body,
    )

    return llm
import os

from langchain_openai import ChatOpenAI
from pydantic import SecretStr


_http_proxy = os.getenv("HTTP_PROXY") or os.getenv("http_proxy")
_https_proxy = os.getenv("HTTPS_PROXY") or os.getenv("https_proxy")
if _http_proxy:
    os.environ["HTTP_PROXY"] = _http_proxy
if _https_proxy:
    os.environ["HTTPS_PROXY"] = _https_proxy
    print(f"[LLM] using proxy: {_https_proxy}")


def get_llm():
    base_url = os.getenv("LLM_BASE_URL", "https://api.moonshot.cn/api/v1")
    model = os.getenv("LLM_MODEL_ID", "moonshot-v1-8k")
    is_local_ollama = "11434" in base_url or "ollama" in model.lower()

    api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    if is_local_ollama and not api_key:
        api_key = "ollama"

    if not api_key:
        raise ValueError("LLM_API_KEY is missing. Check backend/.env.")

    extra_body = None
    if "bigmodel.cn" in base_url or "glm" in model.lower():
        extra_body = {"thinking": {"type": "disabled"}}
        print(f"[LLM] disabled thinking for {model}")

    timeout = int(os.getenv("LLM_TIMEOUT", "300" if is_local_ollama else "120"))
    temperature = float(os.getenv("LLM_TEMPERATURE", "0.6"))

    kwargs = {
        "api_key": SecretStr(api_key),
        "base_url": base_url,
        "model": model,
        "temperature": temperature,
        "max_retries": 0,
        "timeout": timeout,
    }
    if extra_body:
        kwargs["extra_body"] = extra_body

    print(f"[LLM] using {model}, base_url={base_url}, timeout={timeout}s")
    return ChatOpenAI(**kwargs)

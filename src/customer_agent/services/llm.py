"""DeepSeek chat model construction following the LeadGraph integration pattern."""

from customer_agent.config import settings


def get_deepseek(*, temperature: float = 0.2):
    if not settings.deepseek_api_key:
        raise RuntimeError("尚未配置 DEEPSEEK_API_KEY。")
    # Keep the web server's cold start fast; this SDK has a sizeable import cost.
    from langchain_deepseek import ChatDeepSeek

    return ChatDeepSeek(
        extra_body={"thinking": {"type": "disabled"}},
        model=settings.deepseek_model,
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        # This Anaconda environment has Brotli 1.0.9, whose process() method
        # rejects httpx2's output_buffer_limit keyword. Do not negotiate br;
        # gzip/deflate decoding remains supported by httpx2.
        default_headers={"Accept-Encoding": "gzip, deflate"},
        temperature=temperature,
        max_retries=2,
        timeout=60,
    )

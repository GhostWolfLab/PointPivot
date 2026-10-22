"""AI decision module - calls LLM via OpenAI-compatible API"""

import json
import logging
import re
import warnings
warnings.filterwarnings("ignore", message="Unverified HTTPS request")
import httpx
from app.config import decrypt_value
from app.models import SessionLocal, SystemConfig

logger = logging.getLogger(__name__)

VALID_ACTIONS = {"long", "short", "close_long", "close_short", "hold"}


def _get_ai_config(strategy) -> dict:
    """Resolve AI config: strategy override > global defaults."""
    db = SessionLocal()
    try:
        sys_cfg = db.query(SystemConfig).first()

        provider = strategy.ai_provider or (sys_cfg.ai_default_provider if sys_cfg else "deepseek")
        api_key = decrypt_value(strategy.ai_api_key_enc) if strategy.ai_api_key_enc else (
            decrypt_value(sys_cfg.ai_default_api_key_enc) if sys_cfg else ""
        )
        base_url = strategy.ai_base_url or (sys_cfg.ai_default_base_url if sys_cfg else "")
        model = strategy.ai_model or (sys_cfg.ai_default_model if sys_cfg else "deepseek-chat")

        return {
            "provider": provider,
            "api_key": api_key,
            "base_url": base_url.rstrip("/"),
            "model": model,
        }
    finally:
        db.close()


def call_ai(strategy, prompt: str) -> dict | None:
    """
    Call AI model and parse the JSON response.
    Returns parsed dict with keys: action, symbol, confidence, reasoning.
    Returns None on failure.
    """
    ai_cfg = _get_ai_config(strategy)

    if not ai_cfg["base_url"]:
        logger.error(f"Strategy {strategy.id}: AI base URL not configured")
        return None
    # 本地模型（Ollama/vLLM/LocalAI）可以没有 API Key
    if ai_cfg["provider"] != "local" and not ai_cfg["api_key"]:
        logger.error(f"Strategy {strategy.id}: AI API key not configured for provider '{ai_cfg['provider']}'")
        return None

    url = f"{ai_cfg['base_url']}/chat/completions"
    headers = {
        "Content-Type": "application/json",
    }
    if ai_cfg["api_key"]:
        headers["Authorization"] = f"Bearer {ai_cfg['api_key']}"
    payload = {
        "model": ai_cfg["model"],
        "messages": [
            {
                "role": "system",
                "content": "你是一个专业的加密货币交易分析师。你必须严格按照JSON格式返回分析结果，不要包含任何其他文字。"
            },
            {
                "role": "user",
                "content": prompt,
            }
        ],
        "temperature": 0.3,
        "max_tokens": 500,
    }

    try:
        transport = httpx.HTTPTransport(retries=2)
        with httpx.Client(timeout=60.0, verify=False, transport=transport, trust_env=False) as client:
            resp = client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()

        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        logger.info(f"AI raw response for strategy {strategy.id}: {content[:300]}")

        return _parse_ai_response(content)

    except httpx.TimeoutException:
        logger.error(f"AI call timeout for strategy {strategy.id}")
        return None
    except httpx.HTTPStatusError as e:
        logger.error(f"AI HTTP error for strategy {strategy.id}: {e.response.status_code} {e.response.text[:200]}")
        return None
    except Exception as e:
        logger.error(f"AI call failed for strategy {strategy.id}: {e}")
        return None


def _parse_ai_response(content: str) -> dict | None:
    """Parse AI response to extract JSON decision."""
    # Try to extract JSON from the response
    # The AI might wrap it in ```json ... ``` or return plain JSON
    content = content.strip()

    # Remove markdown code blocks
    json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', content, re.DOTALL)
    if json_match:
        content = json_match.group(1)
    else:
        # Try to find a JSON object directly
        json_match = re.search(r'\{[^{}]*"action"[^{}]*\}', content, re.DOTALL)
        if json_match:
            content = json_match.group(0)

    try:
        result = json.loads(content)
    except json.JSONDecodeError:
        logger.error(f"Failed to parse AI JSON: {content[:200]}")
        return None

    # Validate required fields
    action = result.get("action", "").lower().strip()
    if action not in VALID_ACTIONS:
        logger.error(f"Invalid action '{action}' from AI")
        return None

    return {
        "action": action,
        "symbol": result.get("symbol", ""),
        "confidence": int(result.get("confidence", 0)),
        "reasoning": str(result.get("reasoning", "")),
        "tp_price": result.get("tp_price"),   # None when hold/close
        "sl_price": result.get("sl_price"),   # None when hold/close
    }

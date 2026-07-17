"""Ollama connectivity & raw response test.

Directly calls Ollama HTTP API, prints raw JSON responses.
Tests:
  1. Service online check (/api/tags)
  2. Embedding model (/api/embeddings)
  3. Chat model (/api/chat) -- with file content query
  4. OpenAI-compatible API (/v1/chat/completions)

Usage:
  cd <project_root>
  python tests/test_ollama_raw.py

Prerequisites:
  - Ollama service running (ollama serve)
  - Models pulled: ollama pull bge-m3 && ollama pull qwen2.5:7b
"""

import json
import sys
import os
import time

# Ensure project root is in sys.path for config import
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import httpx

# ---- Read config from .env / settings ----
from settings import settings

EMBEDDING_MODEL = settings.embedding_model or "bge-m3"
EMBEDDING_URL = settings.embedding_url or "http://localhost:11434"
LLM_MODEL = settings.llm_model or "qwen2.5:7b"
LLM_BASE_URL = settings.llm_base_url or "http://localhost:11434/v1"


def print_section(title: str):
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}")


def print_raw(label: str, resp: httpx.Response):
    """Print raw HTTP response."""
    print(f"\n--- {label} ---")
    print(f"  HTTP Status : {resp.status_code}")
    print(f"  URL         : {resp.url}")
    print(f"  Headers     : {dict(resp.headers)}")
    try:
        body = resp.json()
        print(f"  Body (JSON) :")
        print(json.dumps(body, ensure_ascii=False, indent=2))
    except Exception:
        print(f"  Body (text) : {resp.text[:2000]}")


def ping_ollama(base_url: str) -> bool:
    """Test if Ollama service is online (GET /)."""
    print_section("[1] Ollama Service Connectivity Check")
    try:
        with httpx.Client(timeout=10) as client:
            resp = client.get(f"{base_url}/")
            print_raw("GET /", resp)
            return resp.status_code == 200
    except httpx.ConnectError as e:
        print(f"  [FAIL] Connection refused: {e}")
        return False
    except Exception as e:
        print(f"  [FAIL] Unknown error: {e}")
        return False


def list_models(base_url: str) -> list[str]:
    """List installed Ollama models (GET /api/tags)."""
    print_section("[2] Installed Model List")
    try:
        with httpx.Client(timeout=10) as client:
            resp = client.get(f"{base_url}/api/tags")
            print_raw("GET /api/tags", resp)
            data = resp.json()
            models = [m["name"] for m in data.get("models", [])]
            print(f"\n  => {len(models)} model(s): {models}")
            return models
    except Exception as e:
        print(f"  [FAIL] Cannot list models: {e}")
        return []


def test_embedding(base_url: str, model: str) -> dict | None:
    """Test Embedding API (POST /api/embeddings).

    This is the core dependency of the project's RAG pipeline
    (DualChromaDB uses OllamaEmbeddings).
    """
    print_section("[3] Embedding Model Test")

    test_texts = [
        "POST /api/login, params: username/password, returns JWT token",
        "GET /api/orders?page=1&size=20, requires Bearer token auth",
    ]

    for i, text in enumerate(test_texts, 1):
        print(f"\n  >>> Test text {i}: {text[:60]}...")
        try:
            with httpx.Client(timeout=30) as client:
                resp = client.post(
                    f"{base_url}/api/embeddings",
                    json={"model": model, "prompt": text},
                )
                print_raw(f"POST /api/embeddings (text {i})", resp)
                if resp.status_code != 200:
                    print(f"  [FAIL] Embedding request failed (HTTP {resp.status_code})")
                    return None
                data = resp.json()
                vec = data.get("embedding", [])
                print(f"  [OK] Vector dimension: {len(vec)}, first 5 values: {vec[:5]}")
        except Exception as e:
            print(f"  [FAIL] Embedding exception: {e}")
            return None

    return {"status": "ok"}


def test_chat_completion(base_url: str, model: str) -> dict | None:
    """Test Chat API (POST /api/chat) with file content query.

    Uses Ollama's native /api/chat endpoint.
    """
    print_section("[4] Chat Model Test (native /api/chat)")

    # Simulate an API definition file
    fake_file_content = """
    API Name: user_register
    Method: POST
    Path: /api/v1/register
    Parameters:
      - username (string, required): username, 3-20 chars
      - password (string, required): password, 8-32 chars, must contain upper/lower/digit
      - email (string, required): email address
    Response:
      {"code": 0, "msg": "success", "data": {"user_id": 12345}}
    """

    prompt = f"""You are an API test engineer. Analyze this API definition file and tell me:
1. What does this API do?
2. What are the required parameters?
3. How many test cases would you suggest? (answer in English, be concise)

API file content:
{fake_file_content}"""

    print(f"\n  >>> File content sent to model:\n{fake_file_content}")
    print(f"\n  >>> Sending to model ({model}) ...")

    try:
        with httpx.Client(timeout=60) as client:
            resp = client.post(
                f"{base_url}/api/chat",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                    "options": {"temperature": 0.3},
                },
            )
            print_raw("POST /api/chat", resp)
            if resp.status_code != 200:
                print(f"  [FAIL] Chat request failed (HTTP {resp.status_code})")
                return None
            data = resp.json()
            reply = data.get("message", {}).get("content", "")
            print(f"\n  [OK] Model reply:\n{reply}")
            return data
    except Exception as e:
        print(f"  [FAIL] Chat exception: {e}")
        return None


def test_v1_chat_completion(base_url: str, model: str) -> dict | None:
    """Test OpenAI-compatible API (POST /v1/chat/completions).

    This is the actual LLM call path used by the project
    (LANGCHAIN_URL=http://localhost:11434/v1).
    """
    print_section("[5] OpenAI-compatible API Test (/v1/chat/completions)")

    # Build v1 URL
    v1_url = base_url
    if not v1_url.endswith("/v1"):
        if "/v1" in v1_url:
            v1_url = v1_url.split("/v1")[0] + "/v1"
        else:
            v1_url = v1_url.rstrip("/") + "/v1"

    prompt = "Introduce yourself in one sentence."

    print(f"\n  >>> Calling {v1_url}/chat/completions, model={model}")

    try:
        with httpx.Client(timeout=60) as client:
            resp = client.post(
                f"{v1_url}/chat/completions",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.5,
                },
            )
            print_raw("POST /v1/chat/completions", resp)
            if resp.status_code != 200:
                print(f"  [FAIL] v1 Chat request failed (HTTP {resp.status_code})")
                return None
            data = resp.json()
            choice = data.get("choices", [{}])[0]
            reply = choice.get("message", {}).get("content", "")
            print(f"\n  [OK] Model reply: {reply}")
            return data
    except Exception as e:
        print(f"  [FAIL] v1 Chat exception: {e}")
        return None


def test_model_info(base_url: str, model: str) -> None:
    """Get model details (POST /api/show)."""
    print_section("[6] Model Details: " + model)

    try:
        with httpx.Client(timeout=10) as client:
            resp = client.post(f"{base_url}/api/show", json={"name": model})
            print_raw(f"POST /api/show (model={model})", resp)
    except Exception as e:
        print(f"  [FAIL] Cannot get model info: {e}")


# ================================================================
#  Main
# ================================================================

def main():
    start_time = time.time()

    print("=" * 70)
    print("  Ollama Raw Response Test Suite")
    print(f"  Embedding Model : {EMBEDDING_MODEL}")
    print(f"  Chat Model      : {LLM_MODEL}")
    print(f"  Embedding URL   : {EMBEDDING_URL}")
    print(f"  LLM Base URL    : {LLM_BASE_URL}")
    print(f"  Time            : {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    results = {}

    # 1. Connectivity
    online = ping_ollama(EMBEDDING_URL)
    results["connectivity"] = "[OK] Online" if online else "[FAIL] Offline"
    if not online:
        print("\n" + "=" * 70)
        print("  [FAIL] Ollama service is not reachable. Check:")
        print("     1. Is 'ollama serve' running?")
        print(f"     2. Is the URL correct? (current: {EMBEDDING_URL})")
        print("=" * 70)
        return

    # 2. Model list
    models = list_models(EMBEDDING_URL)
    results["installed_models"] = len(models)

    # 3. Embedding test
    emb_result = test_embedding(EMBEDDING_URL, EMBEDDING_MODEL)
    results["embedding"] = "[OK] Working" if emb_result else "[FAIL] Broken"

    # 4. Chat (native)
    chat_result = test_chat_completion(EMBEDDING_URL, LLM_MODEL)
    results["chat_native"] = "[OK] Working" if chat_result else "[FAIL] Broken"

    # 5. v1/chat/completions (OpenAI-compatible)
    v1_result = test_v1_chat_completion(EMBEDDING_URL, LLM_MODEL)
    results["chat_v1"] = "[OK] Working" if v1_result else "[FAIL] Broken"

    # 6. Model details
    test_model_info(EMBEDDING_URL, LLM_MODEL)
    test_model_info(EMBEDDING_URL, EMBEDDING_MODEL)

    # Summary
    elapsed = time.time() - start_time
    print_section("SUMMARY")
    for key, val in results.items():
        print(f"  {key:.<40s} {val}")
    print(f"  {'Total time':.<40s} {elapsed:.2f}s")
    print(f"\n{'='*70}")
    print("  Test complete! Raw JSON responses are shown above.")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()

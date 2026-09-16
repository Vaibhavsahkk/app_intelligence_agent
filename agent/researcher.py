"""Composio App Research Agent - AI-powered API documentation researcher."""
import os
import json
import time
import logging
import sys
import re
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from google import genai

# Setup path for config import
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (
    GEMINI_API_KEY, GEMINI_MODEL, GEMINI_FALLBACK_MODELS, RESEARCH_PROMPT,
    BATCH_SIZE, DELAY_BETWEEN_BATCHES, DELAY_BETWEEN_REQUESTS,
    MAX_RETRIES, RETRY_BACKOFF_BASE, DATA_DIR
)

logger = logging.getLogger("composio_agent.researcher")

# Configure Gemini client (new google.genai SDK)
client = genai.Client(api_key=GEMINI_API_KEY)


def get_text_from_html(html: str) -> str:
    """Extract readable text from HTML, removing scripts and styles."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.extract()
    text = soup.get_text(separator=" ")
    lines = (line.strip() for line in text.splitlines())
    chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
    text = "\n".join(chunk for chunk in chunks if chunk)
    return text[:8000]


def extract_json_from_response(text: str) -> dict:
    """Parse JSON from Gemini response, handling markdown code fences."""
    text = text.strip()
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to find JSON object in the text
        match = re.search(r'\{[\s\S]*\}', text)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        logger.error(f"Failed to parse JSON from response (length={len(text)})")
        return {}


def fetch_url_content(url: str) -> str:
    """Fetch URL content with retries and exponential backoff."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    for attempt in range(MAX_RETRIES):
        try:
            response = httpx.get(url, timeout=10.0, follow_redirects=True, headers=headers)
            response.raise_for_status()
            return response.text
        except httpx.HTTPStatusError as e:
            logger.warning(f"HTTP {e.response.status_code} for {url} (attempt {attempt + 1})")
            # If 4xx client error (e.g. 404 Not Found, 403 Forbidden), don't retry this URL
            if 400 <= e.response.status_code < 500:
                break
        except Exception as e:
            logger.warning(f"Fetch failed for {url} (attempt {attempt + 1}): {type(e).__name__}: {e}")
        if attempt < MAX_RETRIES - 1:
            time.sleep(RETRY_BACKOFF_BASE ** attempt)
    return ""


def build_docs_urls(app: dict) -> list:
    """Generate candidate documentation URLs from app hints."""
    hint_url = app.get("hint_url", "")
    if not hint_url:
        return []

    # Normalize hint_url
    if not hint_url.startswith("http"):
        hint_url = f"https://{hint_url}"

    parsed = urlparse(hint_url)
    domain = parsed.netloc or parsed.path.split("/")[0]
    base_domain = ".".join(domain.split(".")[-2:]) if "." in domain else domain

    is_dedicated_doc_url = any(x in hint_url.lower() for x in ["/docs", "/api", "/developer", "developer.", "developers.", "docs.", "api."])

    if is_dedicated_doc_url:
        urls = [hint_url]
    else:
        urls = [
            f"https://developer.{base_domain}",
            f"https://developers.{base_domain}",
            f"https://docs.{base_domain}",
            f"https://api.{base_domain}",
            f"https://{domain}/docs",
            f"https://{domain}/api",
            f"https://{domain}/developer",
            hint_url
        ]
    return urls


def research_single_app(app: dict) -> dict:
    """Research a single app by fetching docs and extracting structured data via Gemini."""
    app_id = app.get("id", 0)
    app_name = app.get("name", "Unknown")
    category = app.get("category", "Unknown")
    hint_url = app.get("hint_url", "")
    hints = app.get("hints", "")

    logger.info(f"[{app_id}/100] Researching: {app_name} ({hint_url})")

    # Try to fetch documentation
    docs_text = ""
    docs_fetched = False
    fetched_url = ""

    for url in build_docs_urls(app):
        html = fetch_url_content(url)
        if html and len(html) > 500:  # Minimum meaningful content
            docs_text = get_text_from_html(html)
            if len(docs_text) > 200:  # Ensure we got real content, not just nav
                docs_fetched = True
                fetched_url = url
                logger.info(f"  Docs fetched from {url} ({len(docs_text)} chars)")
                break

    # Determine confidence based on docs availability
    if docs_fetched and len(docs_text) > 1000:
        confidence = "HIGH"
    elif docs_fetched:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"
        docs_text = f"No documentation page could be fetched. Use your training knowledge about {app_name} to answer. The hint URL is {hint_url}. Mark all answers as based on training knowledge."
        logger.warning(f"  No docs found for {app_name}, using LLM training knowledge")

    # Build the prompt
    prompt = RESEARCH_PROMPT.format(
        app_name=app_name,
        hint_url=hint_url,
        hints=hints if hints else "None",
        category=category
    )
    prompt += f"\n\nDocumentation content to analyze:\n{docs_text}"

    # Call Gemini with retries
    for attempt in range(MAX_RETRIES):
        model_to_use = GEMINI_FALLBACK_MODELS[attempt % len(GEMINI_FALLBACK_MODELS)]
        try:
            response = client.models.generate_content(
                model=model_to_use,
                contents=prompt
            )
            result = extract_json_from_response(response.text)
            if result:
                result["app_id"] = app_id
                result["app_name"] = app_name
                result["confidence"] = confidence
                result["docs_fetched"] = docs_fetched
                result["fetched_url"] = fetched_url
                logger.info(f"  Successfully extracted data for {app_name} using {model_to_use} (confidence: {confidence})")
                return result
            else:
                logger.warning(f"  Empty result for {app_name}, retrying...")
        except Exception as e:
            logger.error(f"  Gemini API error for {app_name} with {model_to_use} (attempt {attempt + 1}): {type(e).__name__}: {e}")
        if attempt < MAX_RETRIES - 1:
            wait_time = RETRY_BACKOFF_BASE ** (attempt + 1)
            logger.info(f"  Waiting {wait_time}s before retry...")
            time.sleep(wait_time)

    # Final fallback: return minimal data
    logger.error(f"  FAILED to process {app_name} after {MAX_RETRIES} attempts")
    return {
        "app_id": app_id,
        "app_name": app_name,
        "category": category,
        "description": "Failed to process",
        "auth_methods": ["Unknown"],
        "self_serve": "Unknown",
        "self_serve_detail": "Agent could not determine",
        "api_type": "Unknown",
        "api_breadth": "Unknown",
        "has_mcp": False,
        "mcp_link": None,
        "buildability": "Unknown",
        "main_blocker": "Agent processing failed",
        "evidence_urls": [hint_url] if hint_url else [],
        "sdk_languages": ["Unknown"],
        "rate_limits": "Unknown",
        "has_webhooks": False,
        "has_sandbox": False,
        "response_format": "Unknown",
        "confidence": "FAILED",
        "docs_fetched": False,
        "fetched_url": "",
        "error": "All attempts failed"
    }


def run_research(app_ids: list = None) -> list:
    """Run research across all apps (or a subset by ID).

    Args:
        app_ids: Optional list of app IDs to process. If None, processes all 100.

    Returns:
        List of research result dicts.
    """
    apps_list_path = os.path.join(DATA_DIR, "apps_list.json")
    results_path = os.path.join(DATA_DIR, "pass1_results.json")

    # Load apps list
    try:
        with open(apps_list_path, "r", encoding="utf-8") as f:
            all_apps = json.load(f)
    except FileNotFoundError:
        logger.error(f"Apps list not found at {apps_list_path}")
        return []
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in apps list: {e}")
        return []

    # Filter by IDs if specified
    if app_ids:
        apps = [a for a in all_apps if a["id"] in app_ids]
        logger.info(f"Processing {len(apps)} selected apps out of {len(all_apps)}")
    else:
        apps = all_apps
        logger.info(f"Processing all {len(apps)} apps")

    # Load any existing results to resume from
    existing_results = []
    processed_ids = set()
    if os.path.exists(results_path):
        try:
            with open(results_path, "r", encoding="utf-8") as f:
                existing_results = json.load(f)
                processed_ids = {r.get("app_id") for r in existing_results}
                logger.info(f"Resuming: {len(existing_results)} apps already processed")
        except Exception:
            pass

    results = list(existing_results)
    remaining = [a for a in apps if a["id"] not in processed_ids]

    if not remaining:
        logger.info("All apps already processed")
        return results

    logger.info(f"Remaining apps to process: {len(remaining)}")

    # Process in batches
    for batch_start in range(0, len(remaining), BATCH_SIZE):
        batch = remaining[batch_start:batch_start + BATCH_SIZE]
        batch_num = batch_start // BATCH_SIZE + 1
        total_batches = (len(remaining) - 1) // BATCH_SIZE + 1
        logger.info(f"=== Batch {batch_num}/{total_batches} ===")

        for app in batch:
            result = research_single_app(app)
            results.append(result)
            time.sleep(DELAY_BETWEEN_REQUESTS)

        # Save intermediate results after each batch
        os.makedirs(os.path.dirname(results_path), exist_ok=True)
        with open(results_path, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        logger.info(f"Saved {len(results)} results to {results_path}")

        # Delay between batches (except last)
        if batch_start + BATCH_SIZE < len(remaining):
            logger.info(f"Waiting {DELAY_BETWEEN_BATCHES}s before next batch...")
            time.sleep(DELAY_BETWEEN_BATCHES)

    # Final summary
    success_count = sum(1 for r in results if r.get("confidence") != "FAILED")
    failed_count = sum(1 for r in results if r.get("confidence") == "FAILED")
    low_conf = sum(1 for r in results if r.get("confidence") == "LOW")
    logger.info(f"=== Research Complete ===")
    logger.info(f"  Successful: {success_count}")
    logger.info(f"  Failed: {failed_count}")
    logger.info(f"  Low confidence: {low_conf}")

    return results


if __name__ == "__main__":
    results = run_research()
    print(f"\nResearch complete. {len(results)} apps processed.")

import os
import logging

# API Configuration
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = "gemini-3.5-flash-lite"
GEMINI_FALLBACK_MODELS = ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-3.8-flash"]

# Rate Limiting
BATCH_SIZE = 10
DELAY_BETWEEN_BATCHES = 3  # seconds
DELAY_BETWEEN_REQUESTS = 1  # seconds
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2  # exponential backoff base

# Verification
VERIFICATION_SAMPLE_SIZE = 30  # 3 per category
ACCURACY_THRESHOLD = 0.85  # target accuracy

# Paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
LOGS_DIR = os.path.join(BASE_DIR, "logs")

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(LOGS_DIR, "agent_run.log"), mode="w", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("composio_agent")

# Research prompt template
RESEARCH_PROMPT = """
You are a technical API researcher for Composio, a platform that turns apps into tools AI agents can call.

Analyze the following developer documentation page content for the app "{app_name}" ({hint_url}).
Additional context/hints: {hints}

Extract the following information. Be precise and evidence-based. If you cannot determine a field from the provided content, say "Not found in docs" rather than guessing.

1. category: The app category (use exactly: "{category}")
2. description: What the app does, in one line (max 15 words)
3. auth_methods: List ALL authentication methods supported. Choose from: OAuth2, API Key, Basic Auth, Bearer Token, JWT, Session, SAML, Other. If multiple, list all.
4. self_serve: Is API access self-serve? Answer with one of: "Self-serve free", "Self-serve paid", "Free trial then paid", "Admin approval required", "Partner/contact-sales only", "Open source / no auth needed". Then explain briefly.
5. api_type: What type of API? (REST, GraphQL, SOAP, WebSocket, gRPC, or combination). Estimate the breadth: "Narrow (< 20 endpoints)", "Medium (20-100 endpoints)", "Broad (100+ endpoints)".
6. has_mcp: Does this app have an existing MCP (Model Context Protocol) server? Answer true or false. If true, provide the link.
7. buildability: Could this be turned into an agent toolkit or agent-callable skills today? Answer: "Ready" (no blockers), "Minor work" (small issues), "Significant work" (major blockers), or "Not feasible" (fundamental blockers). State the main blocker if any.
8. evidence_urls: List the specific documentation URLs that support your answers.
9. sdk_languages: What official SDK/client libraries exist? List programming languages (e.g., Python, JavaScript, Go, Ruby, Java, etc.). Say "None found" if no official SDKs.
10. rate_limits: What are the API rate limits? (e.g., "100 req/min", "1000 req/day", "Varies by plan", "Not documented")
11. has_webhooks: Does the API support webhooks? true or false.
12. has_sandbox: Is there a sandbox/test environment? true or false.
13. response_format: What data format does the API return? (JSON, XML, CSV, or combination)

Respond in this exact JSON format (no markdown, no code fences, just raw JSON):
{{
  "category": "...",
  "description": "...",
  "auth_methods": ["..."],
  "self_serve": "...",
  "self_serve_detail": "...",
  "api_type": "...",
  "api_breadth": "...",
  "has_mcp": false,
  "mcp_link": null,
  "buildability": "...",
  "main_blocker": "...",
  "evidence_urls": ["..."],
  "sdk_languages": ["..."],
  "rate_limits": "...",
  "has_webhooks": false,
  "has_sandbox": false,
  "response_format": "JSON"
}}
"""

VERIFICATION_PROMPT = """
You are a verification agent. You have been given a developer documentation page for "{app_name}".
A previous research agent produced these findings:
{previous_findings}

Your job: independently verify each field by reading the documentation content below.
For each field, state whether the previous finding is CORRECT, INCORRECT, or UNCERTAIN.
If INCORRECT, provide the correct answer with evidence.

Documentation content:
{doc_content}

Respond in this exact JSON format (no markdown, no code fences, just raw JSON):
{{
  "auth_methods": {{"verdict": "CORRECT/INCORRECT/UNCERTAIN", "correct_value": [...], "evidence": "..."}},
  "self_serve": {{"verdict": "CORRECT/INCORRECT/UNCERTAIN", "correct_value": "...", "evidence": "..."}},
  "api_type": {{"verdict": "CORRECT/INCORRECT/UNCERTAIN", "correct_value": "...", "evidence": "..."}},
  "has_mcp": {{"verdict": "CORRECT/INCORRECT/UNCERTAIN", "correct_value": false, "evidence": "..."}},
  "buildability": {{"verdict": "CORRECT/INCORRECT/UNCERTAIN", "correct_value": "...", "evidence": "..."}},
  "has_webhooks": {{"verdict": "CORRECT/INCORRECT/UNCERTAIN", "correct_value": false, "evidence": "..."}}
}}
"""

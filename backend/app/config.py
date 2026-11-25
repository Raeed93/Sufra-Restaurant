"""Settings loaded from environment variables.

No silent fallbacks for the model name or API key: if they are missing,
the app fails at startup instead of quietly calling a deprecated model.
"""
import os

from dotenv import load_dotenv

load_dotenv()


def _required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://foodics:foodics@localhost:5442/foodics",
)


def groq_api_key() -> str:
    return _required("GROQ_API_KEY")


def groq_model() -> str:
    return _required("GROQ_MODEL")


# Safety cap on tool-call rounds per user message, so a confused model
# can't loop forever and burn tokens.
MAX_AGENT_STEPS = int(os.getenv("MAX_AGENT_STEPS", "6"))

# Only sent when set: gpt-oss accepts low/medium/high; some other models don't support it.
REASONING_EFFORT = os.getenv("REASONING_EFFORT") or None

# --- Auth (Day 2) ---
def jwt_secret() -> str:
    secret = _required("JWT_SECRET")
    if len(secret) < 32:
        raise RuntimeError("JWT_SECRET must be at least 32 characters")
    return secret


JWT_EXPIRE_HOURS = int(os.getenv("JWT_EXPIRE_HOURS", "168"))  # 7 days
# True in production (HTTPS only). False locally because localhost is plain HTTP.
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"
# Only the most recent messages of a conversation are sent to the model.
MAX_HISTORY_MESSAGES = int(os.getenv("MAX_HISTORY_MESSAGES", "40"))

# --- Safety layer (Part 2) ---
# "on":  recommend tool + code checks (allergy net, item checks, reply check).
# "off": the Part 1 baseline, model-only. Kept so the evals can compare both.
SAFETY_MODE = os.getenv("SAFETY_MODE", "on").lower()

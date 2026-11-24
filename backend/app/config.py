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
    "postgresql+psycopg://foodics:foodics@localhost:5432/foodics",
)


def groq_api_key() -> str:
    return _required("GROQ_API_KEY")


def groq_model() -> str:
    return _required("GROQ_MODEL")


# Safety cap on tool-call rounds per user message, so a confused model
# can't loop forever and burn tokens.
MAX_AGENT_STEPS = int(os.getenv("MAX_AGENT_STEPS", "6"))

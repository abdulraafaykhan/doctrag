"""Never load local secrets, including during test collection."""
import os
from app.config import Settings, get_settings
Settings.model_config["env_file"] = None
os.environ.update(MONGODB_URI="mongodb://localhost:27017", BANK_MONGODB_URI="", GROQ_API_KEY="test", GEMINI_API_KEY="test", JWT_SECRET="test-only-secret-with-at-least-32-characters")
get_settings.cache_clear()

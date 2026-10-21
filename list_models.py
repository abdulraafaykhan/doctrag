"""Print the Groq model IDs that YOUR API key can use:

    uv run python list_models.py
"""
from groq import Groq

from app.config import get_settings

client = Groq(api_key=get_settings().groq_api_key)
for m in sorted(client.models.list().data, key=lambda m: m.id):
    print(m.id)
import os
import sys
import anthropic

api_key = os.environ.get("ANTHROPIC_API_KEY")
client = anthropic.Anthropic(api_key=api_key)

model = "claude-2.1"
print(f"Testing connectivity with {model}...")

try:
    client.messages.create(
        model=model,
        max_tokens=10,
        messages=[{"role": "user", "content": "Hi"}]
    )
    print("✅ API Key is VALID. Legacy model works.")
except Exception as e:
    print(f"❌ API Key/Connection Issue: {e}")

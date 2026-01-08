import os
import sys
import anthropic

api_key = os.environ.get("ANTHROPIC_API_KEY")
client = anthropic.Anthropic(api_key=api_key)

model = "claude-3-5-sonnet-20240620"
print(f"Testing connectivity with {model}...")

try:
    client.messages.create(
        model=model,
        max_tokens=10,
        messages=[{"role": "user", "content": "Hi"}]
    )
    print("✅ API Key is VALID. Known model works.")
except Exception as e:
    print(f"❌ API Key/Connection Issue: {e}")

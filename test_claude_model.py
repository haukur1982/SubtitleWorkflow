import os
import sys
try:
    import anthropic
except ImportError:
    print("❌ anthropic not installed")
    sys.exit(1)

api_key = os.environ.get("ANTHROPIC_API_KEY")
if not api_key:
    print("❌ ANTHROPIC_API_KEY not set")
    sys.exit(1)

client = anthropic.Anthropic(api_key=api_key)
model = "claude-opus-4.5"

print(f"Testing model: {model}...")

try:
    message = client.messages.create(
        model=model,
        max_tokens=10,
        messages=[{"role": "user", "content": "Hello"}]
    )
    print("✅ Success! Model exists.")
    print(message.content)
except anthropic.BadRequestError as e:
    print(f"❌ BadRequestError: {e}")
    print("The model name is likely invalid.")
except Exception as e:
    print(f"❌ Error: {e}")

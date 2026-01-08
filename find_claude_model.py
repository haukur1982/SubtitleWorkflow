import os
import sys
try:
    import anthropic
except ImportError:
    print("❌ anthropic not installed")
    sys.exit(1)

api_key = os.environ.get("ANTHROPIC_API_KEY")
client = anthropic.Anthropic(api_key=api_key)

candidates = [
    "claude-4.5-opus",
    "claude-4-opus",
    "claude-opus-4",
    "claude-opus-4.5-preview",
    "claude-3-opus-20240229", # Baseline check
    "claude-3.5-opus",
]

print("🔍 Testing candidates...")
for model in candidates:
    try:
        print(f"Testing {model}...", end=" ")
        client.messages.create(
            model=model,
            max_tokens=1,
            messages=[{"role": "user", "content": "Hi"}]
        )
        print("✅ VALID!")
        print(f"\n🎉 FOUND VALID MODEL: {model}")
        sys.exit(0)
    except anthropic.BadRequestError as e:
        print(f"❌ (400) {e.body.get('error', {}).get('message', 'Unknown error')}")
    except anthropic.NotFoundError as e:
        print(f"❌ (404) {e.body.get('error', {}).get('message', 'Not found')}")
    except Exception as e:
        print(f"❌ Error: {e}")

print("\n❌ No valid Opus 4.5 variant found.")

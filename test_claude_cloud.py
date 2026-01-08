import os
import sys
import anthropic

# 1. Get API Key
api_key = os.environ.get("ANTHROPIC_API_KEY")
if not api_key:
    print("❌ ANTHROPIC_API_KEY missing")
    sys.exit(1)

# 2. Init Client
client = anthropic.Anthropic(api_key=api_key)

# 3. Define EXACT Model from your curl test
model_id = "claude-opus-4-5-20251101"

print(f"📡 Testing Cloud API Connectivity...")
print(f"   Model: {model_id}")
print(f"   Library: anthropic v{anthropic.__version__}")

try:
    # 4. Make Request
    response = client.messages.create(
        model=model_id,
        max_tokens=50,
        messages=[{"role": "user", "content": "Ping. Reply with 'Pong'."}]
    )
    
    # 5. Verify Output
    content = response.content[0].text
    print(f"\n✅ SUCCESS! Connection Established.")
    print(f"   Response: {content}")
    
except anthropic.BadRequestError as e:
    print(f"\n❌ FAILED: Bad Request (Likely invalid Model ID)")
    print(f"   Error: {e}")
    sys.exit(1)
except anthropic.AuthenticationError:
    print(f"\n❌ FAILED: Authentication Error (Check API Key)")
    sys.exit(1)
except Exception as e:
    print(f"\n❌ FAILED: Unexpected Error")
    print(f"   {e}")
    sys.exit(1)

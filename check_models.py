from openai import OpenAI

API_KEY = "bc6e55da-22af-438c-ae2c-beb5587e7e43" # Your Key
BASE_URL = "https://api.sambanova.ai/v1"

client = OpenAI(api_key=API_KEY, base_url=BASE_URL)

try:
    print("Fetching available models...")
    models = client.models.list()
    print("\n--- SUCCESS! HERE ARE YOUR AVAILABLE MODELS ---")
    for m in models.data:
        print(f"Model ID: {m.id}")
except Exception as e:
    print(f"Error: {e}")

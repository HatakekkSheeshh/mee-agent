import os
import sys
import json
import urllib.request
import dotenv


dotenv.load_dotenv()  # Load environment variables from .env file

API_KEY = os.getenv("TAVILY_API_KEY")
if not API_KEY:
    print("Error: TAVILY_API_KEY environment variable not set.")
    sys.exit(1)

if len(sys.argv) < 2:
    print("Usage: tavily-search <query>")
    sys.exit(1)

query = " ".join(sys.argv[1:])


# Thiết lập API Request theo chuẩn của Tavily
url = "https://api.tavily.com/search"
headers = {
    "Content-Type": "application/json"
}
data = {
    "api_key": API_KEY,
    "query": query,
    "search_depth": "advanced", # "basic" hoặc "advanced" tùy nhu cầu sâu hay nhanh
    "include_answer": True      # Bảo Tavily tự tóm tắt một câu trả lời ngắn kèm theo dữ liệu thô
}

req = urllib.request.Request(url, data=json.dumps(data).encode('utf-8'), headers=headers, method='POST')

try:
    with urllib.request.urlopen(req) as response:
        res_data = json.loads(response.read().decode('utf-8'))
        
        # 1. In ra câu trả lời tóm tắt của Tavily (nếu có)
        if res_data.get("answer"):
            print(f"### Summary:\n{res_data['answer']}\n")
            
        # 2. In ra các nguồn và snippet chất lượng cho Claude tự đọc
        print("### Search Results:")
        for idx, result in enumerate(res_data.get("results", []), 1):
            print(f"{idx}. **[{result['title']}]({result['url']})**")
            print(f"   *Snippet:* {result['content']}\n")
            
except Exception as e:
    print(f"Error executing Tavily search: {e}")
    sys.exit(1)
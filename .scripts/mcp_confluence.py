import requests
from requests.auth import HTTPBasicAuth

# ===== 設定 =====
BASE_URL = "https://your-domain.atlassian.net"
EMAIL = "your-email@example.com"
API_TOKEN = "your_api_token"

# ===== エンドポイント =====
url = f"{BASE_URL}/wiki/rest/api/space"

# ===== リクエスト =====
response = requests.get(
    url,
    auth=HTTPBasicAuth(EMAIL, API_TOKEN),
    headers={
        "Accept": "application/json"
    },
    params={
        "limit": 50  # 取得件数（デフォルトより増やす）
    }
)

# ===== 結果処理 =====
if response.status_code != 200:
    print(f"Error: {response.status_code}")
    print(response.text)
    exit(1)

data = response.json()

# スペース一覧表示
for space in data.get("results", []):
    print(f"{space['key']} : {space['name']}")





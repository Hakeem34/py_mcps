import os
import re
import sys
import requests
from requests.auth import HTTPBasicAuth
from mcp.server.fastmcp import FastMCP


g_space_keys = {}

# ===== 設定 =====
g_opt_url   = ""
g_opt_user  = ""
g_opt_pass  = ""
g_opt_token = ""

# FastMCPのインスタンスを作成
mcp = FastMCP()


def read_credentials():
    """
    認証情報を読み取ります。
    """
    global g_opt_url
    global g_opt_user
    global g_opt_pass
    global g_opt_token

    try:
        path = sys._MEIPASS
    except AttributeError:
        path = os.path.abspath(__file__)

    path = path.replace(os.path.basename(path), "credentials.txt")
    print(f"Reading credentials from path: {path}", file=sys.stderr)
    if not os.path.exists(path):
        print(f"Credentials file not found", file=sys.stderr)
        return

    with open(path, "r") as f:
        lines = f.readlines()
        for line in lines:
            if match := re.match(r"confluence_url\s*:\s*(\S+)", line):
                g_opt_url = match.group(1)
                if g_opt_url.endswith('.atlassian.net/'):
                    g_opt_url += 'wiki'
            elif match := re.match(r"confluence_user\s*:\s*(\S+)", line):
                g_opt_user = match.group(1)
            elif match := re.match(r"confluence_pass\s*:\s*(\S+)", line):
                g_opt_pass = match.group(1)
            elif match := re.match(r"confluence_key\s*:\s*(\S+)", line):
                g_opt_token = match.group(1)

    return

@mcp.tool()
def get_content_list(space_name) -> str:
    """
    """
    url = f"{g_opt_url}/rest/api/content"
    space_key = g_space_keys[space_name]
    response = requests.get(
        url,
        auth=HTTPBasicAuth(g_opt_user, g_opt_token),
        headers={"Accept": "application/json"},
        params={
            "spaceKey": space_key,
            "limit": 50,
 #          "type": "page"   # page / blogpost など
        }
    )

    if response.status_code != 200:
        print(f"Error: {response.status_code}")
        print(response.text)
        return

    data = response.json()

    print(f"=== Contents in Space: {space_name} ===")

    for content in data.get("results", []):
        print(f"[{content['id']}] {content['title']}")
#       print(content)


@mcp.tool()
def get_space_list() -> str:
    """
    Confluenceのスペースのリストを取得します
    """
    global g_space_keys
    space = f"{g_opt_url}/rest/api/space"

    # ===== リクエスト =====
    response = requests.get(
        space,
        auth=HTTPBasicAuth(g_opt_user, g_opt_token),
        headers={"Accept": "application/json"},
        params={"limit": 50 }          # 取得件数（デフォルトより増やす）
    )

    # ===== 結果処理 =====
    if response.status_code != 200:
        print(f"Error: {response.status_code}", file=sys.stderr)
        print(response.text, file=sys.stderr)
        return f"Error: {response.status_code}"

    data = response.json()
#   print(f"data:\n{data}\n\n", file=sys.stderr)  # デバッグ用に全データを表示

    # スペース一覧表示
    result = ""
    for space in data.get("results", []):
        print(f"{space['key']} : {space['name']}", file=sys.stderr)
        result += f"{space['key']} : {space['name']}" + "\n"
        g_space_keys[space['name']] = space['key']
    return result
    
    
def test_v2_api():
    url = f"{g_opt_url}/api/v2/pages"

    response = requests.get(
        url,
        auth=HTTPBasicAuth(g_opt_user, g_opt_token),
        headers={
            "Accept": "application/json"
        }
    )

    print("status:", response.status_code)
    print(response.text[:500])

def main():
    read_credentials()
    test_v2_api()
    get_space_list()

    for name in g_space_keys:
        get_content_list(name)
    mcp.run()

if __name__ == "__main__":
    main()


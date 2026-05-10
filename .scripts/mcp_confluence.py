import os
import re
import sys
import requests
from requests.auth import HTTPBasicAuth


# ===== 設定 =====
g_opt_url   = ""
g_opt_user  = ""
g_opt_pass  = ""
g_opt_token = ""



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
            elif match := re.match(r"confluence_user\s*:\s*(\S+)", line):
                g_opt_user = match.group(1)
            elif match := re.match(r"confluence_pass\s*:\s*(\S+)", line):
                g_opt_pass = match.group(1)
            elif match := re.match(r"confluence_key\s*:\s*(\S+)", line):
                g_opt_token = match.group(1)

    return


def get_space_list():
    space = f"{g_opt_url}/wiki/rest/api/space"

    # ===== リクエスト =====
    response = requests.get(
        space,
        auth=HTTPBasicAuth(g_opt_user, g_opt_token),
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
#   print(f"data:\n{data}\n\n")  # デバッグ用に全データを表示

    # スペース一覧表示
    for space in data.get("results", []):
        print(f"{space['key']} : {space['name']}")


def main():
    read_credentials()
    get_space_list()
    pass

if __name__ == "__main__":
    main()


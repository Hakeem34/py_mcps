import sys
from mcp.server.fastmcp import FastMCP
from redminelib import Redmine

# 1. 接続設定（環境に合わせて書き換えてください）
REDMINE_URL = 'http://localhost:3000/'
API_KEY = '64cd7998a6baa4d88a1719b821129d2ccca5ba1a'

# MCPサーバーのインスタンス作成
mcp = FastMCP("RedmineExplorer")

# Redmineクライアントの初期化
redmine = Redmine(REDMINE_URL, key=API_KEY)

@mcp.tool()
def list_projects() -> str:
    """
    プロジェクトの一覧を取得します。
    """
    try:
        projects = redmine.project.all()
        if not projects:
            return "プロジェクトは見つかりませんでした。"

        result = []
        for project in projects:
            result.append(f"{project.name} ({project.identifier})")
        return "\n".join(result)
    except Exception as e:
        return f"プロジェクトの取得に失敗しました: {str(e)}"

@mcp.tool()
def list_my_issues(limit: int = 5) -> str:
    """
    自分に割り当てられたチケットを最新順に取得します。
    """
    try:
        # ログインユーザーにアサインされたチケットを取得
        issues = redmine.issue.filter(assigned_to_id='me', sort='updated_on:desc', limit=limit)
        
        if not issues:
            return "アサインされたチケットは見つかりませんでした。"

        result = []
        for issue in issues:
            result.append(f"#{issue.id}: {issue.subject} (ステータス: {issue.status.name})")
        
        return "\n".join(result)
    except Exception as e:
        return f"エラーが発生しました: {str(e)}"

@mcp.tool()
def get_issue_detail(issue_id: int) -> str:
    """
    指定されたチケットIDの詳細情報を取得します。
    """
    try:
        issue = redmine.issue.get(issue_id)
        detail = (
            f"題名: {issue.subject}\n"
            f"作成日: {issue.created_on}\n"
            f"更新日: {issue.updated_on}\n"
            f"予定工数: {getattr(issue, 'estimated_hours', '未設定')}時間\n"
            f"作業時間: {getattr(issue, 'spent_hours', '未設定')}時間\n"
            f"説明: {issue.description}\n"
            f"ステータス: {issue.status.name}\n"
            f"進捗率: {issue.done_ratio}%\n"
            f"優先度: {issue.priority.name}\n"
            f"期限: {getattr(issue, 'due_date', '未設定')}"
        )
        return detail
    except Exception as e:
        return f"チケット #{issue_id} の取得に失敗しました: {str(e)}"

if __name__ == "__main__":
    mcp.run()


import os
import re
import sys
import subprocess
from pathlib import Path
from mcp.server.fastmcp import FastMCP

RE_REVISION = re.compile(r"^r\d+")
RE_LOG_SEPARATOR = re.compile(r"^-{72}$")

g_repo_url = ""
g_working_url = ""

# FastMCPのインスタンスを作成
mcp = FastMCP()

def decode_raw_output(raw_output: bytes) -> str:
    """
    コマンドの生の出力をUTF-8にデコードします。
    デコードに失敗した場合は、エラーメッセージを返します。
    """
    try:
        return raw_output.decode('utf-8')
    except UnicodeDecodeError as e:
        return raw_output.decode('cp932', errors='replace')

def run_command(command: str) -> str:
    """
    コマンドを実行して、その出力を返します。
    """
    try:
        result = subprocess.run(command, shell=True, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=False)
        return decode_raw_output(result.stdout)
    except subprocess.CalledProcessError as e:
        return f"コマンドの実行に失敗しました: {decode_raw_output(e.stderr)}"

def try_command(command: str) -> str:
    """
    コマンドを実行して、その終了コードのみを返します。
    """
    try:
        result = subprocess.run(command, shell=True, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=False)
        return result.returncode
    except subprocess.CalledProcessError as e:
        return 1

def get_repo_url_internal() -> str:
    """
    リポジトリURLを取得します。
    """
    global g_repo_url
    global g_working_url
    svn_info = run_command("svn info")
    # svn infoの出力からリポジトリURLを抽出
    for line in svn_info.split('\n'):
        if line.startswith("URL:"):
            g_working_url = line.split("URL:")[1].strip()
        elif line.startswith("Repository Root:"):
            g_repo_url = line.split("Repository Root:")[1].strip()

    print(f"Repository URL: {g_repo_url}", file=sys.stderr)
    return g_repo_url

def get_svn_log_internal(path: str, optional_args: str = "") -> str:
    try:
        if optional_args:
            return run_command(f"svn log {optional_args} {path}")
        return run_command(f"svn log {path}")
    except Exception as e:
        return f"SVN({path})ログの取得に失敗しました: {str(e)}"

def convert_relative_path(path: str) -> str:
    """
    1.リポジトリからの相対パスを完全なURLに変換します。
    2.1が存在しない場合は、作業コピーのURLを付加した完全なURLに変換します。
    3.1,2の両方が存在しない場合は、ローカルファイルシステム上の相対パスとして扱います。
    いずれも存在しない場合はNoneを返します。
    """
    repository_url = g_repo_url + "/" + path
    print(f"Trying repository URL: {repository_url}", file=sys.stderr)
    if try_command(f"svn info {repository_url}") == 0:
        return repository_url
    
    repository_url = g_working_url + "/" + path
    print(f"Trying working URL: {repository_url}", file=sys.stderr)
    if try_command(f"svn info {repository_url}") == 0:
        return repository_url
    
    if os.path.exists(path):
        return path

    return None

def check_url_is_directory(url: str) -> bool:
    """
    指定されたURLがディレクトリかどうかをチェックします。
    """
    try:
        result = run_command(f"svn info {url}")
        for line in result.split('\n'):
            if line.startswith("Node Kind:"):
                return "directory" in line
        return False
    except Exception as e:
        print(f"URLの情報の取得に失敗しました: {str(e)}", file=sys.stderr)
        return False

def is_safe_path(target_path):
    try:
        target_abs = Path(target_path).resolve()
        cwd = Path.cwd()
        # cwdより下位のパスであれば True、それ以外（別ドライブ含む）は False
        return (target_abs.is_relative_to(cwd)) and (cwd != target_abs)
    except (ValueError, RuntimeError):
        # ValueError: ドライブが異なる場合
        # RuntimeError: 無限ループするシンボリックリンクなど（resolve時に発生の可能性）
        return False

@mcp.tool()
def export_svn_file(relative_path: str, revision: str = "HEAD", output_path: str = "_tmp_export") -> str:
    """
    指定された相対パスをoutput_pathにエクスポートします。
    """
    if not is_safe_path(output_path):
        return f"安全でないパスが指定されました: {output_path}"

    path = convert_relative_path(relative_path)
    if check_url_is_directory(path):
        output_path = os.path.join(output_path, os.path.basename(relative_path))
    else:
        if os.path.dirname(output_path) == "":
            output_path = os.path.join(output_path, os.path.basename(relative_path))

    print(f"Exporting SVN file: {relative_path} at revision: {revision} to output path: {output_path}({os.path.dirname(output_path)})", file=sys.stderr)
    os.makedirs(os.path.dirname(output_path), exist_ok=True) if output_path else None
    print(f"makedirs called for: {relative_path} at revision: {revision} to output path: {output_path}", file=sys.stderr)
    print(f"Converted path: {path}", file=sys.stderr)
    try:
        result = run_command(f"svn export {path} -r {revision} {output_path}")
        print(f"Export result: {result}", file=sys.stderr)
        return result
    except Exception as e:
        return f"SVNファイルのエクスポートに失敗しました: {str(e)}"

@mcp.tool()
def get_svn_diff_by_revision(relative_path: str, revision1: str, revision2: str) -> str:
    """
    指定された相対パスのリビジョン間の差分を取得します。
    """
    path = convert_relative_path(relative_path)
    print(f"Getting SVN diff for: {relative_path} between revisions: {revision1} and {revision2}", file=sys.stderr)
    try:
        return run_command(f"svn diff -r {revision1}:{revision2} {path}")
    except Exception as e:
        return f"SVN差分の取得に失敗しました: {str(e)}"

@mcp.tool()
def get_svn_diff_by_url(relative_path1: str, relative_path2: str, revision1="HEAD", revision2="HEAD") -> str:
    """
    指定された相対パスのリビジョン間の差分を取得します。
    """
    path1 = convert_relative_path(relative_path1)
    path2 = convert_relative_path(relative_path2)
    print(f"Getting SVN diff for: -r {revision1} {path1} -r {revision2} {path2}", file=sys.stderr)
    try:
        return run_command(f"svn diff -r {revision1} {path1} -r {revision2} {path2}")
    except Exception as e:
        return f"SVN差分の取得に失敗しました: {str(e)}"

@mcp.tool()
def get_svn_commit_history(relative_path: str) -> str:
    """
    リポジトリ内の指定された相対パスのSVNコミット履歴(Revisionのみ)を取得します。
    """
    result = []
    print(f"Getting SVN commit history for: {relative_path}", file=sys.stderr)
    path = convert_relative_path(relative_path)
    log_result = get_svn_log_internal(f"{path}", "-q")
    just_after_separator = False
    for line in log_result.split('\n'):
        if RE_LOG_SEPARATOR.match(line):
            just_after_separator = True
            continue
        if just_after_separator:
            revision_match = RE_REVISION.match(line)
            if revision_match:
                result.append(revision_match.group())
            just_after_separator = False

    print(f"Found revisions: {result}", file=sys.stderr)
    return "\n".join(result)

@mcp.tool()
def get_svn_commit_log(revision: str) -> str:
    """
    SVNコミットログ(詳細)をリビジョン指定で取得します。
    """
    print(f"Getting SVN log for revision: {revision}", file=sys.stderr)
    return get_svn_log_internal(f"{g_repo_url}", f"-v -r {revision}")

@mcp.tool()
def get_svn_log(relative_path: str, optional_args: str = "") -> str:
    """
    指定された相対パスのSVNログを取得します。
    詳細な情報を取得するために、オプション引数(-v)を追加できます。
    対象とするリビジョンを限定するために、オプション引数(-r)を追加できます。
    """
    path = convert_relative_path(relative_path)
    print(f"Getting SVN log for: {relative_path} with optional args: {optional_args}", file=sys.stderr)
    return get_svn_log_internal(path, optional_args)

@mcp.tool()
def get_svn_info() -> str:
    """
    SVNの情報を取得します。
    """
    try:
        return run_command("svn info")
    except Exception as e:
        return f"SVN情報の取得に失敗しました: {str(e)}"

@mcp.tool()
def get_svn_list(relative_path: str) -> str:
    """
    指定された相対パスのSVNリスト(詳細情報付き)を取得します。
    """

    print(f"Getting SVN list for: {relative_path}", file=sys.stderr)
    path = convert_relative_path(relative_path)
    try:
        return run_command(f"svn list -v {path}")
    except Exception as e:
        return f"{relative_path}リストの取得に失敗しました: {str(e)}"

def main():
    get_repo_url_internal()

    # コマンドライン引数を処理
    mcp.run()



if __name__ == "__main__":
    main()


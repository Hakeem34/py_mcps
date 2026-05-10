import os
import re
import sys
import subprocess
import dataclasses
from pathlib import Path
from mcp.server.fastmcp import FastMCP


@dataclasses.dataclass
class SVNNode:
    url: str
    basename: str
    type: str  # "file" or "directory"
    size: int = 0  # ファイルサイズ（ディレクトリの場合は0）
    revision: str = ""  # 最終リビジョン
    children: list = dataclasses.field(default_factory=list)  # 子ノードのリスト（ディレクトリの場合）

class LogStats:
    target_path: str = None
    limit: int
    revision1: str
    revision2: str
    last_revision: int
    keyword: str = ""
    regex: bool = False


RE_REVISION = re.compile(r"^r(\d+)")
RE_LOG_SEPARATOR = re.compile(r"^-{72}\s*$")
RE_LOG_HEADER    = re.compile(r"^r(\d+)\s+\|\s+(\S+)\s+\|\s+(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
RE_LOG_COPY      = re.compile(r"\s*A\s*(\S+)\s*\(from\s+([^:]+):(\d+)\)")
RE_LIST_LINE_DIR  = re.compile(r"^\s*(\d+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\/\s*$")
RE_LIST_LINE_FILE = re.compile(r"^\s*(\d+)\s+(\S+)\s+(\d+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s*$")

g_repo_url = ""
g_working_url = ""
g_working_root = ""
g_username = None
g_password = None
g_get_log_stats = LogStats()
g_search_log_stats = LogStats()

# FastMCPのインスタンスを作成
mcp = FastMCP()

def count_log_revisions(log_text: str) -> list:
    """
    SVNログのテキストから、リビジョンの数をカウントします。
    """
    revisions = []
    just_after_separator = False
    for line in log_text.split('\n'):
        if RE_LOG_SEPARATOR.match(line):
            just_after_separator = True
        elif just_after_separator:
            if match := RE_REVISION.match(line):
                revisions.append(int(match.group(1)))
            just_after_separator = False

    return revisions

def update_log_stats(log_stats: LogStats, log_text: str):
    """"
    LogStatsのlast_revisionを、ログテキストからカウントしたリビジョンの最後のものに更新します。
    カウントできない場合は、revision2を数値に変換したものに更新します。
    """
    revisions = count_log_revisions(log_text)
    if revisions:
        log_stats.last_revision = int(revisions[-1])
    else:
        log_stats.last_revision = convert_revision_to_number(log_stats.target_path, log_stats.revision2)

def convert_revision_to_number(local_path: str, revision: str) -> int:
    """
    リビジョン文字列を数値に変換します。
    例えば、"HEAD"は最新のリビジョン番号に、"BASE"は作業コピーのベースリビジョン番号に変換されます。
    有効なリビジョン指定でない場合は0を返します。
    """
   
#   print(f"Converting local path: {local_path}, revision: {revision} to number", file=sys.stderr)
    if revision.isdigit():
        return int(revision)

    revision_number = run_command(f"svn info {local_path} -r {revision} --show-item revision").strip()
    if revision_number.isdigit():
        return int(revision_number)

    if match := re.match(r"^[Rr]\D+(\d+)$", revision):
        revision_number = int(match.group(1))
        return revision_number

    return 0

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
        if command.startswith("svn ") and g_username is not None and g_password is not None:
            command += f" --username {g_username} --password {g_password} --non-interactive --trust-server-cert"
        result = subprocess.run(command, shell=True, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=False)
        return decode_raw_output(result.stdout)
    except subprocess.CalledProcessError as e:
        return f"コマンドの実行に失敗しました: {decode_raw_output(e.stderr)}"
    except Exception as e:
        return f"予期しないエラー: {str(e)}"

def try_command(command: str) -> str:
    """
    コマンドを実行して、その終了コードのみを返します。
    """
    try:
        if command.startswith("svn ") and g_username is not None and g_password is not None:
            command += f" --username {g_username} --password {g_password} --non-interactive --trust-server-cert"
        result = subprocess.run(command, shell=True, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=False)
        return result.returncode
    except subprocess.CalledProcessError as e:
        return 1
    except Exception as e:
        print(f"予期しないエラー: {str(e)}", file=sys.stderr)
        return 1

def search_svn_logs_internal(log_text: str, keyword: str, regex: bool = False) -> str:
    """
    指定されたパスのSVNログから、キーワードを含むログを検索します。
    limitで取得するログの最大数を指定できます。デフォルトは10です。
    revision1とrevision2でリビジョンの範囲を指定できます。デフォルトでは、revision1はHEAD、revision2は1となっています。
    regexがTrueの場合、keywordは正規表現として扱われます。デフォルトはFalseです。
    """
    matched_logs = []
    current_log = []
    for line in log_text.split('\n'):
        if RE_LOG_SEPARATOR.match(line):
            if current_log:
                log_text = "\n".join(current_log)
                if regex:
                    if re.search(keyword, log_text):
                        matched_logs.append(log_text)
                else:
                    if keyword in log_text:
                        matched_logs.append(log_text)
                current_log = []
        else:
            current_log.append(line)

    if current_log:
        log_text = "\n".join(current_log)
        if regex:
            if re.search(keyword, log_text):
                matched_logs.append(log_text)
        else:
            if keyword in log_text:
                matched_logs.append(log_text)

    return matched_logs


def get_repo_url_internal() -> str:
    """
    リポジトリURLを取得します。
    """
    global g_repo_url
    global g_working_url
    global g_working_root
    svn_info = run_command("svn info")
    # svn infoの出力からリポジトリURLを抽出
    for line in svn_info.split('\n'):
        if line.startswith("URL:"):
            g_working_url = line.split("URL:")[1].strip()
        elif line.startswith("Repository Root:"):
            g_repo_url = line.split("Repository Root:")[1].strip()
        elif line.startswith("Working Copy Root Path:"):
            g_working_root = line.split("Working Copy Root Path:")[1].strip()

    print(f"Repository URL: {g_repo_url}, Working Copy Root Path: {g_working_root}, Working URL: {g_working_url}", file=sys.stderr)
    return g_repo_url

def get_svn_log_internal(path: str, optional_args: str = "") -> str:
    if optional_args:
        return run_command(f"svn log {optional_args} {path}")
    return run_command(f"svn log {path}")

def get_svn_list_internal(path: str, revision: str = "HEAD") -> str:
    return run_command(f"svn list {path} -r {revision} -v")

def convert_target_path(path: str) -> str:
    """
    1.作業コピーのURLを付加した完全なURLに変換します。
    2.1が存在しない場合は、リポジトリからの相対パスを完全なURLに変換します。
    3.URLが直接指定されている場合は、そのまま返します
    4.いずれも存在しない場合は、ローカルファイルシステム上の相対パスとして扱います。
    ローカルファイルも存在しない場合はNoneを返します。
    """
    if path.startswith("/"):
        path = path[1:]
 
    repository_url = g_working_url + "/" + path
#   print(f"Trying working URL: {repository_url}", file=sys.stderr)
    if try_command(f"svn info {repository_url}") == 0:
        return repository_url
    
    repository_url = g_repo_url + "/" + path
#   print(f"Trying repository URL: {repository_url}", file=sys.stderr)
    if try_command(f"svn info {repository_url}") == 0:
        return repository_url

    if try_command(f"svn info {path}") == 0:
        return path

    if os.path.exists(path):
        return path

    return None

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

def get_svn_node_tree_internal(target_path: str, revision: str = "HEAD", include_files: bool = False, depth: int = 1) -> SVNNode:
    """
    指定されたパスのSVNノードのツリー構造を取得します。
    """
    node_kind = get_svn_node_kind(target_path, revision)
    if node_kind == "file":
        print(f"Getting SVN node tree for file: {target_path} at revision: {revision}", file=sys.stderr)
        return None

    path = convert_target_path(target_path)
#   print(f"Getting SVN node tree for directory: {target_path} at revision: {revision} with path: {path}, revision: {revision}, depth: {depth}", file=sys.stderr)
    node = None
    lines = get_svn_list_internal(path, revision).split('\n')
    if depth > 0:
        next_depth = depth - 1
    else:
        next_depth = depth

    for line in lines:
#       print(f"Processing list line: {line}", file=sys.stderr)
        if match := RE_LIST_LINE_FILE.match(line):
            if include_files:
                size = int(match.group(3))
                node_revision = match.group(1)
                name = match.group(7)
#               print(f"Found file: {name} size: {size} revision: {revision}", file=sys.stderr)
                child_node = SVNNode(url=path + "/" + name, basename=name, type="file", size=size, revision=node_revision)
                node.children.append(child_node)
        elif match := RE_LIST_LINE_DIR.match(line):
            node_revision = match.group(1)
            name = match.group(6)
#           print(f"Found directory: {name} revision: {node_revision}", file=sys.stderr)
            if (name != r"."):
#               print(f"Descending into directory: {name} revision: {node_revision}", file=sys.stderr)
                if next_depth > 0 or next_depth == -1:
                    child_node = get_svn_node_tree_internal('/'.join([target_path, name]), revision, include_files, next_depth)
                    node.children.append(child_node)
                else:
                    child_node = SVNNode(url=path + "/" + name, basename=name, type="directory", revision=node_revision)
                    node.children.append(child_node)
            else:
#               print(f"Found current directory entry: {name} revision: {node_revision}", file=sys.stderr)
                node = SVNNode(url=path, basename=os.path.basename(target_path), type="directory", revision=node_revision)

    return node

def format_svn_tree(node: SVNNode, prefix: str = "") -> str:
    """
    SVNノードのツリー構造をテキスト形式でフォーマットします。
    """
    lines = []
#   print(f"Formatting node: {node.basename} type: {node.type} revision: {node.revision} size: {node.size}", file=sys.stderr)
    if node.type == "directory":
        lines.append(f"{prefix}{node.basename}")
        for child in node.children:
            lines.append(format_svn_tree(child, prefix + "  "))
    else:
        lines.append(f"{prefix}{node.basename}")

#   print(f"Formatted lines for node {node.basename}:\n", file=sys.stderr)
    return "\n".join(lines)

def match_svn_node_by_name(node: SVNNode, name_pattern: str) -> list:
    """
    SVNノードのツリー構造から、指定された名前パターンにマッチするノードを検索します。
    """
    matched_nodes = []
    path = Path(node.basename)
    if path.match(name_pattern):
        matched_nodes.append(node)
    
    for child in node.children:
        matched_nodes.extend(match_svn_node_by_name(child, name_pattern))
    
    return matched_nodes

@mcp.tool()
def search_svn_nodes(target_path: str, revision: str = "HEAD", file_name: str = "*", depth: int = -1) -> str:
    """
    指定されたパスのSVNノードを検索します。
    target_pathはリポジトリ上の相対パスもしくは作業コピー上の相対パスで指定します。
    """
    print(f"Searching SVN nodes for: {target_path} at revision: {revision}, file_name: {file_name}, depth: {depth}", file=sys.stderr)
    target_path = target_path.removesuffix('/')
    node = get_svn_node_tree_internal(target_path, revision, True, depth)
    if node is None:
        return f"指定されたパスはファイルです: {target_path}"

    matched_nodes = match_svn_node_by_name(node, file_name)
#   for matched_node in matched_nodes:
#       print(f"Matched node: {matched_node.url} type: {matched_node.type} revision: {matched_node.revision} size: {matched_node.size}", file=sys.stderr)    
        
    path = convert_target_path(target_path)
    paths = [matched_node.url.replace(path, target_path, 1) for matched_node in matched_nodes]
    return "\n".join(paths)

@mcp.tool()
def get_svn_tree(target_path: str, revision: str = "HEAD", include_files: bool = False, depth: int = -1) -> str:
    """
    指定されたパスのSVNツリー構造を取得します。
    """
    print(f"Getting SVN tree for: {target_path} at revision: {revision}, include_files: {include_files}, depth: {depth}", file=sys.stderr)
    target_path = target_path.removesuffix('/')
    node = get_svn_node_tree_internal(target_path, revision, include_files, depth)
    if node is None:
        return f"指定されたパスはファイルです: {target_path}"

#   print(f"SVN tree for {target_path}:\n{node}", file=sys.stderr)
    return format_svn_tree(node)



@mcp.tool()
def get_svn_node_size(target_path: str, revision: str = "HEAD") -> str:
    """
    指定されたパスのSVNノードのサイズを取得します。
    """
    path = convert_target_path(target_path)
    print(f"Getting SVN node size for: {target_path}", file=sys.stderr)
    if get_svn_node_kind(target_path, revision) == "directory":
        return "0"

    result = run_command(f"svn list {path} -r {revision} -v")
    for line in result.split('\n'):
        match = re.match(r"^\s*\d+\s+\S+\s+(\d+)?", line)
        if match:
            size = match.group(1)
            return size
        
    print(f"Size not found in SVN info output for: {target_path}", file=sys.stderr)
    return "Size not found"

@mcp.tool()
def get_svn_node_kind(target_path: str, revision: str = "HEAD") -> str:
    """
    指定されたパスが存在するか、存在する場合はノードの種類を取得します。
    """
    path = convert_target_path(target_path)
#   print(f"Getting SVN node kind for: {target_path}", file=sys.stderr)
    result = run_command(f"svn info {path}")
    for line in result.split('\n'):
        if line.startswith("Node Kind:"):
            return line.split("Node Kind:")[1].strip()
    return "non-existent"

@mcp.tool()
def blame_svn_file(target_path: str, revision: str = "HEAD", start_line: int = 1, end_line: int = 0) -> str:
    """
    指定されたパスのSVNファイルのblame情報を取得します。
    blame情報は、各行の最終変更リビジョンと著者を含みます。
    start_lineとend_lineを指定することで、ファイルの特定の行範囲を取得できます。
    マイナス値を指定すると末尾からの行数を表します。end_lineが0の場合は、ファイルの末尾までを取得します。
    """
    path = convert_target_path(target_path)
    print(f"Getting SVN blame for: {target_path} at revision: {revision}, start_line: {start_line}, end_line: {end_line}", file=sys.stderr)
    text = run_command(f"svn blame {path}@{revision}")
    if start_line > 0:
        start = max(0, start_line - 1)
    else:
        start = start_line

    if end_line != 0:
        text = '\n'.join(text.split('\n')[start:end_line])
    else:
        text = '\n'.join(text.split('\n')[start:])
    return text

@mcp.tool()
def cat_svn_file(target_path: str, revision: str = "HEAD", start_line: int = 1, end_line: int = 0) -> str:
    """
    指定されたパスのSVNファイルの内容を取得します。
    start_lineとend_lineを指定することで、ファイルの特定の行範囲を取得できます。
    マイナス値を指定すると末尾からの行数を表します。end_lineが0の場合は、ファイルの末尾までを取得します。
    """
    path = convert_target_path(target_path)
    print(f"Getting SVN file content for: {target_path} at revision: {revision} start_line: {start_line} end_line: {end_line}", file=sys.stderr)
    text = run_command(f"svn cat {path}@{revision}")
    if start_line > 0:
        start = max(0, start_line - 1)
    else:
        start = start_line

    if end_line != 0:
        text = '\n'.join(text.split('\n')[start:end_line])
    else:
        text = '\n'.join(text.split('\n')[start:])
    return text

@mcp.tool()
def export_svn_file(target_path: str, revision: str = "HEAD", output_path: str = "_tmp_export") -> str:
    """
    指定されたパスをoutput_pathにエクスポートします。
    """
    if not is_safe_path(output_path):
        return f"安全でないパスが指定されました: {output_path}"

    path = convert_target_path(target_path)
    if get_svn_node_kind(target_path) == "directory":
        output_path = os.path.join(output_path, os.path.basename(target_path))
    else:
        if os.path.dirname(output_path) == "":
            output_path = os.path.join(output_path, os.path.basename(target_path))

    print(f"Exporting SVN file: {target_path} at revision: {revision} to output path: {output_path}({os.path.dirname(output_path)})", file=sys.stderr)
    os.makedirs(os.path.dirname(output_path), exist_ok=True) if output_path else None
    result = run_command(f"svn export {path}@{revision} {output_path}")
    return result

@mcp.tool()
def get_svn_diff_by_revision(target_path: str, revision1: str, revision2: str) -> str:
    """
    指定されたパスのリビジョン間の差分を取得します。
    """
    path = convert_target_path(target_path)
    print(f"Getting SVN diff for: {target_path} between revisions: {revision1} and {revision2}", file=sys.stderr)
    return run_command(f"svn diff {path}@{revision1} {path}@{revision2}")

@mcp.tool()
def get_svn_diff_by_url(target_path1: str, target_path2: str, revision1="HEAD", revision2="HEAD") -> str:
    """
    指定された二つのパスの差分を取得します。
    """
    path1 = convert_target_path(target_path1)
    path2 = convert_target_path(target_path2)
    print(f"Getting SVN diff for: {path1}@{revision1} {path2}@{revision2}", file=sys.stderr)
    return run_command(f"svn diff {path1}@{revision1} {path2}@{revision2}")

@mcp.tool()
def get_svn_diff(target_path: str) -> str:
    """
    指定されたローカルパスのwork/baseの差分を取得します。
    """
    print(f"Getting SVN diff for: {target_path}", file=sys.stderr)
    return run_command(f'svn diff --internal-diff -x "-p -U 0" {target_path}')

@mcp.tool()
def get_svn_commit_history(target_path: str) -> str:
    """
    リポジトリ内の指定されたパスのSVNコミット履歴(Revisionのみ)を取得します。
    """
    result = []
    print(f"Getting SVN commit history for: {target_path}", file=sys.stderr)
    path = convert_target_path(target_path)
    log_result = get_svn_log_internal(f"{path}", "-q")
    for line in log_result.split('\n'):
#       print(f"Processing log line: {line}", file=sys.stderr)
        revision_match = RE_REVISION.match(line)
        if revision_match:
            result.append(revision_match.group(1))

#   print(f"Found revisions: {result}", file=sys.stderr)
    return "\n".join(result)

@mcp.tool()
def get_svn_commit_log(revision: str) -> str:
    """
    SVNコミットログ(詳細)をリビジョン指定で全文取得します。
    """
    print(f"Getting SVN log for revision: {revision}", file=sys.stderr)
    return get_svn_log_internal(f"{g_repo_url}", f"-v -r {revision}")

@mcp.tool()
def get_svn_logs(target_path: str, limit: int = 10, revision1: str = "HEAD", revision2: str = "1") -> str:
    """
    指定されたパスのSVNログを取得します。
    limitで取得するログの最大数を指定できます。デフォルトは10です。
    revision1とrevision2でリビジョンの範囲を指定できます。デフォルトでは、revision1はHEAD、revision2は1となっています。
    """
    global g_get_log_stats

    g_get_log_stats.target_path = target_path
    g_get_log_stats.limit = limit
    g_get_log_stats.revision1 = revision1
    g_get_log_stats.revision2 = revision2

    path = convert_target_path(target_path)
    print(f"Getting SVN logs for: {target_path} with limit: {limit}, revisions: {revision1}:{revision2}", file=sys.stderr)
    text = get_svn_log_internal(path, f"-v -l {limit} -r {revision1}:{revision2}")
    update_log_stats(g_get_log_stats, text)
    return text

@mcp.tool()
def get_svn_logs_continue() -> str:
    """
    直前のget_svn_logsの続きからログを取得します
    """
    global g_get_log_stats

    if g_get_log_stats.target_path is None:
        return "前回のget_svn_logsの情報がありません"

    arg_revision1 = convert_revision_to_number(g_get_log_stats.target_path, g_get_log_stats.revision1)
    arg_revision2 = convert_revision_to_number(g_get_log_stats.target_path, g_get_log_stats.revision2)
    last_revision = g_get_log_stats.last_revision
    if last_revision == arg_revision2:
        return "これ以上取得できるログはありません"

    if arg_revision1 < arg_revision2:
        start_revision = g_get_log_stats.last_revision + 1
    else:
        start_revision = g_get_log_stats.last_revision - 1

    print(f"Continuing to get older SVN logs for: {g_get_log_stats.target_path} with limit: {g_get_log_stats.limit}, revisions: {start_revision}:{g_get_log_stats.revision2}", file=sys.stderr)
    text = get_svn_log_internal(convert_target_path(g_get_log_stats.target_path), f"-v -l {g_get_log_stats.limit} -r {start_revision}:{g_get_log_stats.revision2}")
    update_log_stats(g_get_log_stats, text)
    return text

@mcp.tool()
def get_svn_status() -> str:
    """
    SVN作業コピーのステータスを取得して返します
    """
    print(f"Getting SVN status for: {g_working_root}", file=sys.stderr)
    return run_command(f"svn status {g_working_root}")

@mcp.tool()
def get_svn_branch_base(target_path: str) -> str:
    """
    指定されたパスのSVNブランチ派生元を取得します。
    """
    path = convert_target_path(target_path)
    relative_path = "/" + os.path.relpath(path, g_repo_url).replace("\\", "/")
    print(f"Getting SVN branch base for: {target_path} -> {relative_path}", file=sys.stderr)
    text = run_command(f"svn log {path} --stop-on-copy -q -v")
    revision = "unknown"
    author = "unknown"
    date = "unknown"
    for line in text.split('\n'):
        print(f"Processing log line for branch base: {line}", file=sys.stderr)
        if match := RE_LOG_HEADER.match(line):
            revision = match.group(1)
            author = match.group(2)
            date = match.group(3)
        elif match := RE_LOG_COPY.search(line):
            added_path = match.group(1)
            copy_from_path = match.group(2)
            copy_from_revision = match.group(3)
            if added_path == relative_path:
                return f"このパスは{date} r{revision}で{copy_from_path}のr{copy_from_revision}から派生しました"

    return f"このパスは{date} r{revision}で新規作成されました。(派生ではない)"


@mcp.tool()
def search_svn_logs(target_path: str, keyword: str, regex: bool = False, limit: int = 10, revision1: str = "HEAD", revision2: str = "1") -> str:
    """
    指定されたSVNログを検索して、ヒットしたものを返します
    limitで取得するログの最大数を指定できます。デフォルトは10です。
    revision1とrevision2でリビジョンの範囲を指定できます。デフォルトでは、revision1はHEAD、revision2は1となっています。
    regexがTrueの場合、keywordは正規表現として扱われます。デフォルトはFalseです。
    """
    global g_search_log_stats

    g_search_log_stats.target_path = target_path
    g_search_log_stats.limit = limit
    g_search_log_stats.revision1 = revision1
    g_search_log_stats.revision2 = revision2
    g_search_log_stats.keyword = keyword
    g_search_log_stats.regex = regex

    path = convert_target_path(target_path)
    print(f"Searching SVN logs for: {target_path} with keyword: {keyword}, limit: {limit}, revisions: {revision1}:{revision2}", file=sys.stderr)
    text = get_svn_log_internal(path, f"-v -l {limit} -r {revision1}:{revision2}")
    update_log_stats(g_search_log_stats, text)
    matched_logs = search_svn_logs_internal(text, keyword, regex)
    return "\n\n".join(matched_logs)

@mcp.tool()
def search_svn_logs_continue() -> str:
    """
    直前のsearch_svn_logsの続きからログを取得して検索します
    """
    global g_search_log_stats

    if g_search_log_stats.target_path is None:
        return "前回のsearch_svn_logsの情報がありません"

    arg_revision1 = convert_revision_to_number(g_search_log_stats.target_path, g_search_log_stats.revision1)
    arg_revision2 = convert_revision_to_number(g_search_log_stats.target_path, g_search_log_stats.revision2)
    last_revision = g_search_log_stats.last_revision
    if last_revision == arg_revision2:
        return "これ以上取得できるログはありません"

    if arg_revision1 < arg_revision2:
        start_revision = g_search_log_stats.last_revision + 1
    else:
        start_revision = g_search_log_stats.last_revision - 1

    print(f"Continuing to search older SVN logs for: {g_search_log_stats.target_path} with keyword: {g_search_log_stats.keyword}, limit: {g_search_log_stats.limit}, revisions: {start_revision}:{g_search_log_stats.revision2}", file=sys.stderr)
    text = get_svn_log_internal(convert_target_path(g_search_log_stats.target_path), f"-v -l {g_search_log_stats.limit} -r {start_revision}:{g_search_log_stats.revision2}")
    update_log_stats(g_search_log_stats, text)
    matched_logs = search_svn_logs_internal(text, g_search_log_stats.keyword, g_search_log_stats.regex)
    return "\n\n".join(matched_logs)

@mcp.tool()
def get_svn_info() -> str:
    """
    SVNの情報を取得します。
    """
    return run_command("svn info")

@mcp.tool()
def get_svn_list(target_path: str, revision: str = "HEAD") -> str:
    """
    SVNリスト(詳細情報付き)を取得します。
    target_pathはリポジトリ上の相対パスもしくは作業コピー上の相対パスで指定します。
    """

    print(f"Getting SVN list for: {target_path} at revision: {revision}", file=sys.stderr)
    path = convert_target_path(target_path)
    return get_svn_list_internal(path, revision)

def test_calls():
#   result = get_svn_commit_history(".scripts/mcp_svn.py")
#   print(f"SVN commit history:\n{result}", file=sys.stderr)
#   size = get_svn_node_size(".scripts/mcp_svn.py")
#   print(f"Size of .scripts/mcp_svn.py: {size}", file=sys.stderr)
#   size = get_svn_node_size(".scripts")
#   print(f"Size of .scripts: {size}", file=sys.stderr)
#   result =get_svn_tree(".", revision="HEAD", include_files=True, depth=2)
#   print(f"SVN tree:\n{result}", file=sys.stderr)
#   result =get_svn_tree("./", revision="HEAD", include_files=True, depth=1)
#   print(f"SVN tree:\n{result}", file=sys.stderr)
#   result =get_svn_tree(".", revision="HEAD", include_files=True, depth=0)
#   print(f"SVN tree:\n{result}", file=sys.stderr)
#   result =get_svn_tree(".", revision="HEAD", include_files=True, depth=-1)
#   print(f"SVN tree:\n{result}", file=sys.stderr)
#   result =get_svn_tree("trunk", revision="HEAD", include_files=False, depth=2)
#   print(f"SVN tree:\n{result}", file=sys.stderr)
#   result = search_svn_nodes("trunk/tools", revision="HEAD", file_name="*.py", depth=3)
#   print(f"Search result:\n{result}", file=sys.stderr)
#   result = search_svn_logs(".", keyword="refs #", regex=False, limit=10)
#   print(f"Search result:\n{result}", file=sys.stderr)
#   result = search_svn_logs(".", keyword=r"refs #\d+", regex=True, limit=10)
#   print(f"Search result:\n{result}", file=sys.stderr)

#   revision = convert_revision_to_number(".", "HEAD")
#   print(f"HEAD revision number: {revision}", file=sys.stderr)
#   revision = convert_revision_to_number(".", "BASE")
#   print(f"BASE revision number: {revision}", file=sys.stderr)
#   revision = convert_revision_to_number(".", " PREV")
#   print(f"PREV revision number: {revision}", file=sys.stderr)
#   revision = convert_revision_to_number(".", "COMMITTED")
#   print(f"COMMITTED revision number: {revision}", file=sys.stderr)
#   revision = convert_revision_to_number(".", "{2025-05-04}")
#   print(f"2025-05-04 revision number: {revision}", file=sys.stderr)
#   revision = convert_revision_to_number(".", "{2026-05-04}")
#   print(f"2026-05-04 revision number: {revision}", file=sys.stderr)
#   revision = convert_revision_to_number(".", "Rev.1234")
#   print(f"Rev.1234 revision number: {revision}", file=sys.stderr)

#   result = get_svn_logs(".", limit=10, revision1="HEAD", revision2="1")
#   print(f"SVN logs:\n{result}", file=sys.stderr)
#   result = get_svn_logs_continue()
#   print(f"SVN logs continue1:\n{result}", file=sys.stderr)
#   result = get_svn_logs_continue()
#   print(f"SVN logs continue2:\n{result}", file=sys.stderr)
#   result = get_svn_logs_continue()
#   print(f"SVN logs continue3:\n{result}", file=sys.stderr)
#   result = get_svn_logs_continue()
#   print(f"SVN logs continue4:\n{result}", file=sys.stderr)
#   result = get_svn_logs_continue()
#   print(f"SVN logs continue5:\n{result}", file=sys.stderr)

#   result = get_svn_status()
#   print(f"SVN status:\n{result}", file=sys.stderr)
    result = get_svn_branch_base(".")
    print(f"SVN branch base:\n{result}", file=sys.stderr)
    result = get_svn_branch_base("branches/tools")
    print(f"SVN branch base:\n{result}", file=sys.stderr)
    result = get_svn_branch_base("branches/tools/mcp_redmine/mcp_redmine_b")
    print(f"SVN branch base:\n{result}", file=sys.stderr)
    result = get_svn_diff(".scripts/mcp_svn.py")
    print(f"SVN diff:\n{result}", file=sys.stderr)

    return

def read_credentials():
    """
    SVNの認証情報を読み取ります。
    """
    global g_username
    global g_password

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
            if match := re.match(r"svn_user\s*:\s*(\S+)", line):
                g_username = match.group(1)
            elif match := re.match(r"svn_password\s*:\s*(\S+)", line):
                g_password = match.group(1)

    print(f"Read credentials: username={g_username}", file=sys.stderr)
    return

def main():
    read_credentials()
    get_repo_url_internal()
    test_calls()

    mcp.run()



if __name__ == "__main__":
    main()


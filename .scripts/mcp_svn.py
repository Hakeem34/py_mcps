import os
import re
import sys
import argparse
import datetime
import subprocess
import dataclasses
from pathlib import Path
from mcp.server.fastmcp import FastMCP


g_log_file = None


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
RE_LOG_COPY      = re.compile(r"^\s*A\s*(\S+)\s*\(from\s+([^:]+):(\d+)\)")
RE_LOG_NOT_ADD   = re.compile(r"^\s*[MDR]\s*(\S+)\s*")
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


def print_log(text, file=sys.stderr):
    print(text, file=sys.stderr)
    if g_log_file:
        print(text, file=g_log_file)
        g_log_file.flush()


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
   
#   print_log(f"Converting local path: {local_path}, revision: {revision} to number", file=sys.stderr)
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
        print_log(f"予期しないエラー: {str(e)}", file=sys.stderr)
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

    print_log(f"Repository URL: {g_repo_url}, Working Copy Root Path: {g_working_root}, Working URL: {g_working_url}", file=sys.stderr)
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
#   print_log(f"Trying working URL: {repository_url}", file=sys.stderr)
    if try_command(f"svn info {repository_url}") == 0:
        return repository_url
    
    repository_url = g_repo_url + "/" + path
#   print_log(f"Trying repository URL: {repository_url}", file=sys.stderr)
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
        print_log(f"Getting SVN node tree for file: {target_path} at revision: {revision}", file=sys.stderr)
        return None

    path = convert_target_path(target_path)
#   print_log(f"Getting SVN node tree for directory: {target_path} at revision: {revision} with path: {path}, revision: {revision}, depth: {depth}", file=sys.stderr)
    node = None
    lines = get_svn_list_internal(path, revision).split('\n')
    if depth > 0:
        next_depth = depth - 1
    else:
        next_depth = depth

    for line in lines:
#       print_log(f"Processing list line: {line}", file=sys.stderr)
        if match := RE_LIST_LINE_FILE.match(line):
            if include_files:
                size = int(match.group(3))
                node_revision = match.group(1)
                name = match.group(7)
#               print_log(f"Found file: {name} size: {size} revision: {revision}", file=sys.stderr)
                child_node = SVNNode(url=path + "/" + name, basename=name, type="file", size=size, revision=node_revision)
                node.children.append(child_node)
        elif match := RE_LIST_LINE_DIR.match(line):
            node_revision = match.group(1)
            name = match.group(6)
#           print_log(f"Found directory: {name} revision: {node_revision}", file=sys.stderr)
            if (name != r"."):
#               print_log(f"Descending into directory: {name} revision: {node_revision}", file=sys.stderr)
                if next_depth > 0 or next_depth == -1:
                    child_node = get_svn_node_tree_internal('/'.join([target_path, name]), revision, include_files, next_depth)
                    node.children.append(child_node)
                else:
                    child_node = SVNNode(url=path + "/" + name, basename=name, type="directory", revision=node_revision)
                    node.children.append(child_node)
            else:
#               print_log(f"Found current directory entry: {name} revision: {node_revision}", file=sys.stderr)
                node = SVNNode(url=path, basename=os.path.basename(target_path), type="directory", revision=node_revision)

    return node

def format_svn_tree(node: SVNNode, prefix: str = "") -> str:
    """
    SVNノードのツリー構造をテキスト形式でフォーマットします。
    """
    lines = []
#   print_log(f"Formatting node: {node.basename} type: {node.type} revision: {node.revision} size: {node.size}", file=sys.stderr)
    if node.type == "directory":
        lines.append(f"{prefix}{node.basename}")
        for child in node.children:
            lines.append(format_svn_tree(child, prefix + "  "))
    else:
        lines.append(f"{prefix}{node.basename}")

#   print_log(f"Formatted lines for node {node.basename}:\n", file=sys.stderr)
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

def pick_up_dir_copy_logs(log_text: str) -> list:
    logs = []

    revision = "unknown"
    author = "unknown"
    date = "unknown"
    copy_from_path = ""
    copy_from_revision = ""
    not_add_log = False
    for log in log_text.split("\n"):
        if match := RE_LOG_SEPARATOR.match(log):
            if (not_add_log == False) and (copy_from_path != ""):
                if (get_svn_node_kind(added_path, revision) == "directory"):
                    text = f"{date} | {added_path}@{revision} | {copy_from_path}@{copy_from_revision}" 
                    print_log(text, file=sys.stderr)
                    logs.append(text)

            copy_from_path = ""
            not_add_log = False
        elif match := RE_LOG_HEADER.match(log):
            revision = match.group(1)
            author = match.group(2)
            date = match.group(3)
        elif match := RE_LOG_NOT_ADD.match(log):
            not_add_log = True
        elif match := RE_LOG_COPY.match(log):
            added_path = match.group(1)
            copy_from_path = match.group(2)
            copy_from_revision = match.group(3)
        else:
#           print_log(log)
            pass
    return logs

@mcp.tool()
def search_svn_nodes(target_path: str, revision: str = "HEAD", file_name: str = "*", depth: int = -1) -> str:
    """
    Searches for SVN nodes at the specified path.
    target_path must be absolute path or full URL.
    """
    print_log(f"Searching SVN nodes for: {target_path} at revision: {revision}, file_name: {file_name}, depth: {depth}", file=sys.stderr)
    target_path = target_path.removesuffix('/')
    node = get_svn_node_tree_internal(target_path, revision, True, depth)
    if node is None:
        return f"指定されたパスはファイルです: {target_path}"

    matched_nodes = match_svn_node_by_name(node, file_name)
#   for matched_node in matched_nodes:
#       print_log(f"Matched node: {matched_node.url} type: {matched_node.type} revision: {matched_node.revision} size: {matched_node.size}", file=sys.stderr)    
        
    path = convert_target_path(target_path)
    paths = [matched_node.url.replace(path, target_path, 1) for matched_node in matched_nodes]
    return "\n".join(paths)

@mcp.tool()
def get_svn_tree(target_path: str, revision: str = "HEAD", include_files: bool = False, depth: int = -1) -> str:
    """
    Retrieves the SVN tree structure for the specified path.
    target_path must be absolute path or full URL.
    """
    print_log(f"Getting SVN tree for: {target_path} at revision: {revision}, include_files: {include_files}, depth: {depth}", file=sys.stderr)
    target_path = target_path.removesuffix('/')
    node = get_svn_node_tree_internal(target_path, revision, include_files, depth)
    if node is None:
        return f"指定されたパスはファイルです: {target_path}"

#   print_log(f"SVN tree for {target_path}:\n{node}", file=sys.stderr)
    return format_svn_tree(node)



@mcp.tool()
def get_svn_node_size(target_path: str, revision: str = "HEAD") -> str:
    """
    Retrieves the size of an SVN node at the specified path.
    target_path must be absolute path or full URL.
    """
    path = convert_target_path(target_path)
    print_log(f"Getting SVN node size for: {target_path}", file=sys.stderr)
    if get_svn_node_kind(target_path, revision) == "directory":
        return "0"

    result = run_command(f"svn list {path} -r {revision} -v")
    for line in result.split('\n'):
        match = re.match(r"^\s*\d+\s+\S+\s+(\d+)?", line)
        if match:
            size = match.group(1)
            return size
        
    print_log(f"Size not found in SVN info output for: {target_path}", file=sys.stderr)
    return "Size not found"

@mcp.tool()
def get_svn_node_kind(target_path: str, revision: str = "HEAD") -> str:
    """
    Retrieves the kind of a node at the specified path, or None if it doesn't exist.
    target_path must be absolute path or full URL.
    """
    path = convert_target_path(target_path)
#   print_log(f"Getting SVN node kind for: {target_path}", file=sys.stderr)
    result = run_command(f"svn info {path}")
    for line in result.split('\n'):
        if line.startswith("Node Kind:"):
            return line.split("Node Kind:")[1].strip()
    return "non-existent"

@mcp.tool()
def blame_svn_file(target_path: str, revision: str = "HEAD", start_line: int = 1, end_line: int = 0) -> str:
    """
    Retrieves the blame information for an SVN file at the specified path.
    target_path must be absolute path or full URL.
    The blame information includes the last change revision and author for each line.
    By specifying start_line and end_line, you can retrieve a specific line range.
    A negative value specifies the number of lines from the end. If end_line is 0, the content up to the end of the file is retrieved.
    """
    path = convert_target_path(target_path)
    print_log(f"Getting SVN blame for: {target_path} at revision: {revision}, start_line: {start_line}, end_line: {end_line}", file=sys.stderr)
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
    Retrieves the content of an SVN file at the specified path.
    target_path must be absolute path or full URL.
    By specifying start_line and end_line, you can retrieve a specific line range.
    A negative value specifies the number of lines from the end. If end_line is 0, the content up to the end of the file is retrieved.
    """
    path = convert_target_path(target_path)
    print_log(f"Getting SVN file content for: {target_path} at revision: {revision} start_line: {start_line} end_line: {end_line}", file=sys.stderr)
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
    Exports the specified path to the given output_path.
    target_path must be absolute path or full URL.
    """
    if not is_safe_path(output_path):
        return f"安全でないパスが指定されました: {output_path}"

    path = convert_target_path(target_path)
    if get_svn_node_kind(target_path) == "directory":
        output_path = os.path.join(output_path, os.path.basename(target_path))
    else:
        if os.path.dirname(output_path) == "":
            output_path = os.path.join(output_path, os.path.basename(target_path))

    print_log(f"Exporting SVN file: {target_path} at revision: {revision} to output path: {output_path}({os.path.dirname(output_path)})", file=sys.stderr)
    os.makedirs(os.path.dirname(output_path), exist_ok=True) if output_path else None
    result = run_command(f"svn export {path}@{revision} {output_path}")
    return result

@mcp.tool()
def get_svn_diff_by_revision(target_path: str, revision1: str, revision2: str) -> str:
    """
    Retrieves the difference between two revisions of a specified path.
    target_path must be absolute path or full URL.
    """
    path = convert_target_path(target_path)
    print_log(f"Getting SVN diff for: {target_path} between revisions: {revision1} and {revision2}", file=sys.stderr)
    return run_command(f"svn diff {path}@{revision1} {path}@{revision2}")

@mcp.tool()
def get_svn_diff_by_url(target_path1: str, target_path2: str, revision1="HEAD", revision2="HEAD") -> str:
    """
    Retrieves the difference between two specified paths.
    target_paths must be absolute path or full URL.
    """
    path1 = convert_target_path(target_path1)
    path2 = convert_target_path(target_path2)
    print_log(f"Getting SVN diff for: {path1}@{revision1} {path2}@{revision2}", file=sys.stderr)
    return run_command(f"svn diff {path1}@{revision1} {path2}@{revision2}")

@mcp.tool()
def get_svn_diff(target_path: str) -> str:
    """
    Retrieves the difference between the work and base versions of a specified local path.
    target_path must be absolute path or full URL.
    """
    print_log(f"Getting SVN diff for: {target_path}", file=sys.stderr)
    return run_command(f'svn diff --internal-diff -x "-p -U 0" {target_path}')

@mcp.tool()
def get_svn_commit_history(target_path: str) -> str:
    """
    Retrieves the SVN commit history (Revisions only) for the specified path in the repository.
    target_path must be absolute path or full URL.
    """
    result = []
    print_log(f"Getting SVN commit history for: {target_path}", file=sys.stderr)
    path = convert_target_path(target_path)
    log_result = get_svn_log_internal(f"{path}", "-q")
    for line in log_result.split('\n'):
#       print_log(f"Processing log line: {line}", file=sys.stderr)
        revision_match = RE_REVISION.match(line)
        if revision_match:
            result.append(revision_match.group(1))

    print_log(f"Found revisions: {result}", file=sys.stderr)
    if len(result) == 0:
        return "No revisions found"
    return f"Found revisions: {', '.join(result)}"

@mcp.tool()
def get_svn_commit_log(target_url: str, revision: str) -> str:
    """
    Retrieves the full SVN commit log (detailed) for a specified revision.
    target_url must be full URL.
    """
    print_log(f"Getting SVN log for revision: {revision}", file=sys.stderr)
    return get_svn_log_internal(f"{g_repo_url}", f"-v -r {revision}")

@mcp.tool()
def get_svn_logs(target_path: str, limit: int = 10, revision1: str = "HEAD", revision2: str = "1") -> str:
    """
    Retrieves the SVN logs for the specified path. 
    target_path must be absolute path or full URL.
    You can set a limit for the maximum number of logs to retrieve. 
    The revision range can be specified with revision1 and revision2. By default, revision1 is HEAD and revision2 is 1.
    """
    global g_get_log_stats

    g_get_log_stats.target_path = target_path
    g_get_log_stats.limit = limit
    g_get_log_stats.revision1 = revision1
    g_get_log_stats.revision2 = revision2

    path = convert_target_path(target_path)
    print_log(f"Getting SVN logs for: {target_path} with limit: {limit}, revisions: {revision1}:{revision2}", file=sys.stderr)
    text = get_svn_log_internal(path, f"-v -l {limit} -r {revision1}:{revision2}")
    update_log_stats(g_get_log_stats, text)
    return text

@mcp.tool()
def get_svn_logs_continue() -> str:
    """
    Continues retrieving logs from the previous call to get_svn_logs.
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

    print_log(f"Continuing to get older SVN logs for: {g_get_log_stats.target_path} with limit: {g_get_log_stats.limit}, revisions: {start_revision}:{g_get_log_stats.revision2}", file=sys.stderr)
    text = get_svn_log_internal(convert_target_path(g_get_log_stats.target_path), f"-v -l {g_get_log_stats.limit} -r {start_revision}:{g_get_log_stats.revision2}")
    update_log_stats(g_get_log_stats, text)
    return text

@mcp.tool()
def get_svn_status() -> str:
    """
    Retrieves and returns the status of the SVN working copy.
    target_path must be absolute path or full URL.
    """
    print_log(f"Getting SVN status for: {g_working_root}", file=sys.stderr)
    return run_command(f"svn status {g_working_root}")


def is_ancestor(path_A, path_B):
    """
    path_A が path_B の祖先ディレクトリなら True
    同一パスも True とする
    """
    path_A = Path(path_A).resolve()
    path_B = Path(path_B).resolve()

    try:
        path_B.relative_to(path_A)
        return True
    except ValueError:
        return False

@mcp.tool()
def get_svn_branch_base(target_path: str) -> str:
    """
    Retrieves the SVN branch base for the specified path.
    target_path must be absolute path or full URL.
    """
    path = convert_target_path(target_path)
    if path == None:
        return f'指定されたパス{target_path}は見つかりませんでした'

    relative_path = "/" + os.path.relpath(path, g_repo_url).replace("\\", "/")
    print_log(f"Getting SVN branch base for: {target_path} -> {relative_path}", file=sys.stderr)
    text = run_command(f"svn log {path} --stop-on-copy -q -v")
    revision = "unknown"
    author = "unknown"
    date = "unknown"
    copy_from_path = ""
    copy_from_revision = ""
    not_add_log = False
    for line in text.split('\n'):
#       print_log(f"Processing log line for branch base: {line}", file=sys.stderr)
        if match := RE_LOG_SEPARATOR.match(line):
            if (not_add_log == False) and (copy_from_path != ""):
                return f"このパスは{date} r{revision}で{copy_from_path}のr{copy_from_revision}から派生しました"

            copy_from_path = ""
            not_add_log = False
        elif match := RE_LOG_HEADER.match(line):
            revision = match.group(1)
            author = match.group(2)
            date = match.group(3)
        elif match := RE_LOG_NOT_ADD.match(line):
            not_add_log = True
        elif match := RE_LOG_COPY.match(line):
            added_path = match.group(1)
            if is_ancestor(added_path, relative_path):
                copy_from_path = match.group(2)
                copy_from_revision = match.group(3)

    return f"このパスは{date} r{revision}で新規作成されました。(派生ではない)"

@mcp.tool()
def get_create_branch_logs(target_path: str) -> str:
    """
    Extracts only branch creation (folder copy) information from the SVN logs.
    target_path must be absolute path or full URL.
    Output format:
    commit date and time | copy_to_path@revision | copy_from_path@base_revision
    """
    path = convert_target_path(target_path)
    if path == None:
        return f'指定されたパス{target_path}は見つかりませんでした'

    relative_path = "/" + os.path.relpath(path, g_repo_url).replace("\\", "/")
    print_log(f"Getting create branch logs for: {target_path} -> {relative_path}", file=sys.stderr)
    text = run_command(f"svn log {path} -q -v")
    logs = pick_up_dir_copy_logs(text)
    if len(logs):
        return "\n".join(logs)

    return f"このパス({target_path})にはブランチは見つかりませんでした"


@mcp.tool()
def search_svn_logs(target_path: str, keyword: str, regex: bool = False, limit: int = 10, revision1: str = "HEAD", revision2: str = "1") -> str:
    """
    Searches SVN logs for a specified path and returns matching logs.
    target_path must be absolute path or full URL.
    You can set a limit for the maximum number of logs to retrieve. Default is 10.
    The revision range can be specified with revision1 and revision2. By default, revision1 is HEAD and revision2 is 1.
    If regex is True, keyword is treated as a regular expression. Default is False.
    """
    global g_search_log_stats

    g_search_log_stats.target_path = target_path
    g_search_log_stats.limit = limit
    g_search_log_stats.revision1 = revision1
    g_search_log_stats.revision2 = revision2
    g_search_log_stats.keyword = keyword
    g_search_log_stats.regex = regex

    path = convert_target_path(target_path)
    print_log(f"Searching SVN logs for: {target_path} with keyword: {keyword}, limit: {limit}, revisions: {revision1}:{revision2}", file=sys.stderr)
    text = get_svn_log_internal(path, f"-v -l {limit} -r {revision1}:{revision2}")
    update_log_stats(g_search_log_stats, text)
    matched_logs = search_svn_logs_internal(text, keyword, regex)
    return "\n\n".join(matched_logs)

@mcp.tool()
def search_svn_logs_continue() -> str:
    """
    Continues retrieving logs and searching from the previous call to search_svn_logs.
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

    print_log(f"Continuing to search older SVN logs for: {g_search_log_stats.target_path} with keyword: {g_search_log_stats.keyword}, limit: {g_search_log_stats.limit}, revisions: {start_revision}:{g_search_log_stats.revision2}", file=sys.stderr)
    text = get_svn_log_internal(convert_target_path(g_search_log_stats.target_path), f"-v -l {g_search_log_stats.limit} -r {start_revision}:{g_search_log_stats.revision2}")
    update_log_stats(g_search_log_stats, text)
    matched_logs = search_svn_logs_internal(text, g_search_log_stats.keyword, g_search_log_stats.regex)
    return "\n\n".join(matched_logs)

@mcp.tool()
def get_svn_info() -> str:
    """
    Retrieves SVN information.
    target_path must be absolute path or full URL.
    """
    return run_command("svn info")

@mcp.tool()
def get_svn_list(target_path: str, revision: str = "HEAD") -> str:
    """
    Retrieves a detailed list of SVN nodes.
    target_path must be absolute path or full URL.
    The target_path can be specified as a relative path from the repository or a relative path from the working copy.
    """

    print_log(f"Getting SVN list for: {target_path} at revision: {revision}", file=sys.stderr)
    path = convert_target_path(target_path)
    return get_svn_list_internal(path, revision)

def test_calls():
#   result = get_svn_commit_history(".scripts/mcp_svn.py")
#   print_log(f"SVN commit history:\n{result}", file=sys.stderr)
#   size = get_svn_node_size(".scripts/mcp_svn.py")
#   print_log(f"Size of .scripts/mcp_svn.py: {size}", file=sys.stderr)
#   size = get_svn_node_size(".scripts")
#   print_log(f"Size of .scripts: {size}", file=sys.stderr)
#   result =get_svn_tree(".", revision="HEAD", include_files=True, depth=2)
#   print_log(f"SVN tree:\n{result}", file=sys.stderr)
#   result =get_svn_tree("./", revision="HEAD", include_files=True, depth=1)
#   print_log(f"SVN tree:\n{result}", file=sys.stderr)
#   result =get_svn_tree(".", revision="HEAD", include_files=True, depth=0)
#   print_log(f"SVN tree:\n{result}", file=sys.stderr)
#   result =get_svn_tree(".", revision="HEAD", include_files=True, depth=-1)
#   print_log(f"SVN tree:\n{result}", file=sys.stderr)
#   result =get_svn_tree("trunk", revision="HEAD", include_files=False, depth=2)
#   print_log(f"SVN tree:\n{result}", file=sys.stderr)
#   result = search_svn_nodes("trunk/tools", revision="HEAD", file_name="*.py", depth=3)
#   print_log(f"Search result:\n{result}", file=sys.stderr)
#   result = search_svn_logs(".", keyword="refs #", regex=False, limit=10)
#   print_log(f"Search result:\n{result}", file=sys.stderr)
#   result = search_svn_logs(".", keyword=r"refs #\d+", regex=True, limit=10)
#   print_log(f"Search result:\n{result}", file=sys.stderr)

#   revision = convert_revision_to_number(".", "HEAD")
#   print_log(f"HEAD revision number: {revision}", file=sys.stderr)
#   revision = convert_revision_to_number(".", "BASE")
#   print_log(f"BASE revision number: {revision}", file=sys.stderr)
#   revision = convert_revision_to_number(".", " PREV")
#   print_log(f"PREV revision number: {revision}", file=sys.stderr)
#   revision = convert_revision_to_number(".", "COMMITTED")
#   print_log(f"COMMITTED revision number: {revision}", file=sys.stderr)
#   revision = convert_revision_to_number(".", "{2025-05-04}")
#   print_log(f"2025-05-04 revision number: {revision}", file=sys.stderr)
#   revision = convert_revision_to_number(".", "{2026-05-04}")
#   print_log(f"2026-05-04 revision number: {revision}", file=sys.stderr)
#   revision = convert_revision_to_number(".", "Rev.1234")
#   print_log(f"Rev.1234 revision number: {revision}", file=sys.stderr)

#   result = get_svn_logs(".", limit=10, revision1="HEAD", revision2="1")
#   print_log(f"SVN logs:\n{result}", file=sys.stderr)
#   result = get_svn_logs_continue()
#   print_log(f"SVN logs continue1:\n{result}", file=sys.stderr)
#   result = get_svn_logs_continue()
#   print_log(f"SVN logs continue2:\n{result}", file=sys.stderr)
#   result = get_svn_logs_continue()
#   print_log(f"SVN logs continue3:\n{result}", file=sys.stderr)
#   result = get_svn_logs_continue()
#   print_log(f"SVN logs continue4:\n{result}", file=sys.stderr)
#   result = get_svn_logs_continue()
#   print_log(f"SVN logs continue5:\n{result}", file=sys.stderr)

#   result = get_svn_status()
#   print_log(f"SVN status:\n{result}", file=sys.stderr)
#   result = get_svn_branch_base(".")
#   print_log(f"SVN branch base:\n{result}", file=sys.stderr)
#   result = get_svn_branch_base("branches/tools")
#   print_log(f"SVN branch base:\n{result}", file=sys.stderr)
#   result = get_svn_branch_base("branches/tools/mcp_redmine/mcp_redmine_b")
#   print_log(f"SVN branch base:\n{result}", file=sys.stderr)
#   result = get_svn_branch_base(".scripts/mcp_svn.py")
#   print_log(f"SVN branch base:\n{result}", file=sys.stderr)
#   result = get_svn_branch_base(".scripts/mcp_svna.py")
#   print_log(f"SVN branch base:\n{result}", file=sys.stderr)
#   result = get_svn_diff(".scripts/mcp_svn.py")
#   print_log(f"SVN diff:\n{result}", file=sys.stderr)
    result = get_create_branch_logs("branches")
    print_log(f"get_create_branch_logs():\n{result}", file=sys.stderr)

    return

def get_app_path():
    if getattr(sys, 'frozen', False):
        # EXEとして実行されている場合
        return os.path.dirname(os.path.abspath(sys.executable))
    else:
        # 通常のPythonスクリプトとして実行されている場合
        return os.path.dirname(os.path.abspath(__file__))


def read_credentials():
    """
    SVNの認証情報を読み取ります。
    """
    global g_username
    global g_password

    path = os.path.join(get_app_path(), "credentials.txt")
    print_log(f"Reading credentials from path: {path}", file=sys.stderr)
    if not os.path.exists(path):
        print_log(f"Credentials file not found", file=sys.stderr)
        return

    with open(path, "r") as f:
        lines = f.readlines()
        for line in lines:
            if match := re.match(r"svn_user\s*:\s*(\S+)", line):
                g_username = match.group(1)
            elif match := re.match(r"svn_password\s*:\s*(\S+)", line):
                g_password = match.group(1)

    print_log(f"Read credentials: username={g_username}", file=sys.stderr)
    return


def create_log_file():
    global g_log_file

    log_path = os.path.join(get_app_path(), "mcplog")
    os.makedirs(log_path, exist_ok = True)

    now = datetime.datetime.now()
    time_stamp = now.strftime('%Y%m%d_%H%M%S')
    log_path = os.path.join(log_path, "mcp_svn_" + time_stamp + ".log")
    g_log_file = open(log_path, "w", encoding="utf-8")


def get_code_work_space():
    import psutil

    for proc in psutil.process_iter(['name', 'cmdline']):
        try:
            if "Code.exe" in proc.info['name']:
                print_log(proc.info['cmdline'])
        except:
            pass

def main():
    parser = argparse.ArgumentParser(description="")
    parser.add_argument("-v", "--verbose", action='store_true')
    args = parser.parse_args()
#   print_log(args.verbose, file=sys.stderr)

    if args.verbose:
        create_log_file()

    get_code_work_space()
    print_log(f"Current working directory: {os.getcwd()}")
    read_credentials()
    get_repo_url_internal()
#   test_calls()

    mcp.run()

    if g_log_file:
        print_log("Server terminated.", file=sys.stderr)
        g_log_file.close()



if __name__ == "__main__":
    main()


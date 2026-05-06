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

RE_REVISION = re.compile(r"^r\d+")
RE_LOG_SEPARATOR = re.compile(r"^-{72}$")
RE_LIST_LINE_DIR  = re.compile(r"^\s*(\d+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\/\s*$")
RE_LIST_LINE_FILE = re.compile(r"^\s*(\d+)\s+(\S+)\s+(\d+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s*$")

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
    except Exception as e:
        return f"予期しないエラー: {str(e)}"

def try_command(command: str) -> str:
    """
    コマンドを実行して、その終了コードのみを返します。
    """
    try:
        result = subprocess.run(command, shell=True, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=False)
        return result.returncode
    except subprocess.CalledProcessError as e:
        return 1
    except Exception as e:
        print(f"予期しないエラー: {str(e)}", file=sys.stderr)
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
    if optional_args:
        return run_command(f"svn log {optional_args} {path}")
    return run_command(f"svn log {path}")

def get_svn_list_internal(path: str, revision: str = "HEAD") -> str:
    return run_command(f"svn list {path} -r {revision} -v")

def convert_relative_path(path: str) -> str:
    """
    1.作業コピーのURLを付加した完全なURLに変換します。
    2.1が存在しない場合は、リポジトリからの相対パスを完全なURLに変換します。
    3.1,2の両方が存在しない場合は、ローカルファイルシステム上の相対パスとして扱います。
    いずれも存在しない場合はNoneを返します。
    """
    repository_url = g_working_url + "/" + path
#   print(f"Trying working URL: {repository_url}", file=sys.stderr)
    if try_command(f"svn info {repository_url}") == 0:
        return repository_url
    
    repository_url = g_repo_url + "/" + path
#   print(f"Trying repository URL: {repository_url}", file=sys.stderr)
    if try_command(f"svn info {repository_url}") == 0:
        return repository_url

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

def get_svn_node_tree_internal(relative_path: str, revision: str = "HEAD", include_files: bool = False, depth: int = 1) -> SVNNode:
    """
    指定された相対パスのSVNノードのツリー構造を取得します。
    """
    node_kind = get_svn_node_kind(relative_path, revision)
    if node_kind == "file":
        print(f"Getting SVN node tree for file: {relative_path} at revision: {revision}", file=sys.stderr)
        return None

    path = convert_relative_path(relative_path)
#   print(f"Getting SVN node tree for directory: {relative_path} at revision: {revision} with path: {path}, revision: {revision}, depth: {depth}", file=sys.stderr)
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
                    child_node = get_svn_node_tree_internal('/'.join([relative_path, name]), revision, include_files, next_depth)
                    node.children.append(child_node)
                else:
                    child_node = SVNNode(url=path + "/" + name, basename=name, type="directory", revision=node_revision)
                    node.children.append(child_node)
            else:
#               print(f"Found current directory entry: {name} revision: {node_revision}", file=sys.stderr)
                node = SVNNode(url=path, basename=os.path.basename(relative_path), type="directory", revision=node_revision)

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
def search_svn_nodes(relative_path: str, revision: str = "HEAD", file_name: str = "*", depth: int = -1) -> str:
    """
    指定された相対パスのSVNノードを検索します。
    """
    print(f"Searching SVN nodes for: {relative_path} at revision: {revision}, file_name: {file_name}, depth: {depth}", file=sys.stderr)
    relative_path = relative_path.removesuffix('/')
    node = get_svn_node_tree_internal(relative_path, revision, True, depth)
    if node is None:
        return f"指定されたパスはファイルです: {relative_path}"

    matched_nodes = match_svn_node_by_name(node, file_name)
#   for matched_node in matched_nodes:
#       print(f"Matched node: {matched_node.url} type: {matched_node.type} revision: {matched_node.revision} size: {matched_node.size}", file=sys.stderr)    
        
    path = convert_relative_path(relative_path)
    paths = [matched_node.url.replace(path, relative_path, 1) for matched_node in matched_nodes]
    return "\n".join(paths)

@mcp.tool()
def get_svn_tree(relative_path: str, revision: str = "HEAD", include_files: bool = False, depth: int = -1) -> str:
    """
    指定された相対パスのSVNツリー構造を取得します。
    """
    print(f"Getting SVN tree for: {relative_path} at revision: {revision}, include_files: {include_files}, depth: {depth}", file=sys.stderr)
    relative_path = relative_path.removesuffix('/')
    node = get_svn_node_tree_internal(relative_path, revision, include_files, depth)
    if node is None:
        return f"指定されたパスはファイルです: {relative_path}"

#   print(f"SVN tree for {relative_path}:\n{node}", file=sys.stderr)
    return format_svn_tree(node)



@mcp.tool()
def get_svn_node_size(relative_path: str, revision: str = "HEAD") -> str:
    """
    指定された相対パスのSVNノードのサイズを取得します。
    """
    path = convert_relative_path(relative_path)
    print(f"Getting SVN node size for: {relative_path}", file=sys.stderr)
    if get_svn_node_kind(relative_path, revision) == "directory":
        return "0"

    result = run_command(f"svn list {path} -r {revision} -v")
    for line in result.split('\n'):
        match = re.match(r"^\s*\d+\s+\S+\s+(\d+)?", line)
        if match:
            size = match.group(1)
            return size
        
    print(f"Size not found in SVN info output for: {relative_path}", file=sys.stderr)
    return "Size not found"

@mcp.tool()
def get_svn_node_kind(relative_path: str, revision: str = "HEAD") -> str:
    """
    指定された相対パスが存在するか、存在する場合はノードの種類を取得します。
    """
    path = convert_relative_path(relative_path)
#   print(f"Getting SVN node kind for: {relative_path}", file=sys.stderr)
    result = run_command(f"svn info {path}")
    for line in result.split('\n'):
        if line.startswith("Node Kind:"):
            return line.split("Node Kind:")[1].strip()
    return "non-existent"

@mcp.tool()
def blame_svn_file(relative_path: str, revision: str = "HEAD", start_line: int = 1, end_line: int = 0) -> str:
    """
    指定された相対パスのSVNファイルのblame情報を取得します。
    blame情報は、各行の最終変更リビジョンと著者を含みます。
    start_lineとend_lineを指定することで、ファイルの特定の行範囲を取得できます。
    マイナス値を指定すると末尾からの行数を表します。end_lineが0の場合は、ファイルの末尾までを取得します。
    """
    path = convert_relative_path(relative_path)
    print(f"Getting SVN blame for: {relative_path} at revision: {revision}, start_line: {start_line}, end_line: {end_line}", file=sys.stderr)
    text = run_command(f"svn blame {path} -r {revision}")
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
def cat_svn_file(relative_path: str, revision: str = "HEAD", start_line: int = 1, end_line: int = 0) -> str:
    """
    指定された相対パスのSVNファイルの内容を取得します。
    start_lineとend_lineを指定することで、ファイルの特定の行範囲を取得できます。
    マイナス値を指定すると末尾からの行数を表します。end_lineが0の場合は、ファイルの末尾までを取得します。
    """
    path = convert_relative_path(relative_path)
    print(f"Getting SVN file content for: {relative_path} at revision: {revision} start_line: {start_line} end_line: {end_line}", file=sys.stderr)
    text = run_command(f"svn cat {path} -r {revision}")
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
def export_svn_file(relative_path: str, revision: str = "HEAD", output_path: str = "_tmp_export") -> str:
    """
    指定された相対パスをoutput_pathにエクスポートします。
    """
    if not is_safe_path(output_path):
        return f"安全でないパスが指定されました: {output_path}"

    path = convert_relative_path(relative_path)
    if get_svn_node_kind(relative_path) == "directory":
        output_path = os.path.join(output_path, os.path.basename(relative_path))
    else:
        if os.path.dirname(output_path) == "":
            output_path = os.path.join(output_path, os.path.basename(relative_path))

    print(f"Exporting SVN file: {relative_path} at revision: {revision} to output path: {output_path}({os.path.dirname(output_path)})", file=sys.stderr)
    os.makedirs(os.path.dirname(output_path), exist_ok=True) if output_path else None
    result = run_command(f"svn export {path} -r {revision} {output_path}")
    return result

@mcp.tool()
def get_svn_diff_by_revision(relative_path: str, revision1: str, revision2: str) -> str:
    """
    指定された相対パスのリビジョン間の差分を取得します。
    """
    path = convert_relative_path(relative_path)
    print(f"Getting SVN diff for: {relative_path} between revisions: {revision1} and {revision2}", file=sys.stderr)
    return run_command(f"svn diff -r {revision1}:{revision2} {path}")

@mcp.tool()
def get_svn_diff_by_url(relative_path1: str, relative_path2: str, revision1="HEAD", revision2="HEAD") -> str:
    """
    指定された相対パスのリビジョン間の差分を取得します。
    """
    path1 = convert_relative_path(relative_path1)
    path2 = convert_relative_path(relative_path2)
    print(f"Getting SVN diff for: -r {revision1} {path1} -r {revision2} {path2}", file=sys.stderr)
    return run_command(f"svn diff -r {revision1} {path1} -r {revision2} {path2}")

@mcp.tool()
def get_svn_commit_history(relative_path: str) -> str:
    """
    リポジトリ内の指定された相対パスのSVNコミット履歴(Revisionのみ)を取得します。
    """
    result = []
    print(f"Getting SVN commit history for: {relative_path}", file=sys.stderr)
    path = convert_relative_path(relative_path)
    log_result = get_svn_log_internal(f"{path}", "-q")
    for line in log_result.split('\n'):
        print(f"Processing log line: {line}", file=sys.stderr)
        revision_match = RE_REVISION.match(line)
        if revision_match:
            result.append(revision_match.group())

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
    return run_command("svn info")

@mcp.tool()
def get_svn_list(relative_path: str, revision: str = "HEAD") -> str:
    """
    指定された相対パスのSVNリスト(詳細情報付き)を取得します。
    """

    print(f"Getting SVN list for: {relative_path} at revision: {revision}", file=sys.stderr)
    path = convert_relative_path(relative_path)
    return get_svn_list_internal(path, revision)

def test_calls():
#   get_svn_commit_history(".scripts/mcp_svn.py")
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
    return

def main():
    get_repo_url_internal()

    test_calls()

    # コマンドライン引数を処理
    mcp.run()



if __name__ == "__main__":
    main()


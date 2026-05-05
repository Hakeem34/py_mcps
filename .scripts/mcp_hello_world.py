import datetime
from mcp.server.fastmcp import FastMCP

# MCPサーバーのインスタンスを作成
mcp = FastMCP("MyFirstMCP")
logfile = open(r"C:\src\tools\mcp_redmine\mcp_hello_world.log", "w")

@mcp.tool()
def hello_world(name: str = "World") -> str:
    """
    指定された名前に挨拶を返します。
    """
    result = f"Hello, {name}! This is from your custom MCP server. Current time: {datetime.datetime.now()}"
    logfile.write(result + "\n")
    logfile.flush()
    return result

if __name__ == "__main__":
    # MCPは標準入出力(stdio)を介してクライアントと通信します
    logfile.write("Starting MCP server...\n")
    logfile.flush()
    mcp.run()
    logfile.close()

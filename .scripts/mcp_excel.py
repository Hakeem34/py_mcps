import os
import re
import sys
import openpyxl
import dataclasses
from pathlib import Path
from mcp.server.fastmcp import FastMCP


g_current_wb_path = ""
g_current_wb      = None


# FastMCPのインスタンスを作成
mcp = FastMCP()


@mcp.tool()
def get_work_sheet_list(work_book_path : str) -> str:
    """
    Get the list of sheets in the workbook.
    """
    global g_current_wb_path
    global g_current_wb

    if g_current_wb_path == work_book_path:
        wb = g_current_wb
    else:
        if g_current_wb:
            g_current_wb.close()

        wb = openpyxl.load_workbook(work_book_path,  data_only=True)
        g_current_wb_path = work_book_path
        g_current_wb      = wb

    sheets = []
    for ws in wb.worksheets:
        sheets.append(ws.title)

    print('\n'.join(sheets), file=sys.stderr)
    return '\n'.join(sheets)

def main():
    get_work_sheet_list(".scripts\\test.xlsx")
    mcp.run()


if __name__ == "__main__":
    main()


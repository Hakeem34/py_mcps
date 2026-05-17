import os
import re
import sys
import openpyxl
from openpyxl.utils import get_column_letter

import zipfile
import re
from lxml import etree as ET
from oletools.olevba import VBA_Parser

import dataclasses
from pathlib import Path
from mcp.server.fastmcp import FastMCP



@dataclasses.dataclass
class WorkSheetCache:
    ws_obj        : openpyxl.worksheet
    hidden_rows   : list
    hidden_cols   : list
    top_left_addr : str = ""
    top_left_val  : str = ""

@dataclasses.dataclass
class WorkBookCache:
    path        : str = ""
    wb_obj      : openpyxl.workbook = None
    work_sheets : dict = None



NS = {
    "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "xdr": "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
}

@dataclasses.dataclass
class WorkSheetXML:
    rid      : str = ""
    name     : str = ""
    xml_path : str = ""
    drawing  : dict = dataclasses.field(default_factory=dict)

@dataclasses.dataclass
class WorkBookXML:
    work_sheets    : dict = dataclasses.field(default_factory=dict)
    vba_macros     : list = dataclasses.field(default_factory=list)
    shared_strings : list = dataclasses.field(default_factory=list)

@dataclasses.dataclass
class SharedString:
    text           : str = ""
    ruby           : str = ""

@dataclasses.dataclass
class DrawingXML:
    xml_path : str = ""
    shapes   : list = dataclasses.field(default_factory=list)

@dataclasses.dataclass
class ShapeInfo:
    xml_path : str = ""
    id       : int = 0
    name     : str = ""
    text     : str = ""
    col      : int = 0
    row      : int = 0
    geometry : str = ""
    width    : int = 0
    height   : int = 0

@dataclasses.dataclass
class VBA_macro:
    module   : str = ""
    lines    : list = dataclasses.field(default_factory=list)


g_current_wb_path = ""
g_current_wb      = None
g_wb_cache        = None

# FastMCPのインスタンスを作成
mcp = FastMCP()


def parse_drawing_xml(z : zipfile.ZipFile, draw_obj : DrawingXML):
    """
    図形描画のxml解析
    """
    shapes = []
    xml = ET.fromstring(z.read(draw_obj.xml_path))
    for anchor in xml.xpath("//xdr:twoCellAnchor | //xdr:oneCellAnchor", namespaces=NS):
        shape = ShapeInfo()
        # ID
        shape.id   = anchor.xpath(".//xdr:cNvPr/@id", namespaces=NS)[0]
        # テキスト
        shape.text = '\n'.join(anchor.xpath(".//a:t/text()", namespaces=NS))
        # 図形名
        shape.name = anchor.xpath(".//xdr:cNvPr/@name", namespaces=NS)
        # セル座標
        shape.col  = int(anchor.xpath(".//xdr:from/xdr:col/text()", namespaces=NS)[0]) + 1
        shape.row  = int(anchor.xpath(".//xdr:from/xdr:row/text()", namespaces=NS)[0]) + 1
        # 幾何学情報、サイズ
        shape.geometry = anchor.xpath(".//a:prstGeom/@prst", namespaces=NS)
        size           = anchor.xpath(".//a:xfrm/a:ext", namespaces=NS)
        if size:
            cx = int(size[0].attrib["cx"])
            cy = int(size[0].attrib["cy"])
            shape.width  = int(cx / 914400 * 25.4)
            shape.height = int(cy / 914400 * 25.4)

        print(f'  ID:{int(shape.id):04} NAME:{shape.name} TEXT:{shape.text}')
        shapes.append(shape)

    return shapes


def parse_vba(wb_path):
    """
    VBAマクロの解析(oletoolsにほぼお任せ)
    """
    vba_macros = []
    # 3. VBAマクロの検索
    try:
        # xlsmファイルを直接指定するだけで、内部の vbaProject.bin を自動解析
        vba_parser = VBA_Parser(wb_path)
        if vba_parser.detect_vba_macros():
            # マクロコードをストリーム抽出し、1行ずつ検索
            for subfilename, stream_path, vba_filename, vba_code in vba_parser.extract_macros():
                if vba_code:
                    macro = VBA_macro(module = vba_filename, lines = vba_code)
                    vba_macros.append(macro)
        vba_parser.close()
    except Exception as e:
        # マクロが含まれない、または破損している場合はスキップ
        pass

    return vba_macros

def parse_work_sheet(z : zipfile.ZipFile, wb_obj : WorkBookXML):
    """
    ワークシートのXML解析
    """
    for ws_name, ws_obj in wb_obj.work_sheets.items():
        #シートのxmlを確認する
        ws_xml = ET.fromstring(z.read(ws_obj.xml_path))
        for c in ws_xml.xpath('//main:c', namespaces=NS):
            cell_addr = c.get('r')      # A1 とか
            cell_type = c.get('t')      # s, inlineStr, b など
            v = c.find('{*}v')
            print(f'cell={cell_addr} type={cell_type} value={v.text}')
            pass

#       print(ws_xml)
#       print(dir(ws_xml))
        for item in ws_xml.items():
            print(item)
#       for event, elem in ET.iterparse(ws_xml, events=('end',)):
#           print(elem.tag)
        
        #シートに紐づくdrawingsをチェックする
        rel_path = "xl/worksheets/_rels/" + os.path.basename(ws_obj.xml_path).replace(".xml", ".xml.rels")
#       print(f"check for {rel_path} from {ws_obj.xml_path}")
        if rel_path in z.namelist():
            rel_xml = ET.fromstring(z.read(rel_path))
            for rel in rel_xml:
                if "drawing" in rel.attrib["Type"]:
                    draw_obj = DrawingXML()
                    draw_obj.xml_path = "xl/drawings/" + rel.attrib["Target"].split("/")[-1]
                    ws_obj.drawing[draw_obj.xml_path] = draw_obj
                    print(f'[rels] {ws_name} -> {draw_obj.xml_path}', file=sys.stderr)
                    parse_drawing_xml(z, draw_obj)
    return

def parse_shared_string(z : zipfile.ZipFile, wb_obj : WorkBookXML):
    """
    共有文字列のxml解析
    """
    shared_strings = []
    if 'xl/sharedStrings.xml' in z.namelist():
        string_index = 0
        ss_xml = ET.fromstring(z.read('xl/sharedStrings.xml'))
        for si in ss_xml.xpath('//main:si', namespaces=NS):
#           full_text = "".join(t.text for t in si.findall('{*}t') if t.text)
            full_text = ''.join(t.text for t in si.xpath('.//main:t[not(ancestor::main:rPh)]', namespaces=NS)if t.text)
            ruby_text = ''.join(t.text for t in si.xpath('.//main:t[ancestor::main:rPh]', namespaces=NS)if t.text)
            print(full_text)
#           print(ruby_text)
            ss = SharedString()
            ss.text = full_text
            ss.ruby = ruby_text
            shared_strings.append(ss)
    
    wb_obj.shared_strings = shared_strings
    return

def parse_work_book(z : zipfile.ZipFile):
    """
    ワークブックのxml解析
    """
    # workbook.xmlを読む
    wb_xml = ET.fromstring(z.read("xl/workbook.xml"))
    # workbook.xml.relsを読む
    wb_rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))

    # rId → sheetX.xml のマップを作る
    rid_to_sheetxml = {
        rel.attrib["Id"]: rel.attrib["Target"]
        for rel in wb_rels
        if "worksheet" in rel.attrib["Type"]
    }
#   print(rid_to_sheetxml)
    wb_obj = WorkBookXML()

    for sheet in wb_xml.xpath("//main:sheet", namespaces=NS):
        ws_obj = WorkSheetXML()
        ws_obj.name = sheet.attrib["name"]
        ws_obj.rid = sheet.attrib["{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"]
        ws_obj.xml_path = "xl/" + rid_to_sheetxml[ws_obj.rid]
        wb_obj.work_sheets[ws_obj.name] = ws_obj

    parse_work_sheet(z, wb_obj)
    parse_shared_string(z, wb_obj)
    return ws_obj


def parse_by_xml(wb_path):
    """
    xmlによるExcelファイル解析
    """
    with zipfile.ZipFile(wb_path) as z:
        wb_obj            = parse_work_book(z)
        wb_obj.vba_macros = parse_vba(wb_path)
    return

def grep_excel_fast(file_path, search_word):
    print(f"検索開始: {file_path} (キーワード: '{search_word}')\n" + "-"*50, file=sys.stderr)
    
    matched_string_indexes = set()  # キーワードが含まれる共有文字列のID(siインデックス)を格納
    has_shared_strings = False

    # 1. 共有文字列 (sharedStrings.xml) を <si> 単位でインデックス管理
    with zipfile.ZipFile(file_path, 'r') as z:
        if 'xl/sharedStrings.xml' in z.namelist():
            has_shared_strings = True
            string_index = 0
            
            with z.open('xl/sharedStrings.xml') as f:
                # <si> タグの閉じタグ（end）をトリガーにする
                for event, elem in ET.iterparse(f, events=('end',)):
                    if elem.tag.endswith('si'):
                        # <si> 内にあるすべての <t> タグのテキストを結合
                        # これにより、文字単位で装飾が分かれているセルも1つの文字列として結合される
                        full_text = "".join(t.text for t in elem.findall('{*}t') if t.text)
                        
                        if search_word in full_text:
                            matched_string_indexes.add(str(string_index))
                            print(f'SharedText : {str(string_index)} = {elem.text}', file=sys.stderr)
                        string_index += 1
                        elem.clear() # メモリ解放

        # 2. 各シート、Shapeの検索
        for file_info in z.infolist():
            filename = file_info.filename

            # セルデータの検索 (sheetX.xml)
            if 'xl/worksheets/sheet' in filename and filename.endswith('.xml'):
                sheet_name = filename.split('/')[-1]
                with z.open(file_info) as f:
                    # iterparseで1要素ずつ処理し、メモリ消費を極小に抑える
                    for event, elem in ET.iterparse(f, events=('end',)):
                        # <c> はセル要素。t="s" は「共有文字列を使用している」という意味
                        if elem.tag.endswith('c'):
                            is_shared = (elem.get('t') == 's')
                            v_elem = elem.find('{*}v') # 子要素の <v> タグ（値）を探す
                            
                            if v_elem is not None and v_elem.text:
                                print(f'v_elem.text = {v_elem.text}', file = sys.stderr)
                                cell_val = v_elem.text.strip()
                                # 共有文字列(t="s")の場合：si インデックスと照合
                                if is_shared and cell_val in matched_string_indexes:
                                    print(f"[セル] {sheet_name} 内（セル: {elem.get('r')}）に一致（文字列データ）:", file=sys.stderr)
                                # 数値や日付、直書き文字列の場合：直接キーワードが含まれるか確認
                                elif not is_shared and search_word in cell_val:
                                    print(f"[セル] {sheet_name} 内（セル: {elem.get('r')}）に一致（数値/その他）", file=sys.stderr)
                            elem.clear()
                        else:
#                           print(f'elem.tag == {elem.tag}', file=sys.stderr)
                            pass

            # Shape（図形）テキストの検索 (drawingX.xml)
            elif 'xl/drawings/drawing' in filename and filename.endswith('.xml'):
                drawing_name = filename.split('/')[-1]
                with z.open(file_info) as f:
                    for event, elem in ET.iterparse(f, events=('end',)):
                        # <a:t>タグ（図形内のテキスト要素）を抽出
                        if elem.tag.endswith('t') and elem.text and search_word in elem.text:
                            print(f"[Shape] {drawing_name} 内に一致: {elem.text.strip()}", file=sys.stderr)
                        elem.clear()

    # 3. VBAマクロの検索
    try:
        # xlsmファイルを直接指定するだけで、内部の vbaProject.bin を自動解析
        vba_parser = VBA_Parser(file_path)
        if vba_parser.detect_vba_macros():
            # マクロコードをストリーム抽出し、1行ずつ検索
            for subfilename, stream_path, vba_filename, vba_code in vba_parser.extract_macros():
                if vba_code:
                    for line_no, line in enumerate(vba_code.splitlines(), start=1):
                        if search_word in line:
                            print(f"[VBAマクロ] モジュール: {vba_filename} ({line_no}行目): {line.strip()}", file=sys.stderr)
        vba_parser.close()
    except Exception as e:
        # マクロが含まれない、または破損している場合はスキップ
        pass

    print("-"*50 + "\n検索終了", file=sys.stderr)


def get_hidden_rows(ws) -> list[int]:
    hidden_rows = []
    for row in range(1, ws.max_row + 1):
        if ws.row_dimensions[row].hidden:
            hidden_rows.append(row)
#          print(f'{ws.title} row={row} は非表示です max_row = {ws.max_row}', file=sys.stderr)
    return hidden_rows

def get_hidden_cols(ws) -> list[int]:
    hidden_cols = []
    for col in range(1, ws.max_column + 1):
        if ws.column_dimensions[get_column_letter(col)].hidden:
            hidden_cols.append(col)
#           print(f'{ws.title} col={col} は非表示です max_col = {ws.max_column}', file=sys.stderr)
    return hidden_cols

def get_cell_addr(row : int, col : int) -> str:
    col_letter = get_column_letter(col)
    return f"{col_letter}{row}"

def get_sheet_header(ws_c : WorkSheetCache):
    """
    シートの見出し（最も左上に位置するセル）のテキストを取得
    """
    ws = ws_c.ws_obj
    for r_idx, row in enumerate(ws.rows):
        if (r_idx + 1) in ws_c.hidden_rows:
            continue

        for c_idx, cell in enumerate(row):
            if (c_idx + 1) in ws_c.hidden_cols:
                continue

            if cell.value is not None:
                ws_c.top_left_addr = get_cell_addr(r_idx + 1, c_idx + 1)
                ws_c.top_left_val  = str(cell.value)
                text = f'{ws_c.top_left_addr}:{ws_c.top_left_val}'
#               print(text, file=sys.stderr)
                return text


def cache_work_sheets(wb_c : WorkBookCache):
    worksheets = {}
    for ws in wb_c.wb_obj:
        hidden_rows = get_hidden_rows(ws)
        hidden_cols = get_hidden_cols(ws)
        sheet_cache = WorkSheetCache(ws_obj=ws, hidden_rows=hidden_rows, hidden_cols=hidden_cols)
        get_sheet_header(sheet_cache)
        worksheets[ws.title] = sheet_cache

    wb_c.work_sheets = worksheets
    return

def get_workbook_cache(wb_path : str) -> WorkBookCache:
    global g_wb_cache

    if g_wb_cache and g_wb_cache.path != "" and wb_path == "":
        return g_wb_cache
    
    if g_wb_cache and g_wb_cache.path == wb_path:
        return g_wb_cache

    try:
        if g_wb_cache and g_wb_cache.wb_obj:
            g_wb_cache.wb_obj.close()

        g_wb_cache = WorkBookCache()
        g_wb_cache.wb_obj = openpyxl.load_workbook(wb_path,  data_only=True)
        g_wb_cache.path   = wb_path
        cache_work_sheets(g_wb_cache)
    except Exception as e:
        print(f"{wb_path}が開けませんでした {e}", file=sys.stderr)
        g_wb_cache.path   = ""
        g_wb_cache.wb_obj = None
        g_wb_cache = None

    return g_wb_cache

@mcp.tool()
def get_work_sheet_header(sheet_name : str, wb_path : str = "") -> str:
    """
    Get top left cell text in work sheet.
    """
    wb_c = get_workbook_cache(wb_path)
    if not wb_c:
        return f"Can't open {wb_path}."

    if sheet_name in wb_c.work_sheets:
        ws_c = wb_c.work_sheets[sheet_name]
        if ws_c.top_left_addr != "":
            text = ws_c.top_left_addr + ":" + ws_c.top_left_val
        else:
            text = f''
    else:
        text = f'このブック({wb_c.path})には{sheet_name}がありません'
    return text

@mcp.tool()
def get_work_sheet_list(wb_path : str = "") -> str:
    """
    Get the list of sheets in the workbook.
    """
    global g_current_wb_path
    global g_current_wb

    print(f'get_work_sheet_list : {wb_path}', file=sys.stderr)
    wb_c = get_workbook_cache(wb_path)
    if not wb_c:
        return f"Can't open {wb_path}."

    sheets = []
    for ws in wb_c.wb_obj.worksheets:
        sheets.append(ws.title)

#   print('\n'.join(sheets), file=sys.stderr)
    return '\n'.join(sheets)

def main():
#   sheet_names = get_work_sheet_list("sample\\test.xlsx")
#   for sheet_name in sheet_names.split('\n'):
#       result = get_work_sheet_header(sheet_name)
#       print(f'["{sheet_name}"]:{result}')

#   result = get_work_sheet_header("Unknown Sheet")
#   print(f'["Unknown Sheet"]:{result}')
    grep_excel_fast("sample\\test_macro.xlsm", "コメント")
#   grep_excel_fast("sample\\test.xlsx", "コメント")
    parse_by_xml("sample\\test_macro.xlsm")

    mcp.run()


if __name__ == "__main__":
    main()


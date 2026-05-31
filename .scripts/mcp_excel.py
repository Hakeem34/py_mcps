import os
import re
import sys
import argparse
import datetime
import openpyxl
from openpyxl.utils import get_column_letter

import zipfile
import re
from lxml import etree as ET
from oletools.olevba import VBA_Parser

import dataclasses
from pathlib import Path
from pathlib import PurePosixPath
from mcp.server.fastmcp import FastMCP

EMU_PER_PIXEL = 9525
EMU_PER_MM    = 36000
EMU_PER_INCH  = 914400

NS = {
    "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r"   : "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "xdr" : "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing",
    "a"   : "http://schemas.openxmlformats.org/drawingml/2006/main",
}

NS_CHART  = {
    'c': 'http://schemas.openxmlformats.org/drawingml/2006/chart',
    'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
}

NS_TC     = {'tc': 'http://schemas.microsoft.com/office/spreadsheetml/2018/threadedcomments'}
NS_PERSON = {'p': 'http://schemas.microsoft.com/office/spreadsheetml/2018/person'}

@dataclasses.dataclass
class CellInfo:
    text     : str = ""



@dataclasses.dataclass
class WorkSheetXML:
    rid      : str = ""
    name     : str = ""
    xml_path : str = ""
    comments : list = dataclasses.field(default_factory=list)
    images   : list = dataclasses.field(default_factory=list)
    charts   : list = dataclasses.field(default_factory=list)
    shapes   : list = dataclasses.field(default_factory=list)

@dataclasses.dataclass
class WorkBookXML:
    wb_path        : str
    work_sheets    : dict = dataclasses.field(default_factory=dict)
    vba_macros     : list = dataclasses.field(default_factory=list)
    shared_strings : list = dataclasses.field(default_factory=list)
    person_info    : dict = dataclasses.field(default_factory=dict)

@dataclasses.dataclass
class SharedString:
    text           : str = ""
    ruby           : str = ""

@dataclasses.dataclass
class CommentInfo:
    author      : str
    cell        : str
    text        : str

@dataclasses.dataclass
class CommentXML:
    new_comment : bool = False
    xml_path    : str = ""
    comments    : list = dataclasses.field(default_factory=list)

@dataclasses.dataclass
class ShapeInfo:
    id       : int = 0
    name     : str = ""
    text     : str = ""
    col      : int = 0
    row      : int = 0
    geometry : str = ""
    off_x    : int = 0
    off_y    : int = 0
    width    : int = 0
    height   : int = 0
    children : list = dataclasses.field(default_factory=list)

@dataclasses.dataclass
class ChartInfo:
    xml_path : str = ""
    title    : str = ""
    series   : list = dataclasses.field(default_factory=list)
    axis     : dict = dataclasses.field(default_factory=dict)

@dataclasses.dataclass
class VBA_macro:
    module   : str = ""
    lines    : list = dataclasses.field(default_factory=list)


g_current_wb      = None
g_log_file        = None

# FastMCPのインスタンスを作成
mcp = FastMCP()

def print_log(text, file=sys.stderr):
    print(text, file=sys.stderr)
    if g_log_file:
        print(text, file=g_log_file)
        g_log_file.flush()

def get_app_path():
    if getattr(sys, 'frozen', False):
        # EXEとして実行されている場合
        return os.path.dirname(os.path.abspath(sys.executable))
    else:
        # 通常のPythonスクリプトとして実行されている場合
        return os.path.dirname(os.path.abspath(__file__))

def create_log_file():
    global g_log_file

    log_path = os.path.join(get_app_path(), "mcplog")
    os.makedirs(log_path, exist_ok = True)

    now = datetime.datetime.now()
    time_stamp = now.strftime('%Y%m%d_%H%M%S')
    log_path = os.path.join(log_path, "mcp_excel_" + time_stamp + ".log")
    g_log_file = open(log_path, "w", encoding="utf-8")

def get_shape_text(sp, ns):
    paragraphs = []

    # paragraph単位
    for p in sp.xpath('.//a:p', namespaces=ns):
        runs = []
        # run単位
        for r in p.xpath('./a:r | ./a:br', namespaces=ns):
            # 改行
            if r.tag.endswith('br'):
                runs.append('\n')
                continue

            # text
            texts = r.xpath('./a:t/text()', namespaces=ns)
            if texts:
                runs.append(texts[0])

        paragraphs.append(''.join(runs))

    return '\n'.join(paragraphs)

def get_comment_text(text_elem):
    # 普通コメント:
    # <text><t>AAA</t></text>
    texts = text_elem.xpath('.//main:t', namespaces=NS)
    return ''.join(t.text for t in texts if t.text)

def get_chart_text(text_elem):
    # chart value
    vals = text_elem.xpath('.//c:v', namespaces=NS_CHART)
    if vals:
        return ''.join(v.text for v in vals if v.text)

    # drawing text
    texts = text_elem.xpath('.//a:t', namespaces=NS_CHART)
    return ''.join(t.text for t in texts if t.text)

def load_persons(z : zipfile.ZipFile):
    persons = {}

    # person.xml 探索
    for name in z.namelist():
        if not name.startswith('xl/persons/'):
            continue

        if not name.endswith('.xml'):
            continue

        root = ET.fromstring(z.read(name))
        for person in root.xpath('//p:person', namespaces=NS_PERSON):
            person_id = person.get('id')
            display_name = (person.get('displayName') or '')
#           user_id = (person.get('userId') or '')
#           persons[person_id] = {'display_name': display_name, 'user_id': user_id,}
            persons[person_id] = display_name

    return persons


def get_rels_path(parts_path: str):
    p = PurePosixPath(parts_path)
    return str(p.parent / '_rels' / f'{p.name}.rels')


def parse_shape(sp):
    shape = ShapeInfo()

    cNvPr = sp.find('./xdr:nvSpPr/xdr:cNvPr', namespaces=NS)
    if cNvPr is not None:
        shape.id = cNvPr.get('id')
        shape.name = cNvPr.get('name') or ''

        # 幾何学情報、サイズ
        shape.geometry = sp.xpath(".//a:prstGeom/@prst", namespaces=NS)
        pos = sp.xpath(".//a:xfrm/a:off", namespaces=NS)
        if pos:
            x = int(pos[0].attrib["x"])
            y = int(pos[0].attrib["y"])
            shape.off_x = int(x / EMU_PER_MM)
            shape.off_y = int(y / EMU_PER_MM)

        size = sp.xpath(".//a:xfrm/a:ext", namespaces=NS)
        if size:
            cx = int(size[0].attrib["cx"])
            cy = int(size[0].attrib["cy"])
            shape.width  = int(cx / EMU_PER_MM)
            shape.height = int(cy / EMU_PER_MM)

#   shape.text = '\n'.join(sp.xpath('.//a:t/text()', namespaces=NS))
    shape.text = get_shape_text(sp, NS)
    print_log(f'[{shape.id}][{shape.name}]:{shape.text}')
    print_log(f'  geometry : {shape.geometry}')
    print_log(f'  offset   : ({shape.off_x}, {shape.off_y})')
    print_log(f'  size     : {shape.width} x {shape.height}')
    return shape

def parse_group_shape(grp):
    shape = ShapeInfo()
    cNvPr = grp.find('./xdr:nvGrpSpPr/xdr:cNvPr', namespaces=NS)
    if cNvPr is not None:
        shape.id = cNvPr.get('id')
        shape.name = cNvPr.get('name') or ''

    print_log(f'[{shape.id}][{shape.name}]')
    # group内shape: descendant shapes in a group
    for sp in grp.xpath('./xdr:sp', namespaces=NS):
        print_log(f"xdr:sp")
        shape.children.append(parse_shape(sp))

    for subgrp in grp.xpath('./xdr:grpSp', namespaces=NS):
        print_log(f"xdr:grpSp2")
        shape.children.append(parse_group_shape(subgrp))

    return shape

def parse_drawing_xml(z : zipfile.ZipFile, draw_xml_path : str, ws_obj : WorkSheetXML):
    """
    図形描画のxml解析
    """
    xml = ET.fromstring(z.read(draw_xml_path))
    for anchor in xml.xpath("//xdr:twoCellAnchor | //xdr:oneCellAnchor | //xdr:absoluteAnchor", namespaces=NS):
#       print_log(f'parse_anchor():{anchor}')

        # normal shape
        for sp in anchor.xpath('./xdr:sp', namespaces=NS):
            print_log(f"xdr:sp1")
            shape = parse_shape(sp)

            # セル座標
            shape.col  = int(anchor.xpath(".//xdr:from/xdr:col/text()", namespaces=NS)[0]) + 1
            shape.row  = int(anchor.xpath(".//xdr:from/xdr:row/text()", namespaces=NS)[0]) + 1
            ws_obj.shapes.append(shape)

        for grp in anchor.xpath('./xdr:grpSp', namespaces=NS):
            print_log(f"xdr:grpSp1")
            shape = parse_group_shape(grp)
            # セル座標
            shape.col  = int(anchor.xpath(".//xdr:from/xdr:col/text()", namespaces=NS)[0]) + 1
            shape.row  = int(anchor.xpath(".//xdr:from/xdr:row/text()", namespaces=NS)[0]) + 1
            ws_obj.shapes.append(shape)
 
    rel_path = get_rels_path(draw_xml_path)
    if rel_path in z.namelist():
#       print_log(f'[rels] {draw_xml_path} -> {rel_path}')
        rel_xml = ET.fromstring(z.read(rel_path))
        for rel in rel_xml:
            if 'chart' in rel.attrib["Type"]:
#               print_log(f'chart : {rel.attrib["Target"]}')
                chart_path = rel.attrib["Target"].replace('../', 'xl/')
                parse_chart(z, chart_path, ws_obj)
            elif 'image' in rel.attrib["Type"]:
#               print_log(f'image : {rel.attrib["Target"]}')
                ws_obj.images.append(rel.attrib["Target"])
                
        

    return 


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
        print_log(f'------------------------------------------- parse_work_sheet : {ws_name} -------------------------------------------')
        #シートのxmlを確認する
        ws_xml = ET.fromstring(z.read(ws_obj.xml_path))
        for c in ws_xml.xpath('//main:c', namespaces=NS):
            cell_addr = c.get('r')      # A1 とか
            cell_type = c.get('t')      # s, inlineStr, b など
            v = c.find('{*}v')
            if (v != None):
#               print(dir(v))
#               print_log(f'  cell={cell_addr} type={cell_type} value={v.text}')
                pass

       
        #シートに紐づくdrawings / commentsをチェックする
        rel_path = get_rels_path(ws_obj.xml_path)
#       print(f"check for {rel_path} from {ws_obj.xml_path}")
        if rel_path in z.namelist():
            rel_xml = ET.fromstring(z.read(rel_path))
            for rel in rel_xml:
                if "drawing" in rel.attrib["Type"]:
                    draw_xml_path = "xl/drawings/" + rel.attrib["Target"].split("/")[-1]
#                   print_log(rel.attrib["Target"])
                    print_log(f'[rels] {ws_name} -> {draw_xml_path}', file=sys.stderr)
                    parse_drawing_xml(z, draw_xml_path, ws_obj)
                elif "threadedComment" in rel.attrib["Type"]:
#                   print_log(rel.attrib["Target"])
                    comment_obj = CommentXML(new_comment=True)
                    comment_obj.xml_path = "xl/threadedComments/" + rel.attrib["Target"].split("/")[-1]
                    parse_comments(z, wb_obj, comment_obj)
                    ws_obj.comments.append(comment_obj)
                elif "comment" in rel.attrib["Type"]:
#                   print_log(rel.attrib["Target"])
                    comment_obj = CommentXML(new_comment=False)
                    comment_obj.xml_path = "xl/" + rel.attrib["Target"].split("/")[-1]
                    parse_comments(z, wb_obj, comment_obj)
                    ws_obj.comments.append(comment_obj)
                else:
                    print_log(f'Other Rel : {rel.attrib["Target"]}')

    return


def parse_chart(z : zipfile.ZipFile, chart_path : str, ws_obj : WorkSheetXML):
    chart_xml = ET.fromstring(z.read(chart_path))

    chart_info = ChartInfo(xml_path = chart_path)
    # chart title
    title_elem = chart_xml.find('.//c:title', namespaces=NS_CHART)
    if title_elem is not None:
        title = get_chart_text(title_elem)
#       print_log(f'title = {title}')
        chart_info.title = title

    # all series
    for ser in chart_xml.xpath('//c:ser', namespaces=NS_CHART):
        # series name / 系列名
        tx = ser.find('.//c:tx', namespaces=NS_CHART)
        if tx is not None:
            text = get_chart_text(tx)
            if text:
#               print_log(f'series name = {text}')
                chart_info.series.append(text)
        
    # category axis title / カテゴリ軸ラベル
    for ax in chart_xml.xpath('//c:catAx', namespaces=NS_CHART):
        texts = ax.xpath('.//c:title//a:t', namespaces=NS_CHART)
        title = ''.join(t.text for t in texts if t.text)
        if title:
#           print_log(f'cat_axis_title = {title}')
            chart_info.axis['category'] = title

    # value axis title / 値の軸ラベル
    for ax in chart_xml.xpath('//c:valAx', namespaces=NS_CHART):
        texts = ax.xpath('.//c:title//a:t', namespaces=NS_CHART)
        title = ''.join(t.text for t in texts if t.text)
        if title:
#           print_log(f'val_axis_title = {title}')
            chart_info.axis['value'] = title
 
def parse_shared_string(z : zipfile.ZipFile, wb_obj : WorkBookXML):
    """
    共有文字列のxml解析
    """
    shared_strings = []
    print_log(f'------------------------------------------- parse_shared_string -------------------------------------------')
    if 'xl/sharedStrings.xml' in z.namelist():
        string_index = 0
        ss_xml = ET.fromstring(z.read('xl/sharedStrings.xml'))
        for si in ss_xml.xpath('//main:si', namespaces=NS):
            full_text = ''.join(t.text for t in si.xpath('.//main:t[not(ancestor::main:rPh)]', namespaces=NS)if t.text)
            ruby_text = ''.join(t.text for t in si.xpath('.//main:t[ancestor::main:rPh]', namespaces=NS)if t.text)
#           print_log(full_text)
            ss = SharedString()
            ss.text = full_text
            ss.ruby = ruby_text
            shared_strings.append(ss)
    
    wb_obj.shared_strings = shared_strings
    return

def parse_comments(z : zipfile.ZipFile, wb_obj : WorkBookXML, comment_obj : CommentXML):
    """
    コメント・メモのxml解析
    """
    comments = []
    print_log(f'------------------------------------------- parse_comments {comment_obj.xml_path}-------------------------------------------')
    comment_xml = ET.fromstring(z.read(comment_obj.xml_path))
    if comment_obj.new_comment:
        for tc in comment_xml.xpath('//tc:threadedComment', namespaces=NS_TC):
            # セル位置
            cell_ref = tc.get('ref', '')
#           comment_id = tc.get('id', '')
#           parent_id = tc.get('parentId', '')
            person_id = tc.get('personId', '')
            author = ''
            if person_id in wb_obj.person_info:
                author = wb_obj.person_info[person_id]
#           dt = tc.get('dT', '')
            text_elem = tc.find('{*}text')
            text = ''
            if text_elem is not None and text_elem.text:
                text = ''.join(text_elem.itertext())

#           print_log(text)
            comment_obj.comments.append(CommentInfo(author, cell_ref, text))
    else:
        authors = []
        for author in comment_xml.xpath('//main:authors/main:author', namespaces=NS):
            authors.append(author.text or '')

        for comment in comment_xml.xpath('//main:comment', namespaces=NS):
            text_elem = comment.find('{*}text')
            cell_ref = comment.get('ref')
            text = get_comment_text(text_elem)
            author_name = ""
            author_id = comment.get('authorId')
            if author_id is not None:
                idx = int(author_id)
                if 0 <= idx < len(authors):
                    author_name = authors[idx]

            comment_obj.comments.append(CommentInfo(author_name, cell_ref, text))
#           print_log(f'[{cell_ref}] : {text}')
    return

def parse_work_book(wb_path : str, z : zipfile.ZipFile):
    """
    ワークブックのxml解析
    """
    print_log(f'------------------------------------------- parse_work_book {wb_path}-------------------------------------------')

    # workbook.xmlを読む
    wb_xml = ET.fromstring(z.read("xl/workbook.xml"))
    # workbook.xml.relsを読む
    wb_rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))

    # rId → sheetX.xml のマップを作る
    rid_to_sheetxml = {}
    for rel in wb_rels:
        if "worksheet" in rel.attrib["Type"] or "chartsheet" in rel.attrib["Type"]:
            rid_to_sheetxml[rel.attrib["Id"]] = rel.attrib["Target"]
#   print(rid_to_sheetxml)

    wb_obj = WorkBookXML(wb_path = wb_path)
    wb_obj.person_info = load_persons(z)
    for sheet in wb_xml.xpath("//main:sheet", namespaces=NS):
        rid = sheet.attrib["{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"]
        if rid in rid_to_sheetxml:
            ws_obj = WorkSheetXML()
            ws_obj.name = sheet.attrib["name"]
            ws_obj.rid = rid
            ws_obj.xml_path = "xl/" + rid_to_sheetxml[ws_obj.rid]
            wb_obj.work_sheets[ws_obj.name] = ws_obj
        else:
            print_log(f'Unknown Sheet Type : rid={ws_obj.rid}')

    parse_work_sheet(z, wb_obj)
    parse_shared_string(z, wb_obj)
    return ws_obj

def parse_by_xml(wb_path):
    """
    xmlによるExcelファイル解析
    """
    with zipfile.ZipFile(wb_path) as z:
        wb_obj            = parse_work_book(wb_path, z)
        wb_obj.vba_macros = parse_vba(wb_path)
    return

@mcp.tool()
def grep_work_books(target_path : str, key_word : str, cells : bool = True, sheet_name : bool = True, comments : bool = True, shapes : bool = True, chart : bool = True) -> str:
    """
    """
    return ''

@mcp.tool()
def get_work_sheet_summary(sheet_name : str, wb_path : str = "") -> str:
    """
    Get a summary of work sheet.
    """
    return ''

@mcp.tool()
def get_work_sheet_list(wb_path : str = "") -> str:
    """
    Get the list of sheets in the workbook.
    """
    return ''

def main():
    parser = argparse.ArgumentParser(description="")
    parser.add_argument("-v", "--verbose", action='store_true')
    args = parser.parse_args()

    if args.verbose:
        create_log_file()

    parse_by_xml("sample\\test_macro.xlsm")
#   parse_by_xml("sample\\test_new_comment.xlsx")

#   mcp.run()


if __name__ == "__main__":
    main()


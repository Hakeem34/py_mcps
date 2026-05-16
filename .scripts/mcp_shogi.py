import os
import re
import sys
import json
import shutil
import signal
import dataclasses
import subprocess
from pathlib import Path
import ctypes
from typing import List, Dict, Optional

from mcp.server.fastmcp import FastMCP

ENGINE_PATH = r".\engine\YaneuraOu_NNUE_halfkp_256x2_32_32-V900Git_SSE42.exe"

# FastMCPのインスタンスを作成
mcp = FastMCP("shogi-mcp")


# --------------------------------------------------
# 対局状態
# --------------------------------------------------

current_sfen = "startpos"
move_history: List[str] = []


HANDICAP_SFENS = {
    "平手": "startpos",
    "香落ち": "sfen lnsgkgsn1/1r5b1/ppppppppp/9/9/9/PPPPPPPPP/1B5R1/LNSGKGSNL b - 1",
    "角落ち": "sfen lnsgkgsnl/1r7/ppppppppp/9/9/9/PPPPPPPPP/7R1/LNSGKGSNL b - 1",
    "飛車落ち": "sfen lnsgkgsnl/7b1/ppppppppp/9/9/9/PPPPPPPPP/1B7/LNSGKGSNL b - 1",
}


# --------------------------------------------------
# USI Engine Wrapper
# --------------------------------------------------

class YaneuraOuEngine:

    def __init__(self, engine_path: str):
        self.engine_path = engine_path
        self.proc = None
        self.start()

    def start(self):
        if not os.path.exists(self.engine_path):
            raise FileNotFoundError(f"Engine not found: {self.engine_path}")

        self.proc = subprocess.Popen(
            [self.engine_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )

        self.send_command("usi")
        self._wait("usiok")

        self.send_command("isready")
        self._wait("readyok")

    def send_command(self, cmd: str):
        if self.proc is None:
            raise RuntimeError("Engine not started")

        self.proc.stdin.write(cmd + "\n")
        self.proc.stdin.flush()

    def read_line(self) -> str:
        return self.proc.stdout.readline().strip()

    def _wait(self, token: str):
        while True:
            line = self.read_line()
            if token in line:
                return

    def position(self, sfen: str, moves: List[str]):
        if sfen == "startpos":
            cmd = "position startpos"
        else:
            cmd = f"position {sfen}"

        if moves:
            cmd += " moves " + " ".join(moves)

        self.send_command(cmd)

    def go(self, movetime_ms: int = 1000) -> Dict:
        self.send_command(f"go movetime {movetime_ms}")

        info_lines = []
        bestmove = None

        while True:
            line = self.read_line()

            if not line:
                continue

            if line.startswith("info"):
                info_lines.append(line)

            if line.startswith("bestmove"):
                bestmove = line.split()[1]
                break

        return {
            "bestmove": bestmove,
            "info": info_lines,
        }

    def stop(self):
        if self.proc:
            self.send_command("quit")
            self.proc.kill()
            self.proc = None


engine = None


# --------------------------------------------------
# Utility
# --------------------------------------------------

PIECE_MAP = {
    "p": "歩",
    "l": "香",
    "n": "桂",
    "s": "銀",
    "g": "金",
    "b": "角",
    "r": "飛",
    "k": "玉",
}

def get_app_path():
    if getattr(sys, 'frozen', False):
        # EXEとして実行されている場合
        return os.path.dirname(os.path.abspath(sys.executable))
    else:
        # 通常のPythonスクリプトとして実行されている場合
        return os.path.dirname(os.path.abspath(__file__))

BASE_DIR = get_app_path()
wshogi_cpp_path = os.path.join(BASE_DIR, "engine", "wshogi_cpp.dll")
#print(wshogi_cpp_path)
wshogi_cpp_dll = ctypes.cdll.LoadLibrary(wshogi_cpp_path)
wshogi_cpp_dll.legal_moves.restype = ctypes.c_char_p
wshogi_cpp_dll.legal_moves.argtypes = [ctypes.c_char_p]


def legal_moves(input_str="position startpos"):
	# 文字列sfenから合法手を生成する関数。usi形式のものをリストで返す。
    result = wshogi_cpp_dll.legal_moves(input_str.encode()).decode().split()
    return result

def wshogi_push(move_str, sfen_str="position startpos"):
    # 手を指す関数。sfenに1手追加する。
    if sfen_str=="":
        sfen_str="position startpos"
    rtn_str = sfen_str + " " + move_str
    return rtn_str

def wshogi_pop(sfen_str):
    # sfenから1手を戻す関数。1手分を消す。
    if "moves " in sfen_str:
        rtn_str = sfen_str.rsplit(' ',1)[0]
    else:
        rtn_str = sfen_str
    return rtn_str

def wshogi_turn(sfen_str="position startpos"):
    # sfenの文字列から手番を返す。"BLACK"（先手）か、"WHITE"（後手）
    cmd_lst = list(sfen_str.split(" "))  # スペース区切りのリスト。
    # 例1）position startpos
    if len(cmd_lst) == 2:
        rtn_str = "BLACK"
    # 例2）position startpos moves 7g7f 8d8e
    elif cmd_lst[1] == "startpos":
        if (len(cmd_lst)-2) % 2 == 1:
            rtn_str = "BLACK"
        else:
            rtn_str = "WHITE"

    # sfenで局面が送られてくるとき
    elif cmd_lst[1] == "sfen":  # 指定局面
        # 例1）position sfen lnsgkgsnl/1r5b1/p1ppppppp/1p7/9/7P1/PPPPPPP1P/1B5R1/LNSGKGSNL b - 1
        if len(cmd_lst) == 6:
            if cmd_lst[3] == "b":
                rtn_str = "BLACK"
            else:
                rtn_str = "WHITE"
        # 例2）position sfen lnsgkgsnl/1r5b1/p1ppppppp/1p7/9/7P1/PPPPPPP1P/1B5R1/LNSGKGSNL b - 1 moves 7g7f 8d8e
        else:
            if (len(cmd_lst)-6) % 2 == 1 and cmd_lst[3] == "b":
                rtn_str = "BLACK"
            elif (len(cmd_lst)-6) % 2 == 0 and cmd_lst[3] == "w":
                rtn_str = "BLACK"
            elif (len(cmd_lst)-6) % 2 == 1 and cmd_lst[3] == "w":
                rtn_str = "WHITE"
            elif (len(cmd_lst)-6) % 2 == 0 and cmd_lst[3] == "b":
                rtn_str = "WHITE"
            else:
                rtn_str = "BLACK"  # ここは通らない想定
    return rtn_str


def ensure_engine():
    global engine

    if engine is None:
        engine = YaneuraOuEngine(ENGINE_PATH)


def current_position_command() -> str:
    if current_sfen == "startpos":
        base = "position startpos"
    else:
        base = f"position {current_sfen}"

    if move_history:
        base += " moves " + " ".join(move_history)

    return base


# --------------------------------------------------
# SFEN → テキスト盤面
# --------------------------------------------------

def sfen_to_board_text(sfen: str) -> str:

    if sfen == "startpos":
        sfen = "sfen lnsgkgsnl/1r5b1/ppppppppp/9/9/9/PPPPPPPPP/1B5R1/LNSGKGSNL b - 1"

    tokens = sfen.split()

    board = tokens[1]
    side = tokens[2]
    hands = tokens[3]

    rows = board.split("/")

    lines = []

    lines.append(f"手番: {'先手' if side == 'b' else '後手'}")
    lines.append(f"持駒: {hands}")
    lines.append("")
    lines.append("  ９ ８ ７ ６ ５ ４ ３ ２ １")
    lines.append("+---------------------------+")

    kanji_rows = ["一", "二", "三", "四", "五", "六", "七", "八", "九"]

    for idx, row in enumerate(rows):

        out = []

        i = 0
        while i < len(row):
            ch = row[i]

            if ch.isdigit():
                out.extend([" ・"] * int(ch))
                i += 1
                continue

            promoted = False
            if ch == "+":
                promoted = True
                i += 1
                ch = row[i]

            piece = PIECE_MAP.get(ch.lower(), "?")

            if promoted:
                piece = "成" + piece

            if ch.islower():
                piece = "v" + piece
            else:
                piece = " " + piece

            out.append(piece)
            i += 1

        lines.append(f"|{' '.join(out)}|{kanji_rows[idx]}")

    lines.append("+---------------------------+")

    return "\n".join(lines)


# --------------------------------------------------
# Tool
# --------------------------------------------------

@mcp.tool()
def new_game(handicap: str = "平手") -> str:
    """
    将棋の対局を始める
    handicap(手合い)は平手、香落ち、角落ち、飛車落ちから指定
    """

    global current_sfen
    global move_history

    current_sfen = HANDICAP_SFENS.get(handicap, "startpos")
    move_history = []

    return f"新しい対局を開始しました: {handicap}"


@mcp.tool()
def get_position() -> str:
    """
    現在の局面をSFEN形式で返す
    """

    return current_position_command()


@mcp.tool()
def get_board_text() -> str:
    """
    現在の局面をテキストで返す
    """

    return sfen_to_board_text(current_sfen)


@mcp.tool()
def make_move(move: str) -> str:
    """
    指し手をUSI形式で示し、指し手を適用する
    """

    global current_sfen

    position_cmd = current_position_command()

    next_position = apply_move(position_cmd, move)

    if not next_position:
        return f"不正な指し手: {move}"

    move_history.append(move)

    current_sfen = next_position

    return json.dumps({
        "move": move,
        "position": current_sfen,
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def undo_move() -> str:
    """
    指し手を１手戻す
    """

    global current_sfen

    if not move_history:
        return "これ以上戻せません"

    removed = move_history.pop()

    if current_sfen == "startpos":
        return "開始局面です"

    if move_history:
        if current_sfen.startswith("sfen "):
            base = current_sfen
        else:
            base = "startpos"

        current_sfen = base
    else:
        current_sfen = "startpos"

    return f"戻しました: {removed}"


@mcp.tool()
def get_legal_moves() -> str:
    """
    現局面に対する合法手をUSI形式で返す
    """

    position_cmd = current_position_command()

    result = legal_moves(position_cmd)

    try:
        moves = json.loads(result)
    except Exception:
        moves = result.split()

    return json.dumps({
        "position": position_cmd,
        "moves": moves,
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def get_scored_moves(limit: int = 3, movetime_ms: int = 1000) -> str:
    """
    現局面に対する評価付き候補手を返す
    """

    ensure_engine()

    engine.position(current_sfen, move_history)

    result = engine.go(movetime_ms)

    parsed = []

    for line in result["info"]:

        m = re.search(r"score cp (-?\d+)", line)
        pv = re.search(r" pv (.*)$", line)

        if m:
            parsed.append({
                "eval": int(m.group(1)),
                "pv": pv.group(1) if pv else "",
            })

    return json.dumps({
        "bestmove": result["bestmove"],
        "infos": parsed[-limit:],
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def get_game_history() -> str:
    """
    現対局の棋譜を返す
    """

    return json.dumps(move_history, ensure_ascii=False, indent=2)


@mcp.tool()
def classify_position() -> str:
    """
    現対局の戦型を簡易判定する
    """

    joined = " ".join(move_history)

    if "2g2f" in joined and "8c8d" in joined:
        return "居飛車系"

    if "7g7f" in joined and "3c3d" in joined:
        return "一般的な序盤"

    return "不明"


@mcp.tool()
def search_position(position: str) -> str:
    """
    SFEN形式で指定した局面の棋譜を検索する
    現状はダミー実装
    """

    return f"局面検索(未実装): {position}"


@mcp.tool()
def evaluate(position: str, movetime_ms: int = 1000) -> str:
    """
    SFEN形式で指定した局面を評価します
    """

    ensure_engine()

    engine.position(position, [])

    result = engine.go(movetime_ms)

    last_info = None

    for line in reversed(result["info"]):
        if "score cp" in line:
            last_info = line
            break

    if last_info is None:
        return json.dumps(result, ensure_ascii=False, indent=2)

    score = re.search(r"score cp (-?\d+)", last_info)
    pv = re.search(r" pv (.*)$", last_info)

    data = {
        "bestmove": result["bestmove"],
        "eval": int(score.group(1)) if score else None,
        "pv": pv.group(1) if pv else "",
    }

    return json.dumps(data, ensure_ascii=False, indent=2)


# --------------------------------------------------
# Main
# --------------------------------------------------


def main():
    mcp.run()


if __name__ == "__main__":
    main()

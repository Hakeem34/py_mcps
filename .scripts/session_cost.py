"""Watch Visual Studio Code Copilot Chat transcripts in real time."""

from __future__ import annotations

import argparse
import json
import os
import dataclasses
import sqlite3
import datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from sympy import re

g_log_file = None

@dataclasses.dataclass
class WorkspaceInfo:
	workspace_storage_dir: Path = Path()
	workspace_dir: Path = Path()
	workspace_id: str = ""
	db_path: Path = Path()
	sessions: list = dataclasses.field(default_factory=list)

@dataclasses.dataclass
class SessionInfo:
	session_file_name: str = ""
	session_id: str = ""
	startTime: datetime.datetime = None
	transcript_msgs: list = dataclasses.field(default_factory=list)
	chat_session_msgs: list = dataclasses.field(default_factory=list)


PAGE_SIZE = 10


def parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(
		description="Watch Visual Studio Code Copilot Chat transcripts in real time."
	)
	parser.add_argument(
		"--path",
		type=Path,
		default=None,
		help="Path to the transcript file. If not provided, the default storage root will be used.",
	)
	return parser.parse_args()

def decode_workspace_folder(folder: str) -> Path:
	parsed = urlparse(folder)
	if parsed.scheme == "file":
		return Path(unquote(parsed.path).lstrip("/") if parsed.netloc == "" else unquote(f"//{parsed.netloc}{parsed.path}"))
	return Path(unquote(folder))

def default_storage_root() -> Path:
	app_data = os.environ.get("APPDATA")
	if not app_data:
		raise RuntimeError("APPDATA is not set; pass a transcript file with --path.")
	return Path(app_data) / "Code" / "User" / "workspaceStorage"

def read_from_ws_db(db_path: Path) -> list[dict[str, Any]]:
	conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
	cursor = conn.cursor()

	cursor.execute("""
		SELECT key, value
		FROM ItemTable
	""")

	g_log_file.write(f"--------------------------------------------------------- Reading from database: {db_path} ---------------------------------------------------------\n")
	for key, value in cursor.fetchall():
		g_log_file.write(f"KEY: {key} -> VALUE: {value}\n")
	conn.close()

def read_transcripts_jsonl(jsonl_file: Path):
	transcript_msgs = []
	g_log_file.write(f"--------------------------------------------------------- Reading from transcript file: {jsonl_file} ---------------------------------------------------------\n")
	with jsonl_file.open("r", encoding="utf-8") as f:
		for line in f:
			try:
				msg = json.loads(line)
				g_log_file.write(f"Read message: {msg}\n")
				msg_id = msg.get("id", "None")
				msg_type = msg.get("type", "None")
				msg_timestamp = msg.get("timestamp", "None")
				msg_parent = msg.get("parentId", "None")
				g_log_file.write(f"  {msg_id}:Type={msg_type}, Timestamp={msg_timestamp}, Parent={msg_parent}\n")

				msg_data = msg.get("data", {})
				msg_turn_id = msg_data.get("turnId", None)
				msg_content = msg_data.get("content", None)
				msg_toolname = msg_data.get("toolName", None)
				if msg_toolname:
					msg_args = msg_data.get("arguments", None)
					g_log_file.write(f"TURN: {msg_turn_id}, arguments: {msg_args}, context: {msg_content}\n")

				transcript_msgs.append(msg)
			except json.JSONDecodeError:
				continue
	return transcript_msgs

def list_chat_sessions(workspace_root: Path) -> list[Path]:
	sessions = []
	transcripts = workspace_root / "GitHub.copilot-chat" / "transcripts"
	#print(f"Looking for transcripts in: {transcripts}")
	if transcripts.exists() and transcripts.is_dir():
		#print(f"Found transcripts directory: {transcripts}")
		for session_file in transcripts.glob("*.jsonl"):
			session_info = SessionInfo()
			session_info.session_file_name = session_file.name
			session_info.session_id = session_file.stem
			sessions.append(session_info)
			#print(f"Found session file: {session_file}")
	return sessions

def list_workspace_info(storage_root: Path) -> list[WorkspaceInfo]:
	workspace_info = []
	for workspace_dir in storage_root.iterdir():
		if not workspace_dir.is_dir():
			continue
		info_file = workspace_dir / "workspace.json"
		if not info_file.exists():
			continue
		with info_file.open("r", encoding="utf-8") as f:
			try:
				info = WorkspaceInfo()
				ws_json = json.load(f)
				info.workspace_storage_dir = workspace_dir
				info.workspace_dir = decode_workspace_folder(ws_json.get("folder", ""))
				info.workspace_id = os.path.basename(workspace_dir)
				db_path = workspace_dir / "state.vscdb"
				if db_path.exists():
					info.db_path = db_path

				info.sessions = list_chat_sessions(workspace_dir)
				workspace_info.append(info)
			except json.JSONDecodeError:
				continue
	return workspace_info


def main() -> int:
	global g_log_file

	now = datetime.datetime.now()
	time_stamp = now.strftime("%Y%m%d_%H%M%S")
	log_file_path = f"session_cost_{time_stamp}.log"
	g_log_file =open(log_file_path, "w", encoding="utf-8")
	args = parse_args()
	storage_root = default_storage_root()
	workspaces = list_workspace_info(storage_root)
	for workspace in workspaces:
		if args.path and workspace.workspace_dir != args.path:
			continue
		#print(f"Workspace: {workspace['workspace_name']} (ID: {workspace['workspace_id']})")
		read_from_ws_db(workspace.db_path)

		print(f"{workspace.workspace_id} : {workspace.workspace_dir}")
		for session in workspace.sessions:
			print(f"  Session: {session.session_id} (FileName: {session.session_file_name})")
			g_log_file.write(f"  Session: {session.session_file_name} (ID: {session.session_id})\n")
			read_transcripts_jsonl(workspace.workspace_storage_dir / "GitHub.copilot-chat" / "transcripts" / session.session_file_name)
		print("-" * 40)

	g_log_file.close()

if __name__ == "__main__":
	main()

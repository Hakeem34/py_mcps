"""Watch Visual Studio Code Copilot Chat transcripts in real time."""

from __future__ import annotations

import argparse
import base64
import json
import os
import dataclasses
import sqlite3
import datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

g_log_file = None

class WorkspaceInfo:
	def __init__(self) -> None:
		self.workspace_storage_dir = Path()
		self.workspace_dir = Path()
		self.workspace_id = ""
		self.db_path = Path()
		self.sessions: list[SessionInfo] = []

	def find_session_by_id(self, session_id: str) -> SessionInfo | None:
		for session in self.sessions:
			if session.session_id == session_id:
				return session
		return None

	def append_session(self, session: SessionInfo) -> bool:
		if self.find_session_by_id(session.session_id) is not None:
			return False
		self.sessions.append(session)
		return True

class TranscriptMessage:
	def __init__(self) -> None:
		self.id = ""
		self.type = ""
		self.timestamp: datetime.datetime = None
		self.parent = ""
		self.data: dict[str, Any] = {}

@dataclasses.dataclass
class SessionInfo:
	session_file_name: str = ""
	session_id: str = ""
	title: str = ""
	archived: bool = False
	startTime: datetime.datetime = None
	creationDate: datetime.datetime = None
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

def decode_session_resource(resource: str) -> str | None:
	encoded_session_id = resource.rsplit("/", 1)[-1]
	padded_session_id = encoded_session_id + "=" * (-len(encoded_session_id) % 4)
	try:
		return base64.urlsafe_b64decode(padded_session_id).decode("utf-8")
	except (UnicodeDecodeError, ValueError):
		return None

def read_from_ws_db(workspace: WorkspaceInfo) -> list[dict[str, Any]]:
	conn = sqlite3.connect(f"file:{workspace.db_path}?mode=ro", uri=True)
	cursor = conn.cursor()

	cursor.execute("""
		SELECT key, value
		FROM ItemTable
	""")

	g_log_file.write(f"--------------------------------------------------------- Reading from database: {workspace.db_path} ---------------------------------------------------------\n")
	for key, value in cursor.fetchall():
		g_log_file.write(f"--- KEY: {key} ---------------------------------------------------\n")
#		g_log_file.write(f"  VALUE: {value}\n")
		if key == "chat.ChatSessionStore.index":
			json_value = json.loads(value)
			entries = json_value.get("entries", [])
			empty_sessions = []
			for entry_id, entry_val in entries.items():
				is_empty = entry_val.get("isEmpty", None)
				if is_empty:
#					g_log_file.write(f"    Entry ID: {entry_id} is empty.\n")
					empty_sessions.append(entry_id)
					continue
				else:
					g_log_file.write(f"  [{entry_id}] : {entry_val.get('title', '')}\n")

				sessionId = entry_val.get("sessionId", None)
				if (sessionInfo := workspace.find_session_by_id(sessionId)) is None:
					sessionInfo = SessionInfo(session_id=sessionId)
					workspace.append_session(sessionInfo)

				if sessionInfo.title == "":
					sessionInfo.title = entry_val.get("title", "")
				elif sessionInfo.title != entry_val.get("title", ""):
					g_log_file.write(f"  [Warning] Session title mismatch for session ID {sessionId}: existing title '{sessionInfo.title}', new title '{entry_val.get('title', '')}'\n")

			g_log_file.write(f"Empty sessions: {empty_sessions}\n")
		elif key == "GitHub.copilot-chat":
			json_value = json.loads(value)
			for key in json_value:
#				g_log_file.write(f"    Key: {key}, Value: {json_value[key]}\n")
				if key == "github.copilot.cli.workspaceSessionFile":
					cli_session_file = json_value.get("github.copilot.cli.workspaceSessionFile", "")
					g_log_file.write(f"  CLI session file: {cli_session_file}\n")
		elif key == "agentSessions.model.cache":
			json_value = json.loads(value)
			for session in json_value:
				if "resource" in session and "label" in session:
					resource = session["resource"]
					session_id = decode_session_resource(resource)
					sessionInfo = workspace.find_session_by_id(session_id)
					if sessionInfo is None:
						sessionInfo = SessionInfo(session_id=session_id)
						workspace.append_session(sessionInfo)

					if sessionInfo.title == "":
						sessionInfo.title = session["label"]
					elif sessionInfo.title != session["label"]:
						g_log_file.write(f"  [Warning] Session title mismatch for session ID {session_id}: existing title '{sessionInfo.title}', new title '{session['label']}'\n")

					g_log_file.write(
						f"  [Resource] Value: {resource}, Session ID: {session_id}, "
						f"Label: {session['label']}\n"
					)
				else:
					g_log_file.write(f"  [Other] Key: {session}\n")
		elif key == "agentSessions.state.cache":
			json_value = json.loads(value)
			for key in json_value:
#				g_log_file.write(f"    Key: {key}\n")
				if "resource" in key:
					resource = key["resource"]
					session_id = decode_session_resource(resource)
					sessionInfo = workspace.find_session_by_id(session_id)
					if sessionInfo is None:
						sessionInfo = SessionInfo(session_id=session_id)
						workspace.append_session(sessionInfo)
					if "archived" in key and key["archived"]:
						sessionInfo.archived = True
						g_log_file.write(f"  [Resource][Archived] : {key['resource']}\n")
					else:
						sessionInfo.archived = False
						g_log_file.write(f"  [Resource] : {key['resource']}\n")
		elif key == "memento/interactive-session-view-copilot":
			# このkeyには最後に扱ったセッションの情報しかないので、無視
#			json_value = json.loads(value)
#			for key in json_value:
#				g_log_file.write(f"    Key: {key}, Value: {json_value[key]}\n")
			pass


	conn.close()

def read_chat_session_kind0(session_info: SessionInfo, value: dict):
	for item in value:
		if item == "creationDate":
			creation_date = value.get("creationDate", 0)
			if isinstance(creation_date, (int, float)):
				session_info.creationDate = datetime.datetime.fromtimestamp(creation_date / 1000, tz=datetime.timezone.utc)
				g_log_file.write(f"    Creation Date (UTC): {session_info.creationDate.isoformat()}\n")
		elif item == "requests":
			requests = value.get("requests", [])
			for request in requests:
				g_log_file.write(f"    Request: {request}\n")
				for req_key, req_val in request.items():
					g_log_file.write(f"      Request Key: {req_key}, Value: {req_val}\n")
		elif item == "inputState":
			input_states = value.get("inputState", None)
			for is_key, is_val in input_states.items():
				if is_key == "selectedModel":
					selected_model_infos = is_val
					for smi_key, smi_val in selected_model_infos.items():
						if smi_key == "metadata":
							for meta_key, meta_val in smi_val.items():
								g_log_file.write(f"        Metadata Key: {meta_key}, Value: {meta_val}\n")
						else:
							g_log_file.write(f"      Selected Model Info: {smi_key} : {smi_val}\n")
				else:
					g_log_file.write(f"    Input State: {is_key} : {is_val} {type(is_val)}\n")

		else:
			g_log_file.write(f"    Other item in kind[0]: {item} : {value.get(item, None)}\n")

def read_chat_session_kind1(session_info: SessionInfo, key:list, value: dict):
	if key == "customTitle":
		if value != session_info.title:
			g_log_file.write(f"    Custom title changed from '{session_info.title}' to '{value}'\n")
	elif key == ["requests"]:
		g_log_file.write(f"    Storange Set Requests log!: {value}\n")
		exit(-1)
	elif len(key) == 3:
		if key[0] == "requests" and isinstance(key[1], int):
#			g_log_file.write(f"  kind[1]: Key has three elements: {key} : ...\n")
			request_index = key[1]
			sub_key = key[2]
			if sub_key == "result":
				g_log_file.write(f"  kind[1]: Set Request Index: {request_index}, Sub Key: {sub_key}, Value: ...\n")
#				g_log_file.write(f"  kind[1]: Set Request Index: {request_index}, Sub Key: {sub_key}, Value: {value}\n")
				for result_key, result_val in value.items():
					if result_key == "metadata":
						g_log_file.write(f"    Set Request Index: {request_index} Result Key: {result_key}, Value: ...\n")
						for meta_key, meta_val in result_val.items():
							if meta_key == "toolCallRounds":
								g_log_file.write(f"      Tool Call Rounds: ...\n")
								for tcr in meta_val:
									for tcr_key, tcr_val in tcr.items():
										g_log_file.write(f"        Tool Call Round Key: {tcr_key}, Value: {tcr_val}\n")
							else:
								g_log_file.write(f"      Metadata Key: {meta_key}, Value: {meta_val}\n")
					else:
						g_log_file.write(f"    Set Request Index: {request_index}, Sub Key: {sub_key}, Result Key: {result_key}, Value: {result_val}\n")
			else:
				g_log_file.write(f"  kind[1]: Set Request Index: {request_index}, Sub Key: {sub_key}, Value: {value}\n")
		else:
			g_log_file.write(f"  kind[1]: Unexpected key format: {key}\n")
	elif len(key) == 1:
		g_log_file.write(f"  kind[1]: Key has one element: {key} : {value}\n")
	else:							
		g_log_file.write(f"  kind[1]: Key has more than one element: {key} : {value}\n")

def read_chat_session_kind2(session_info: SessionInfo, key:list, value: dict):
	if key == ["requests"]:
		for request in value:
			g_log_file.write(f"    Request: ...\n")
			for req_key, req_val in request.items():
				if req_key == "result":
#					g_log_file.write(f"      Update Request Key: result, Value: ...\n")
					g_log_file.write(f"      Update Request Key: {req_key}, Value: {req_val}\n")
				elif req_key == "modeInfo":
#					g_log_file.write(f"      Update Request Key: modeInfo, Value: ...\n")
#					g_log_file.write(f"      Update Request Key: {req_key}, Value: {req_val}\n")
					for mode_key, mode_val in req_val.items():
						if mode_key == "modeInstructions":
#							g_log_file.write(f"        Mode Info Key: {mode_key}, Value: ...\n")
							for instr_key, instr_val in mode_val.items():
								g_log_file.write(f"          Mode Instruction Key: {instr_key}, Value: {instr_val}\n")
						else:
							g_log_file.write(f"        Mode Info Key: {mode_key}, Value: {mode_val}\n")
				elif req_key == "agent":
#					g_log_file.write(f"      Update Request Key: agent, Value: ...\n")
					g_log_file.write(f"      Update Request Key: {req_key}, Value: {req_val}\n")
				elif req_key == "variableData":
#					g_log_file.write(f"      Update Request Key: variableData, Value: ...\n")
					g_log_file.write(f"      Update Request Key: {req_key}, Value: {req_val}\n")
				elif req_key == "response":
#					g_log_file.write(f"      Update Request Key: response, Value: ...\n")
#					g_log_file.write(f"      Update Request Key: {req_key}, Value: {req_val}\n")
					resp_index = 0
					for resp in req_val:
						g_log_file.write(f"        Response Index: {resp_index}\n")
						for resp_key, resp_val in resp.items():
							g_log_file.write(f"          Response Key: {resp_key}, Value: {resp_val}\n")
						resp_index += 1	
				elif req_key == "message":
#					g_log_file.write(f"      Update Request Key: message, Value: ...\n")
#					g_log_file.write(f"      Update Request Key: {req_key}, Value: {req_val}\n")
					for msg_key, msg_val in req_val.items():
						g_log_file.write(f"        Message Key: {msg_key}, Value: {msg_val}\n")
				else:
					g_log_file.write(f"      Update Request Key: {req_key}, Value: {req_val}\n")
	elif len(key) == 3:
		if key[0] == "requests" and isinstance(key[1], int):
			request_index = key[1]
			sub_key = key[2]
			if sub_key == "copilotCredits":
				g_log_file.write(f"  kind[2]: Request Index: {request_index}, copilotCredits Value: {value}\n")
			elif sub_key == "response":
#				g_log_file.write(f"  kind[2]: Request Index: {request_index}, response Value: ...\n")
#				g_log_file.write(f"  kind[2]: Request Index: {request_index}, Sub Key: {sub_key}, Value: {value}\n")
				resp_index = 0
				for resp in value:
					g_log_file.write(f"  kind[2]: Request Index: {request_index}, Response Index: {resp_index}\n")
					for resp_key, resp_val in resp.items():
						if resp_key == "toolSpecificData":
							g_log_file.write(f"    Response Key: {resp_key}, Value: ...\n")
							for tsd_key, tsd_val in resp_val.items():
								g_log_file.write(f"      Tool Specific Data Key: {tsd_key}, Value: {tsd_val}\n")
						else:
							g_log_file.write(f"    Response Key: {resp_key}, Value: {resp_val}\n")
					resp_index += 1
			else:
				g_log_file.write(f"  kind[2]: Request Index: {request_index}, Sub Key: {sub_key}, Value: {value}\n")
		else:
			g_log_file.write(f"  kind[2]: Unexpected key format: {key}\n")
	else:							
		g_log_file.write(f"  kind[2]: Key has more than one element: {key}\n")



def read_chat_sessions_jsonl(session_info: SessionInfo, jsonl_file: Path):
	g_log_file.write(f"--------------------------------------------------------- Reading from chat session file: {jsonl_file} ---------------------------------------------------------\n")
	with jsonl_file.open("r", encoding="utf-8") as f:
		for line in f:
			try:
				msg = json.loads(line)
				kind = msg.get("kind", "None")
				if kind == 0:
					value = msg.get("v", None)
					read_chat_session_kind0(session_info, value)
				elif kind == 1:
					key = msg.get("k", None)
					value = msg.get("v", None)
					read_chat_session_kind1(session_info, key, value)
				elif kind == 2:
					key = msg.get("k", None)
					value = msg.get("v", None)
					read_chat_session_kind2(session_info, key, value)
				else:
					g_log_file.write(f"    Handling chat message of unknown kind: {kind}\n")
#				g_log_file.write(f"Read message: {msg}\n")
			except json.JSONDecodeError as e:
				g_log_file.write(f"Failed to decode JSON line: {line}\n")
	return

def read_transcripts_jsonl(session_info: SessionInfo, jsonl_file: Path):
	g_log_file.write(f"--------------------------------------------------------- Reading from transcript file: {jsonl_file} ---------------------------------------------------------\n")
	with jsonl_file.open("r", encoding="utf-8") as f:
		for line in f:
			try:
				msg = json.loads(line)
#				g_log_file.write(f"Read message: {msg}\n")
				TranscriptMessageObj = TranscriptMessage()
				TranscriptMessageObj.id = msg.get("id", "None")
				TranscriptMessageObj.type = msg.get("type", "None")
				TranscriptMessageObj.timestamp = msg.get("timestamp", "None")
				TranscriptMessageObj.parent = msg.get("parentId", "None")
				TranscriptMessageObj.data = msg.get("data", {})
				session_info.transcript_msgs.append(TranscriptMessageObj)

				msg_id = msg.get("id", "None")
				msg_type = msg.get("type", "None")
				msg_timestamp = msg.get("timestamp", "None")
				msg_parent = msg.get("parentId", "None")
				g_log_file.write(f"  [{msg_type:30s}]:{msg_id}: Timestamp={msg_timestamp}, Parent={msg_parent}\n")

				msg_data = msg.get("data", {})
				if msg_type == "session.start":
					session_id = msg_data.get("sessionId", None)
					g_log_file.write(f"    Session started with ID: {session_id}\n")
				elif msg_type == "assistant.message":
					message_id = msg_data.get("messageId", None)
					g_log_file.write(f"    Assistant message with ID: {message_id}\n")
				elif msg_type == "user.message":
					content = msg_data.get("content", None)
					g_log_file.write(f"    User message content: {content}\n")
				elif msg_type == "assistant.turn_start":
					turn_id = msg_data.get("turnId", None)
					g_log_file.write(f"    Assistant turn started with ID: {turn_id}\n")
				elif msg_type == "assistant.turn_end":
					turn_id = msg_data.get("turnId", None)
					g_log_file.write(f"    Assistant turn ended with ID: {turn_id}\n")
				elif msg_type == "tool.execution_start":
					call_id = msg_data.get("toolCallId", None)
					tool_name = msg_data.get("toolName", None)
					tool_arguments = msg_data.get("arguments", None)
					g_log_file.write(f"    Tool execution started for tool: {tool_name}, call ID: {call_id}, arguments: {tool_arguments}\n")
					if tool_name == "runSubagent":
						agent_name = tool_arguments.get("agentName", None)
						g_log_file.write(f"      Run subagent with name: {agent_name}\n")
				elif msg_type == "tool.execution_complete":
					tool_name = msg_data.get("toolName", None)
					call_id = msg_data.get("toolCallId", None)
					success = msg_data.get("success", None)
					g_log_file.write(f"    Tool execution ended for tool: {tool_name}, call ID: {call_id}, success: {success}\n")
				else:
					g_log_file.write(f"    Unhandled message type: {msg_type}\n")

			except json.JSONDecodeError:
				continue
	return 

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
	match = False
	for workspace in workspaces:
		if args.path and workspace.workspace_dir != args.path:
			continue
		#print(f"Workspace: {workspace['workspace_name']} (ID: {workspace['workspace_id']})")
		cwd = Path(os.getcwd())
#		print(f"{workspace.workspace_id} : {workspace.workspace_dir}")
		if workspace.workspace_dir == cwd:
			print(f"Current working directory matches workspace directory: {cwd} : {workspace.workspace_id}")
			match = True
			read_from_ws_db(workspace)
			for session in workspace.sessions:
				transcripts_path = workspace.workspace_storage_dir / "GitHub.copilot-chat" / "transcripts" / session.session_file_name
				shat_session_path = workspace.workspace_storage_dir / "chatSessions" / session.session_file_name
				if os.path.exists(transcripts_path) and os.path.exists(shat_session_path):
					print(f"Session: {session.session_id} (FileName: {session.session_file_name})")
					g_log_file.write(f"Session: {session.session_file_name} (ID: {session.session_id})\n")
					read_transcripts_jsonl(session, workspace.workspace_storage_dir / "GitHub.copilot-chat" / "transcripts" / session.session_file_name)
					read_chat_sessions_jsonl(session, workspace.workspace_storage_dir / "chatSessions" / session.session_file_name)
				else:
					print(f"Missing session files for session: {session.session_id} (FileName: {session.session_file_name})")
					g_log_file.write(f"Missing session files for session: {session.session_file_name} (ID: {session.session_id})\n")
			
#		print("-" * 40)

	g_log_file.close()
	if not match:
		print(f"No matching workspace found for current working directory: {cwd}")

if __name__ == "__main__":
	main()

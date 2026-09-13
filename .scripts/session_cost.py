""" Visual Studio Code Copilot Chat Session Cost Analyzer """

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sqlite3
import datetime
import subprocess
import sys
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

class SelectedModelInfo:
	def __init__(self) -> None:
		self.identifier: str = ""						# selectedModel / identifier
		self.mode_id: str = ""							# mode / id
		self.mode_kind: str = ""						# mode / kind
		self.inputCost: str = ""						# selectedModel / metadata / inputCost
		self.outputCost: str = ""						# selectedModel / metadata /outputCost
		self.cacheCost: str = ""						# selectedModel / metadata / cacheCost
		self.cacheWriteCost: str = ""					# selectedModel / metadata / cacheWriteCost
		self.longContextInputCost: str = ""				# selectedModel / metadata / longContextInputCost
		self.longContextOutputCost: str = ""			# selectedModel / metadata / longContextOutputCost
		self.longContextCacheCost: str = ""				# selectedModel / metadata / longContextCacheCost
		self.longContextCacheWriteCost: str = ""		# selectedModel / metadata / longContextCacheWriteCost
		self.priceCategory: str = ""					# selectedModel / metadata / priceCategory
		self.maxInputTokens: str = ""					# selectedModel / metadata / maxInputTokens
		self.maxOutputTokens: str = ""					# selectedModel / metadata / maxOutputTokens
		self.cap_vision: bool = False					# selectedModel / metadata / capabilities / vision
		self.cap_toolCalling: bool = False				# selectedModel / metadata / capabilities / toolCalling
		self.cap_agentMode: bool = False				# selectedModel / metadata / capabilities / agentMode
		self.reasoningEffort: str = ""					# selectedModel / modelConfiguration / reasoningEffort
		self.contextSize: str = ""						# selectedModel / modelConfiguration / contextSize

	def update_by_inputState_msg(self, message: ChatSessionMessage) -> None:
		if type(message.key) == list:
			subkey = message.key[1]
			if subkey == "mode":
				self.mode_id = decode_workspace_folder(get_key_value_from_descendants(message, ["id"], ""))
				self.mode_kind = get_key_value_from_descendants(message, ["kind"], "")
			elif subkey == "selectedModel":
				self.identifier = get_key_value_from_descendants(message, ["identifier"], "")
				self.inputCost = get_key_value_from_descendants(message, ["inputCost"], "")
				self.outputCost = get_key_value_from_descendants(message, ["outputCost"], "")
				self.cacheCost = get_key_value_from_descendants(message, ["cacheCost"], "")
				self.cacheWriteCost = get_key_value_from_descendants(message, ["cacheWriteCost"], "")
				self.longContextInputCost = get_key_value_from_descendants(message, ["longContextInputCost"], "")
				self.longContextOutputCost = get_key_value_from_descendants(message, ["longContextOutputCost"], "")
				self.longContextCacheCost = get_key_value_from_descendants(message, ["longContextCacheCost"], "")
				self.longContextCacheWriteCost = get_key_value_from_descendants(message, ["longContextCacheWriteCost"], "")
				self.priceCategory = get_key_value_from_descendants(message, ["priceCategory"], "")
				self.maxInputTokens = get_key_value_from_descendants(message, ["maxInputTokens"], "")
				self.maxOutputTokens = get_key_value_from_descendants(message, ["maxOutputTokens"], "")
				self.cap_vision = get_key_value_from_descendants(message, ["capabilities", "vision"], False)
				self.cap_toolCalling = get_key_value_from_descendants(message, ["capabilities", "toolCalling"], False)
				self.cap_agentMode = get_key_value_from_descendants(message, ["capabilities", "agentMode"], False)
				self.reasoningEffort = get_key_value_from_descendants(message, ["modelConfiguration", "reasoningEffort"], "")
				self.contextSize = get_key_value_from_descendants(message, ["modelConfiguration", "contextSize"], "")
		else:
			self.mode_id = decode_workspace_folder(get_key_value_from_descendants(message, ["mode", "id"], self.mode_id))
			self.mode_kind = get_key_value_from_descendants(message, ["mode", "kind"], self.mode_kind)
			self.identifier = get_key_value_from_descendants(message, ["selectedModel", "identifier"], self.identifier)
			self.inputCost = get_key_value_from_descendants(message, ["selectedModel", "inputCost"], self.inputCost)
			self.outputCost = get_key_value_from_descendants(message, ["selectedModel", "outputCost"], self.outputCost)
			self.cacheCost = get_key_value_from_descendants(message, ["selectedModel", "cacheCost"], self.cacheCost)
			self.cacheWriteCost = get_key_value_from_descendants(message, ["selectedModel", "cacheWriteCost"], self.cacheWriteCost)
			self.longContextInputCost = get_key_value_from_descendants(message, ["selectedModel", "longContextInputCost"], self.longContextInputCost)
			self.longContextOutputCost = get_key_value_from_descendants(message, ["selectedModel", "longContextOutputCost"], self.longContextOutputCost)
			self.longContextCacheCost = get_key_value_from_descendants(message, ["selectedModel", "longContextCacheCost"], self.longContextCacheCost)
			self.longContextCacheWriteCost = get_key_value_from_descendants(message, ["selectedModel", "longContextCacheWriteCost"], self.longContextCacheWriteCost)
			self.priceCategory = get_key_value_from_descendants(message, ["selectedModel", "priceCategory"], self.priceCategory)
			self.maxInputTokens = get_key_value_from_descendants(message, ["selectedModel", "maxInputTokens"], self.maxInputTokens)
			self.maxOutputTokens = get_key_value_from_descendants(message, ["selectedModel", "maxOutputTokens"], self.maxOutputTokens)
			self.cap_vision = get_key_value_from_descendants(message, ["selectedModel", "capabilities", "vision"], self.cap_vision)
			self.cap_toolCalling = get_key_value_from_descendants(message, ["selectedModel", "capabilities", "toolCalling"], self.cap_toolCalling)
			self.cap_agentMode = get_key_value_from_descendants(message, ["selectedModel", "capabilities", "agentMode"], self.cap_agentMode)
			self.reasoningEffort = get_key_value_from_descendants(message, ["selectedModel", "modelConfiguration","reasoningEffort"], self.reasoningEffort)
			self.contextSize = get_key_value_from_descendants(message, ["selectedModel", "modelConfiguration", "contextSize"], self.contextSize)
		return

	def __str__(self) -> str:
		return (
			f"SelectedModelInfo(identifier={self.identifier}, mode_id={self.mode_id}, mode_kind={self.mode_kind}, "
			f"inputCost={self.inputCost}, outputCost={self.outputCost}, cacheCost={self.cacheCost}, "
			f"cacheWriteCost={self.cacheWriteCost}, longContextInputCost={self.longContextInputCost}, "
			f"longContextOutputCost={self.longContextOutputCost}, longContextCacheCost={self.longContextCacheCost}, "
			f"longContextCacheWriteCost={self.longContextCacheWriteCost}, priceCategory={self.priceCategory}, "
			f"maxInputTokens={self.maxInputTokens}, maxOutputTokens={self.maxOutputTokens}, "
			f"cap_vision={self.cap_vision}, cap_toolCalling={self.cap_toolCalling}, cap_agentMode={self.cap_agentMode}, "
			f"reasoningEffort={self.reasoningEffort}, contextSize={self.contextSize})"
		)


class PromptTokenDetails:
	def __init__(self) -> None:
		self.category: str = ""
		self.label: str = ""
		self.percentage: str = ""

class ChatSessionMessage:
	def __init__(self) -> None:
		self.kind = ""
		self.key = ""
		self.value = None
		self.children: list[ChatSessionMessage] = []
		self.parent: ChatSessionMessage = None

class ChatSessionResponseMessage:
	def __init__(self) -> None:
		self.phase = ""
		self.timestamp = ""
		self.response = ""

class ChatSessionRequestAndResponse:
	def __init__(self) -> None:
		self.requestId: str = ""
		self.start_time: str = ""
		self.model_info: SelectedModelInfo = SelectedModelInfo()
		self.responseId: str = ""
		self.response_timestamp: str = ""
		self.response_msgs: list[ChatSessionResponseMessage] = []
		self.completionTokens: int = 0
		self.promptTokens: int = 0
		self.outputTokens: int = 0
		self.promptTokenDetails: list[PromptTokenDetails] = []
		self.agent_content_file: str = ""
		self.allowedSubagents: list[str] = []
		self.message_text: str = ""
		self.result_details_model: str = ""
		self.result_details_credit: float = 0.0

class TranscriptMessage:
	def __init__(self) -> None:
		self.id = ""
		self.type = ""
		self.timestamp: datetime.datetime = None
		self.parent = ""
		self.data: dict[str, Any] = {}

class ToolExecutionRecord:
	def __init__(self) -> None:
		self.toolCallId: str = ""
		self.start_time: datetime.datetime = None
		self.end_time: datetime.datetime = None
		self.tool_name: str = ""
		self.status: str = ""
		self.tool_args: dict[str, Any] = {}
		self.success: bool = False
		self.index: int = 0

class SessionInfo:
	def __init__(self) -> None:
		self.session_file_name: str = ""
		self.session_id: str = ""
		self.title: str = ""
		self.archived: bool = False
		self.startTime: str = ""
		self.creationDate: str = ""
		self.transcript_msgs: list[TranscriptMessage] = []
		self.chat_session_msgs: list[ChatSessionMessage] = []
		self.tool_execution_records: dict[str, ToolExecutionRecord] = {}
		self.tool_execution_index: int = 0
		self.current_model_info: SelectedModelInfo = SelectedModelInfo()
		self.request_and_response: list[ChatSessionRequestAndResponse] = []

	def handle_transcript_msg(self, TranscriptMessageObj: TranscriptMessage) -> None:
		self.transcript_msgs.append(TranscriptMessageObj)
		if TranscriptMessageObj.type == "tool.execution_start":
			tool_call_id = TranscriptMessageObj.data.get("toolCallId")
			if tool_call_id:
				if tool_call_id in self.tool_execution_records:
					g_log_file.write(f"Tool execution start twice for ID : {tool_call_id}\n")
				else:
					self.tool_execution_records[tool_call_id] = ToolExecutionRecord()
					self.tool_execution_records[tool_call_id].toolCallId = tool_call_id
					self.tool_execution_records[tool_call_id].start_time = TranscriptMessageObj.timestamp
					self.tool_execution_records[tool_call_id].tool_name = TranscriptMessageObj.data.get("toolName", "")
					self.tool_execution_records[tool_call_id].tool_args = TranscriptMessageObj.data.get("arguments", {})
					self.tool_execution_records[tool_call_id].index = self.tool_execution_index
					self.tool_execution_index += 1
			else:
				g_log_file.write("Tool execution start message without toolCallId\n") if g_log_file else None
			pass
		elif TranscriptMessageObj.type == "tool.execution_complete":
			tool_call_id = TranscriptMessageObj.data.get("toolCallId")
			if tool_call_id:
				if tool_call_id in self.tool_execution_records:
					self.tool_execution_records[tool_call_id].end_time = TranscriptMessageObj.timestamp
					self.tool_execution_records[tool_call_id].success = TranscriptMessageObj.data.get("success", False)
				else:
					g_log_file.write(f"Tool execution complete for unknown ID : {tool_call_id}\n")
			else:
				g_log_file.write("Tool execution complete message without toolCallId\n") if g_log_file else None
			pass
		elif TranscriptMessageObj.type == "session.start":
			pass
		elif TranscriptMessageObj.type == "assistant.message":
			pass
		elif TranscriptMessageObj.type == "assistant.turn_start":
			pass
		elif TranscriptMessageObj.type == "assistant.turn_end":
			pass
		elif TranscriptMessageObj.type == "user.message":
			pass
		else:
			g_log_file.write(f"Unhandled transcript message type: {TranscriptMessageObj.type}\n") if g_log_file else None
		return

	def handle_chat_session_key_value(self, key, value: Any) -> None:
		if type(key) is list:
			key = key[0] if key else None

		if key == "creationDate":
			self.creationDate = datetime.datetime.fromtimestamp(int(value) / 1000).strftime("%Y/%m/%d %H:%M:%S")
		elif key == "customTitle":
			if self.title != value:
				g_log_file.write(f"Title unmatch  {self.title} vs {value}\n") if g_log_file else None
#			g_log_file.write(f"customTitle	{value}\n") if g_log_file else None
		elif key == "agentName":
#			g_log_file.write(f"Agent Name: {value}\n") if g_log_file else None
			pass
		elif key == "toolCallId":
#			g_log_file.write(f"Tool Call ID: {value}\n") if g_log_file else None
			pass

	def get_model_and_credit(self, message: ChatSessionMessage) -> tuple[str, float]:
		result_details_model = ""
		result_details_credit = 0.0
		detail_text = get_key_value_from_descendants(message, ["details"], "")
		if not isinstance(detail_text, str):
			return result_details_model, result_details_credit

		result = re.fullmatch(
			r"(?P<model>[^\r\n\u2022]+?)"
			r"(?:\s*\u2022\s*(?P<credit>\d+(?:\.\d+)?)\s+credits?)?",
			detail_text.strip(),
			re.IGNORECASE,
		)
		if result:
			result_details_model = result.group("model").strip()
			result_details_credit = float(result.group("credit") or 0.0)

		return result_details_model, result_details_credit

	def parse_request_response(self, message: ChatSessionMessage, req_res: ChatSessionRequestAndResponse) -> ChatSessionRequestAndResponse:
		if message.key == "response":
			for i, res_msg in enumerate(message.children):
				kind = get_key_value_from_descendants(res_msg, ["kind"], "")
				timestamp = get_key_value_from_descendants(res_msg, ["timestamp"], "")
				phase = get_key_value_from_descendants(res_msg, ["phase"], "")
				if kind == "":
					value = get_key_value_from_descendants(res_msg, ['value'], '')
					if value == "\n```\n":
#							g_log_file.write(f"Response message {i}: ```\n") if g_log_file else None
						pass
					else:
						g_log_file.write(f"Response message {i}: {value}\n") if g_log_file else None
						res_msg = ChatSessionResponseMessage()
						res_msg.response = value
						res_msg.timestamp = timestamp
						res_msg.phase = phase
						req_res.response_msgs.append(res_msg)
				else:
					if kind == "mcpServersStarting":
						pass
					elif kind == "thinking":
						pass
					elif kind == "questionCarousel":
						pass
					elif kind == "toolInvocationSerialized":
						toolCallId = get_key_value_from_descendants(res_msg, ["toolCallId"], "")
						tool_id = get_key_value_from_descendants(res_msg, ["toolId"], "")
						pass
					elif kind == "inlineReference":
						name = get_key_value_from_descendants(res_msg, ["name"], "")
						req_res.response_msgs.append(f"参照：**{name}**")
						g_log_file.write(f"Inline reference: {name}\n") if g_log_file else None
						pass
					elif kind == "undoStop":
						pass
					elif kind == "codeblockUri":
						pass
					elif kind == "textEditGroup":
						edit_path = get_key_value_from_descendants(res_msg, ["path"], "")
						req_res.response_msgs.append(f"**ファイル更新：{edit_path}**")
						g_log_file.write(f"Text edit group: {edit_path}\n") if g_log_file else None
						pass
					else:
						g_log_file.write(f"Response message {i}: {get_key_value_from_descendants(res_msg, ['kind'], '')}\n") if g_log_file else None

		return req_res

	def parse_response_update(self, message: ChatSessionMessage, index: int) -> None:
		req_res = self.request_and_response[index] if index < len(self.request_and_response) else None
		if not req_res:
			g_log_file.write(f"Response update not found for index: {index}\n") if g_log_file else None
			exit(1)

		for i, child_msg in enumerate(message.children):
			kind = get_key_value_from_descendants(child_msg, ["kind"], "")
			timestamp = get_key_value_from_descendants(child_msg, ["timestamp"], "")
			phase = get_key_value_from_descendants(child_msg, ["phase"], "")
			if kind == "":
				value = get_key_value_from_descendants(child_msg, ['value'], '')
				if value == "\n```\n":
#					g_log_file.write(f"Response message {i}: ```\n") if g_log_file else None
					pass
				else:
					g_log_file.write(f"Response message {i}: {value}\n") if g_log_file else None
					res_msg = ChatSessionResponseMessage()
					res_msg.response = value
					res_msg.timestamp = timestamp
					res_msg.phase = phase
					req_res.response_msgs.append(res_msg)
#				g_log_file.write(f"Response update child message {i} has empty kind: {child_msg.key}\n") if g_log_file else None
			else:
				g_log_file.write(f"Response update child message {i}: kind={kind}, key={child_msg.key}\n") if g_log_file else None

		g_log_file.write(f"Parsed response update for index {index}\n") if g_log_file else None
		return

	def parse_new_request(self, message: ChatSessionMessage) -> None:
		"""
		kind="2"の新しいリクエストを解析するメソッド
		"""
		req_res = ChatSessionRequestAndResponse()
		req_res.requestId = get_key_value_from_descendants(message, ["requestId"], "")
		req_res.start_time = get_key_value_from_descendants(message, ["timestamp"], "")
		req_res.message_text = get_key_value_from_descendants(message, ["message", "text"], "")
		req_res.responseId = get_key_value_from_descendants(message, ["responseId"], "")
		req_res.response_timestamp = get_key_value_from_descendants(message, ["responseTimestamp"], "")
		req_res.promptTokens = get_key_value_from_descendants(message, ["promptTokens"], "")
		token_details = find_key_from_descendants(message, ["promptTokenDetails"])
		if token_details is not None:
			for token_detail in token_details.children:
				token_detai_info = PromptTokenDetails()
				token_detai_info.category = get_key_value_from_descendants(token_detail, ["responseId"], "")
				token_detai_info.label = get_key_value_from_descendants(token_detail, ["label"], "")
				token_detai_info.percentage = get_key_value_from_descendants(token_detail, ["percentageOfPrompt"], "")
#				g_log_file.write(f"Token detail_info: {token_detai_info.category}: {token_detai_info.label} : {token_detai_info.percentage}\n") if g_log_file else None
				req_res.promptTokenDetails.append(token_detai_info)

		for child_msg in message.children:
			if child_msg.key == "response":
				self.parse_request_response(child_msg, req_res)
			elif child_msg.key == "result":
				detail_text = get_key_value_from_descendants(message, ["details"], "")
				req_res.result_details_model, req_res.result_details_credit = self.get_model_and_credit(message)
				pass

		g_log_file.write(f"Parsed new request:[{req_res.requestId}]{req_res.message_text}\n") if g_log_file else None
		self.request_and_response.append(req_res)
		return

	def parse_request_result(self, message: ChatSessionMessage, index: int) -> None:
		req_res = self.request_and_response[index] if index < len(self.request_and_response) else None
		if not req_res:
			g_log_file.write(f"Request result not found for index: {index}\n") if g_log_file else None
			exit(1)

		detail_text = get_key_value_from_descendants(message, ["details"], "")
		prompt_tokens = get_key_value_from_descendants(message, ["metadata", "promptTokens"], "")
		output_tokens = get_key_value_from_descendants(message, ["metadata", "outputTokens"], "")
		req_res.prompt_tokens = int(prompt_tokens) if type(prompt_tokens) == int or (type(prompt_tokens) == str and prompt_tokens.isdigit()) else req_res.prompt_tokens
		req_res.output_tokens = int(output_tokens) if type(output_tokens) == int or (type(output_tokens) == str and output_tokens.isdigit()) else req_res.output_tokens
		toolCallRounds = find_key_from_descendants(message, ["metadata", "toolCallRounds"])
		g_log_file.write(f"Parsed request result for index {index}: prompt_tokens={prompt_tokens}, output_tokens={output_tokens}, detail_text={detail_text}\n") if g_log_file else None
		if not isinstance(detail_text, str):
			return

		result = re.fullmatch(
			r"(?P<model>[^\r\n\u2022]+?)"
			r"(?:\s*\u2022\s*(?P<credit>\d+(?:\.\d+)?)\s+credits?)?",
			detail_text.strip(),
			re.IGNORECASE,
		)
		if result:
			req_res.result_details_model = result.group("model").strip()
			req_res.result_details_credit = float(result.group("credit") or 0.0)
			g_log_file.write(
				f"Matched model and credit for index {index}: "
				f"{req_res.result_details_model} / {req_res.result_details_credit}\n"
			) if g_log_file else None

def get_first_valid_line(text):
	"""Return the first non-empty line from the given text."""
	for line in text.splitlines():
		if line.strip():
			return line
	return ""

def parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(
		description="Watch Visual Studio Code Copilot Chat transcripts in real time."
	)
	parser.add_argument(
		"path",
		nargs="?",
		type=Path,
		default=None,
		help="Path to the workspace to inspect. Defaults to the current working directory.",
	)
	parser.add_argument(
		"-b",
		"--backup",
		action="store_true",
		help="Back up the target WorkspaceStorage folder to the current directory.",
	)
	parser.add_argument(
		"-i",
		"--input",
		type=Path,
		default=None,
		metavar="WORKSPACE_STORAGE",
		help="Path to a WorkspaceStorage folder to analyze directly.",
	)
	parser.add_argument(
		"-s",
		"--session",
		metavar="SESSION_ID",
		help="Parse only the specified session ID.",
	)
	return parser.parse_args()

def decode_workspace_folder(folder: str) -> Path:
	parsed = urlparse(folder)
	if parsed.scheme == "file":
		return Path(unquote(parsed.path).lstrip("/") if parsed.netloc == "" else unquote(f"//{parsed.netloc}{parsed.path}"))
	return Path(unquote(folder))

def default_storage_root() -> Path:
	""" 
	まずWorkSpaceのストレージフォルダを返す
	"""
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
	"""
	ワークスペースのSQLiteデータベースからセッション情報を読み取る
	Returns:
		list[dict[str, Any]]: データベースから取得したセッション情報のリスト
	"""
	
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
					sessionInfo = SessionInfo()
					sessionInfo.session_id=sessionId
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


def disassemble_chat_session_value(session_info: SessionInfo, chat_session_message: ChatSessionMessage, value, indent=2):
	if type(value) is dict:
		for key, val in value.items():
			child_message = ChatSessionMessage()
			child_message.kind = chat_session_message.kind
			child_message.key = key
			child_message.parent = chat_session_message
			if type(val) in (dict, list):
				g_log_file.write(f"{' ' * indent}Key: {key}, Value is a nested structure\n")
				disassemble_chat_session_value(session_info, child_message, val, indent=indent + 2)
			else:
				disassemble_chat_session_value(session_info, child_message, val, indent=indent)
			chat_session_message.children.append(child_message)

	elif type(value) is list:
		for i, item in enumerate(value):
			child_message = ChatSessionMessage()
			child_message.kind = chat_session_message.kind
			child_message.key = str(i)
			child_message.parent = chat_session_message
			if type(item) in (dict, list):
				g_log_file.write(f"{' ' * indent}Index: {i}, Value is a nested structure\n")
				disassemble_chat_session_value(session_info, child_message, item, indent=indent + 2)
			else:
				disassemble_chat_session_value(session_info, child_message, item, indent=indent)
			chat_session_message.children.append(child_message)
	else:
		chat_session_message.value = value
		session_info.handle_chat_session_key_value(chat_session_message.key, chat_session_message.value)
		if chat_session_message.key == "content":
			g_log_file.write(f"{' ' * indent}Key: {chat_session_message.key}, Value: {get_first_valid_line(value)}, omit msg : content\n")
		elif chat_session_message.key == "text":
			if chat_session_message.parent is not None and chat_session_message.parent.parent is not None and chat_session_message.parent.parent.key == "renderedUserMessage":
				g_log_file.write(f"{' ' * indent}Key: {chat_session_message.key}, Value: {get_first_valid_line(value)}  omit msg : text\n")
		elif chat_session_message.key == "encrypted":
			g_log_file.write(f"{' ' * indent}Key: {chat_session_message.key}, Value:  omit msg : encrypted\n")
		elif chat_session_message.key == "timestamp" or chat_session_message.key == "responseTimestamp":
			time_text = datetime.datetime.fromtimestamp(int(value)/1000).strftime("%Y/%m/%d %H:%M:%S")
			g_log_file.write(f"{' ' * indent}Key: {chat_session_message.key}, Value: {value} ({time_text})\n")
		else:
			g_log_file.write(f"{' ' * indent}Key: {chat_session_message.key}, Value: {value}\n")

def read_chat_sessions_jsonl(session_info: SessionInfo, jsonl_file: Path):
	"""
	チャットセッションJSONLファイルを読み取る
	Args:
		session_info (SessionInfo): セッション情報を格納するオブジェクト
		jsonl_file (Path): 読み取るJSONLファイルのパス
	"""
	g_log_file.write(f"--------------------------------------------------------- Reading from chat session file: {jsonl_file} ---------------------------------------------------------\n")
	with jsonl_file.open("r", encoding="utf-8") as f:
		for line in f:
			try:
				msg = json.loads(line)
				kind = msg.get("kind", "None")
				chat_session_message = ChatSessionMessage()
				chat_session_message.parent = None
				chat_session_message.kind = kind
				if kind == 0:
					chat_session_message.key = ""
					value = msg.get("v", None)
					g_log_file.write(f"  kind[0]: Disassembling value...\n")
					disassemble_chat_session_value(session_info, chat_session_message, value, 4)
				elif kind == 1:
					key = msg.get("k", None)
					value = msg.get("v", None)
					g_log_file.write(f"  kind[1][key={key}]: Disassembling value...\n")
					chat_session_message.key = key
					disassemble_chat_session_value(session_info, chat_session_message, value, 4)
				elif kind == 2:
					key = msg.get("k", None)
					value = msg.get("v", None)
					chat_session_message.key = key
					g_log_file.write(f"  kind[2][key={key}]: Disassembling value...\n")
					disassemble_chat_session_value(session_info, chat_session_message, value, 4)
				else:
					g_log_file.write(f"    Handling chat message of unknown kind: {kind}\n")

				session_info.chat_session_msgs.append(chat_session_message)
			except json.JSONDecodeError as e:
				g_log_file.write(f"Failed to decode JSON line: {line}\n")
	return

def read_transcripts_jsonl(session_info: SessionInfo, jsonl_file: Path):
	"""
	チャットのトランスクリプトJSONLファイルを読み取る
	Args:
		session_info (SessionInfo): セッション情報を格納するオブジェクト
		jsonl_file (Path): 読み取るJSONLファイルのパス
	"""
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
				session_info.handle_transcript_msg(TranscriptMessageObj)


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
	"""
	指定されたワークスペースのルートディレクトリから、チャットセッションのJSONLファイルをリストアップする
	Args:
		workspace_root (Path): ワークスペースのルートディレクトリ
	Returns:
		list[Path]: セッション情報のリスト
	"""
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

def workspace_info_from_storage_dir(workspace_dir: Path) -> WorkspaceInfo | None:
	"""Read workspace metadata from one WorkspaceStorage folder."""
	info_file = workspace_dir / "workspace.json"
	if not info_file.exists():
		return None
	with info_file.open("r", encoding="utf-8") as f:
		try:
			ws_json = json.load(f)
			folder = ws_json.get("folder")
			if not isinstance(folder, str) or not folder:
				return None

			info = WorkspaceInfo()
			info.workspace_storage_dir = workspace_dir
			info.workspace_dir = decode_workspace_folder(folder)
			info.workspace_id = os.path.basename(workspace_dir)
			db_path = workspace_dir / "state.vscdb"
			if db_path.exists():
				info.db_path = db_path

			info.sessions = list_chat_sessions(workspace_dir)
			return info
		except json.JSONDecodeError:
			return None

def list_workspace_info(storage_root: Path) -> list[WorkspaceInfo]:
	"""
	ディレクトリごとにworkspace.jsonを読み込みパスを特定、
	state.vscdbの所在を確認し、WorkSpace情報をリストで返す
	"""

	workspace_info = []
	for workspace_dir in storage_root.iterdir():
		if not workspace_dir.is_dir():
			continue
		info = workspace_info_from_storage_dir(workspace_dir)
		if info is not None:
			workspace_info.append(info)
	return workspace_info

def backup_workspace_storage(workspace: WorkspaceInfo, output_dir: Path) -> Path:
	"""Create a tar archive of the target WorkspaceStorage folder."""
	time_stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
	archive_path = output_dir / f"bup_{workspace.workspace_id}_{time_stamp}.7z"
	storage_dir = workspace.workspace_storage_dir.resolve()

	try:
		subprocess.run(
			[
				"tar",
				"-a",
				"-c",
				"-f",
				str(archive_path),
				"--exclude",
				archive_path.name,
				"-C",
				str(storage_dir.parent),
				storage_dir.name,
			],
			check=True,
		)
	except FileNotFoundError as error:
		raise RuntimeError("tar command was not found in PATH") from error

	return archive_path

def find_key_from_descendants(chat_msg: ChatSessionMessage, key_stack: list[str]) -> ChatSessionMessage:
	"""
	指定されたキーを持つ子孫メッセージを再帰的に検索する
	Args:
		chat_msg (ChatSessionMessage): 検索対象のチャットメッセージ
		key_stack (list[str]): 検索するキーのスタック
	Returns:
		ChatSessionMessage: 指定されたキーに最初に一致した子孫メッセージ
	"""
	if not key_stack:
		return None
	if chat_msg.key == key_stack[0]:
		if len(key_stack) == 1:
			return chat_msg
		for child_msg in chat_msg.children:
			result = find_key_from_descendants(child_msg, key_stack[1:])
			if result is not None:
				return result
	for child_msg in chat_msg.children:
		result = find_key_from_descendants(child_msg, key_stack)
		if result is not None:
			return result
	return None

def get_key_value_from_descendants(chat_msg: ChatSessionMessage, key_stack: list[str], default=None):
	"""
	指定されたキーを持つ子孫メッセージから値を取得する
	Args:
		chat_msg (ChatSessionMessage): 検索対象のチャットメッセージ
		key_stack (list[str]): 検索するキーのスタック
		default: 見つからなかった場合のデフォルト値
	Returns:
		指定されたキーに最初に一致した子孫メッセージの値、見つからなかった場合はdefault
	"""
	result_msg = find_key_from_descendants(chat_msg, key_stack)
	if result_msg is not None:
		return result_msg.value
	return default


def parase_chat_session_msg(chat_msg : ChatSessionMessage, session_info : SessionInfo):
	"""
	パースされたチャットセッションメッセージを処理し、セッション情報を更新する
	Args:
		chat_msg (ChatSessionMessage): パース対象のチャットセッションメッセージ
		session_info (SessionInfo): 更新対象のセッション情報オブジェクト
	"""
	if type(chat_msg.key) == list and len(chat_msg.key) > 3:
		g_log_file.write(f"Too many key list! {chat_msg.key}\n")
		print(f"Too many key list! {chat_msg.key}\n")
		exit(1)

	if (type(chat_msg.key) == list and chat_msg.key[0] == "inputState") or chat_msg.key == "inputState":
		session_info.current_model_info.update_by_inputState_msg(chat_msg)
		g_log_file.write(f"CurrentModelInfo: {session_info.current_model_info}\n")
	elif (type(chat_msg.key) == list and len(chat_msg.key) == 3):
		if chat_msg.key[0] == "requests" and chat_msg.key[2] == "result":
			index = int(chat_msg.key[1])
			session_info.parse_request_result(chat_msg, index)
		elif chat_msg.key[0] == "requests" and chat_msg.key[2] == "response":
			index = int(chat_msg.key[1])
			session_info.parse_response_update(chat_msg, index)
	elif chat_msg.key == ['requests']:
		if len(chat_msg.children) != 1 or chat_msg.children[0].key != "0":
			g_log_file.write(f"Unexpected 'requests' key length: {chat_msg.key}\n")
			print(f"Unexpected 'requests' key length: {chat_msg.key}\n")
			exit(1)

		session_info.parse_new_request(chat_msg.children[0])

	for child_msg in chat_msg.children:
		parase_chat_session_msg(child_msg, session_info)




def output_workspace_summary(workspace: WorkspaceInfo) -> None:
	"""
	ワークスペースのサマリー情報を出力する
	Args:
		workspace (WorkspaceInfo): ワークスペース情報のオブジェクト
	"""
	time_stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
	target_workspace = workspace.workspace_dir
	summary_file = open(target_workspace / f"セッションログ_{time_stamp}.md", "w", encoding="utf-8")
	summary_file.write(f"# WorkSpace\n  Path: {workspace.workspace_dir}<br>  Id: {workspace.workspace_id}\n")
	summary_file.write(f"# Sessions\n| Id | 作成日時 | タイトル | アーカイブ状態 |\n")
	summary_file.write(f"| --- | --- | --- | --- |\n")
	for session in workspace.sessions:
		if session.archived:
			summary_file.write(f"| {session.session_id} | {session.creationDate} | {session.title} | アーカイブ済み |\n")
		else:
			summary_file.write(f"| {session.session_id} | {session.creationDate} | {session.title} | |\n")
	summary_file.write("\n")
	summary_file.write("\n")

	# 各セッションの詳細情報を出力
	for session in workspace.sessions:
		summary_file.write(f"## [{session.creationDate}] : {session.title}\nId: {session.session_id}\n")
		summary_file.write(f"### transcript_msgs:\n")
		for trans_msg in session.transcript_msgs:
#			summary_file.write(f"  - [{trans_msg.timestamp}] {trans_msg.type}\n")
			pass

		g_log_file.write(f"\n\n------------------ Parsing chat session messages len = {len(session.chat_session_msgs)} ------------------\n")
		summary_file.write(f"### chat_session_msgs:\n")
		for chat_msg in session.chat_session_msgs:
#			summary_file.write(f"  - {chat_msg.kind}: {chat_msg.key}\n")
			g_log_file.write(f"------------------ Parsing chat session message: kind[{chat_msg.kind}]:{chat_msg.key} ------------------\n")
			parase_chat_session_msg(chat_msg, session)
			g_log_file.write("\n")

		summary_file.write(f"| Request | Response Message | Model | Credit |\n")
		summary_file.write(f"| --- | --- | --- | --- |\n")
		for req_res in session.request_and_response:
			req_text = req_res.message_text.replace('\r\n', r'<br>')
#			res_text = "<br>".join([msg.replace('\r\n', r'<br>').replace('\r', r'<br>').replace('\n', r'<br>') for msg in req_res.response_msgs])
			res_text = 	req_res.response_msgs[-1].response.replace('\r\n', r'<br>').replace('\r', r'<br>').replace('\n', r'<br>')
			summary_file.write(f"| {req_text} | {res_text} | {req_res.result_details_model} | {req_res.result_details_credit} |\n")

	summary_file.close()
	return

def main() -> int:
	global g_log_file

	# ログファイルの準備
	now = datetime.datetime.now()
	time_stamp = now.strftime("%Y%m%d_%H%M%S")
	log_file_path = f"session_cost_{time_stamp}.log"
	g_log_file =open(log_file_path, "w", encoding="utf-8")

	# 引数の解析とワークスペース情報の取得
	args = parse_args()
	g_log_file.write(f"Starting session cost analysis at {time_stamp}, input: {args.input}, path: {args.path}, session: {args.session}, backup: {args.backup}\n")
	if args.input:
		input_storage = args.input.resolve()
		if not input_storage.is_dir():
			print(f"WorkspaceStorageフォルダが見つかりません: {input_storage}")
			g_log_file.close()
			return 1
		workspace = workspace_info_from_storage_dir(input_storage)
		if workspace is None:
			print(f"workspace.jsonが見つからないか、内容が不正です: {input_storage}")
			g_log_file.close()
			return 1
		workspaces = [workspace]
	else:
		storage_root = default_storage_root()
		workspaces = list_workspace_info(storage_root)

	# 現在の作業ディレクトリとターゲットワークスペースの決定
	cwd = Path.cwd().resolve()
	target_workspace = args.path.resolve() if args.path else (
		workspaces[0].workspace_dir.resolve() if args.input else cwd
	)
	match = False
	for workspace in workspaces:
		if workspace.workspace_dir.resolve() != target_workspace:
			continue

		print(f"Workspace found: {workspace.workspace_dir} : {workspace.workspace_id}")
		match = True
		if args.backup:
			try:
				archive_path = backup_workspace_storage(workspace, cwd)
				print(f"Backup created: {archive_path}")
			except (RuntimeError, subprocess.CalledProcessError) as error:
				print(f"バックアップに失敗しました: {error}")
				g_log_file.close()
				return 1

		# ワークスペースのデータベースからセッション情報を読み取る
		read_from_ws_db(workspace)

		if args.session:
			session = workspace.find_session_by_id(args.session)
			if session is None:
				print(f"Sessionが見つかりません: {args.session}")
				continue
			g_log_file.write(f"Filtering sessions for session ID: {args.session}\n")
			print(f"Filtering sessions for session ID: {args.session}")
			workspace.sessions = [session]

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
			
		output_workspace_summary(workspace)

	g_log_file.close()
	if not match:
		print(f"ワークスペースが見つかりません: {target_workspace}")
		return 1

	return 0

if __name__ == "__main__":
	sys.exit(main())

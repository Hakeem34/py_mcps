"""Watch Visual Studio Code Copilot Chat transcripts in real time."""

from __future__ import annotations

import argparse
import json
import msvcrt
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO
from urllib.parse import unquote, urlparse


PAGE_SIZE = 10


def default_storage_root() -> Path:
	app_data = os.environ.get("APPDATA")
	if not app_data:
		raise RuntimeError("APPDATA is not set; pass a transcript file with --path.")
	return Path(app_data) / "Code" / "User" / "workspaceStorage"


def discover_transcripts(storage_root: Path) -> list[Path]:
	paths = storage_root.glob("*/GitHub.copilot-chat/transcripts/*.jsonl")
	return sorted(paths, key=lambda path: path.stat().st_mtime, reverse=True)


def workspace_label(transcript_path: Path) -> str:
	storage_path = transcript_path.parents[2]
	metadata_path = storage_path / "workspace.json"
	try:
		metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
	except (OSError, json.JSONDecodeError):
		return storage_path.name
	if not isinstance(metadata, dict):
		return storage_path.name
	uri = metadata.get("folder") or metadata.get("workspace")
	if not isinstance(uri, str) or not uri.startswith("file:"):
		return storage_path.name
	parsed = urlparse(uri)
	path = unquote(parsed.path)
	if len(path) >= 3 and path[0] == "/" and path[2] == ":":
		path = path[1].upper() + path[2:]
	return path.replace("/", "\\") or storage_path.name


def select_transcript(paths: list[Path]) -> Path | None:
	page = 0
	page_count = (len(paths) + PAGE_SIZE - 1) // PAGE_SIZE
	while True:
		start = page * PAGE_SIZE
		page_paths = paths[start : start + PAGE_SIZE]
		print(f"\nCopilot Chat transcripts ({len(paths)} total) - page {page + 1}/{page_count}")
		for number, path in enumerate(page_paths, start=1):
			updated = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
			print(f"{number:>2}. {updated}  {path.stem}  workspace:{workspace_label(path)}")
		print("Enter 1-10 to watch, n for next, p for previous, q or Esc to quit.")

		try:
			command = input("> ").strip().lower()
		except (EOFError, KeyboardInterrupt):
			print()
			return None
		if command in {"q", "\x1b"}:
			return None
		if command == "n" and page < page_count - 1:
			page += 1
			continue
		if command == "p" and page > 0:
			page -= 1
			continue
		if command.isdigit():
			selection = int(command)
			if 1 <= selection <= len(page_paths):
				return page_paths[selection - 1]
		print("Invalid selection.")


def format_timestamp(timestamp: Any) -> str:
	if isinstance(timestamp, (int, float)):
		return datetime.fromtimestamp(timestamp / 1000).strftime("%Y-%m-%d %H:%M:%S")
	if not isinstance(timestamp, str):
		return "-"
	try:
		return datetime.fromisoformat(timestamp.replace("Z", "+00:00")).astimezone().strftime(
			"%Y-%m-%d %H:%M:%S"
		)
	except ValueError:
		return timestamp


def print_event(
	event: dict[str, Any], session_id: str, raw: bool, intermediate: bool
) -> bool:
	if raw:
		print(json.dumps(event, ensure_ascii=False))
		return True
	data = event.get("data")
	if not isinstance(data, dict):
		return False
	content = data.get("content")
	if not isinstance(content, str) or not content:
		return False
	if event.get("type") == "user.message":
		print(f"[{format_timestamp(event.get('timestamp'))}] [{session_id}] USER")
		print(content)
		print()
		return True
	if event.get("type") != "assistant.message":
		return False
	is_final = data.get("toolRequests") == []
	if not is_final and not intermediate:
		return False
	label = "COPILOT" if is_final else "COPILOT (intermediate)"
	print(f"[{format_timestamp(event.get('timestamp'))}] [{session_id}] {label}")
	print(content)
	print()
	return True


def process_lines(
	stream: TextIO,
	session_id: str,
	counts: Counter[str],
	raw: bool,
	intermediate: bool,
	session_path: Path | None = None,
	seen_responses: set[int] | None = None,
) -> int:
	printed_events = 0
	for line in stream:
		try:
			event = json.loads(line)
		except json.JSONDecodeError:
			continue
		if not isinstance(event, dict):
			continue
		event_type = event.get("type")
		if print_event(event, session_id, raw, intermediate):
			counts[str(event_type)] += 1
			printed_events += 1
			data = event.get("data")
			if (
				not raw
				and event_type == "assistant.message"
				and isinstance(data, dict)
				and data.get("toolRequests") == []
				and session_path is not None
				and seen_responses is not None
			):
				metadata = latest_unreported_metadata(session_path, seen_responses)
				if metadata:
					index, model, credits = metadata
					print_response_metadata(model, credits)
					seen_responses.add(index)
	return printed_events


def print_prompt(request: dict[str, Any], session_id: str) -> bool:
	message = request.get("message")
	if not isinstance(message, dict):
		return False
	text = message.get("text")
	if not isinstance(text, str) or not text:
		return False
	print(f"[{format_timestamp(request.get('timestamp'))}] [{session_id}] USER")
	print(text)
	print()
	return True


def print_response_metadata(model: Any, credits: Any) -> None:
	model_text = model if isinstance(model, str) else "-"
	credit_text = f"{credits:.5f}" if isinstance(credits, (int, float)) else "-"
	print(f"Model: {model_text}  Credit: {credit_text}")
	print()


def response_content(response: list[Any]) -> str:
	parts: list[str] = []
	for part in response:
		if not isinstance(part, dict):
			continue
		value = part.get("value")
		if isinstance(value, str):
			parts.append(value)
		elif part.get("kind") == "inlineReference":
			resolve_id = part.get("resolveId")
			reference = resolve_id if isinstance(resolve_id, str) else "unknown"
			parts.append(f"[inline reference unavailable: {reference}]")
	return "".join(parts)


def print_response(response: list[Any], model: Any, credits: Any) -> bool:
	content = response_content(response)
	if not content:
		return False
	print_response_metadata(model, credits)
	return True


def process_session_lines(
	stream: TextIO,
	session_id: str,
	counts: Counter[str],
	seen_requests: set[str],
	seen_responses: set[int],
	ready_responses: set[int],
	request_timestamps: dict[int, Any],
	request_models: dict[int, str],
	request_credits: dict[int, float],
) -> int:
	printed_events = 0
	for line in stream:
		try:
			record = json.loads(line)
		except json.JSONDecodeError:
			continue
		if not isinstance(record, dict):
			continue
		key = record.get("k")
		if key == ["requests"]:
			requests = record.get("v")
			if not isinstance(requests, list):
				continue
			for index, request in enumerate(requests):
				if not isinstance(request, dict):
					continue
				request_timestamps[index] = request.get("timestamp")
				request_id = request.get("requestId")
				if not isinstance(request_id, str) or request_id in seen_requests:
					continue
				seen_requests.add(request_id)
		elif (
			isinstance(key, list)
			and len(key) == 3
			and key[0] == "requests"
			and isinstance(key[1], int)
			and key[2] == "result"
		):
			ready_responses.add(key[1])
			result = record.get("v")
			metadata = result.get("metadata") if isinstance(result, dict) else None
			if isinstance(metadata, dict) and isinstance(metadata.get("resolvedModel"), str):
				request_models[key[1]] = metadata["resolvedModel"]
		elif (
			isinstance(key, list)
			and len(key) == 3
			and key[0] == "requests"
			and isinstance(key[1], int)
			and key[2] == "copilotCredits"
			and isinstance(record.get("v"), (int, float))
		):
			request_credits[key[1]] = record["v"]
		elif (
			isinstance(key, list)
			and len(key) == 3
			and key[0] == "requests"
			and isinstance(key[1], int)
			and key[2] == "response"
			and key[1] in ready_responses
			and key[1] not in seen_responses
		):
			response = record.get("v")
			if isinstance(response, list) and print_response(
				response, request_models.get(key[1]), request_credits.get(key[1])
			):
				seen_responses.add(key[1])
				ready_responses.remove(key[1])
				counts["assistant.response"] += 1
				printed_events += 1
	return printed_events


def load_request_timestamps(path: Path) -> dict[int, Any]:
	timestamps: dict[int, Any] = {}
	snapshot_timestamps: list[Any] = []
	with path.open("r", encoding="utf-8") as stream:
		for line in stream:
			try:
				record = json.loads(line)
			except json.JSONDecodeError:
				continue
			if not isinstance(record, dict):
				continue
			key = record.get("k")
			value = record.get("v")
			if key == ["requests"] and isinstance(value, list):
				if len(value) == 1 and isinstance(value[0], dict):
					snapshot_timestamps.append(value[0].get("timestamp"))
				else:
					for index, request in enumerate(value):
						if isinstance(request, dict):
							timestamps[index] = request.get("timestamp")
			elif (
				isinstance(key, list)
				and len(key) == 2
				and key[0] == "requests"
				and isinstance(key[1], int)
				and isinstance(value, dict)
			):
				timestamps[key[1]] = value.get("timestamp")
	for index, timestamp in enumerate(snapshot_timestamps):
		timestamps.setdefault(index, timestamp)
	return timestamps


def load_completed_request_indices(path: Path) -> set[int]:
	completed: set[int] = set()
	with path.open("r", encoding="utf-8") as stream:
		for line in stream:
			try:
				record = json.loads(line)
			except json.JSONDecodeError:
				continue
			key = record.get("k") if isinstance(record, dict) else None
			if (
				isinstance(key, list)
				and len(key) == 3
				and key[0] == "requests"
				and isinstance(key[1], int)
				and key[2] == "result"
			):
				completed.add(key[1])
	return completed


def load_request_metadata(path: Path) -> tuple[dict[int, str], dict[int, float]]:
	models: dict[int, str] = {}
	credits: dict[int, float] = {}
	with path.open("r", encoding="utf-8") as stream:
		for line in stream:
			try:
				record = json.loads(line)
			except json.JSONDecodeError:
				continue
			key = record.get("k") if isinstance(record, dict) else None
			if (
				not isinstance(key, list)
				or len(key) != 3
				or key[0] != "requests"
				or not isinstance(key[1], int)
			):
				continue
			if key[2] == "result":
				result = record.get("v")
				metadata = result.get("metadata") if isinstance(result, dict) else None
				if isinstance(metadata, dict) and isinstance(metadata.get("resolvedModel"), str):
					models[key[1]] = metadata["resolvedModel"]
			elif key[2] == "copilotCredits" and isinstance(record.get("v"), (int, float)):
				credits[key[1]] = record["v"]
	return models, credits


def latest_unreported_metadata(
	path: Path, seen_responses: set[int]
) -> tuple[int, str | None, float | None] | None:
	completed = load_completed_request_indices(path) - seen_responses
	if not completed:
		return None
	models, credits = load_request_metadata(path)
	index = max(completed)
	return index, models.get(index), credits.get(index)


def rendered_user_request(result: dict[str, Any]) -> str | None:
	metadata = result.get("metadata")
	parts = metadata.get("renderedUserMessage") if isinstance(metadata, dict) else None
	if not isinstance(parts, list):
		return None
	rendered = "".join(
		part["text"]
		for part in parts
		if isinstance(part, dict) and isinstance(part.get("text"), str)
	)
	match = re.search(r"<userRequest>\s*(.*?)\s*</userRequest>", rendered, re.DOTALL)
	return match.group(1).strip() if match else None


def load_transcript_history(path: Path) -> dict[str, tuple[Any, str]]:
	history: dict[str, tuple[Any, str]] = {}
	current_prompt: str | None = None
	current_timestamp: Any = None
	response_parts: list[str] = []
	with path.open("r", encoding="utf-8") as stream:
		for line in stream:
			try:
				event = json.loads(line)
			except json.JSONDecodeError:
				continue
			if not isinstance(event, dict):
				continue
			data = event.get("data")
			if not isinstance(data, dict):
				continue
			if event.get("type") == "user.message":
				if current_prompt is not None and response_parts:
					history[current_prompt] = (current_timestamp, "\n\n".join(response_parts))
				content = data.get("content")
				current_prompt = content if isinstance(content, str) else None
				current_timestamp = event.get("timestamp")
				response_parts = []
			elif (
				current_prompt is not None
				and event.get("type") == "assistant.message"
				and isinstance(data.get("content"), str)
			):
				response_parts.append(data["content"])
	if current_prompt is not None and response_parts:
		history[current_prompt] = (current_timestamp, "\n\n".join(response_parts))
	return history


def replay_history(session_path: Path, transcript_path: Path, session_id: str) -> int:
	records: list[dict[str, Any]] = []
	with session_path.open("r", encoding="utf-8") as stream:
		for line in stream:
			try:
				record = json.loads(line)
			except json.JSONDecodeError:
				continue
			if isinstance(record, dict):
				records.append(record)

	_, credits = load_request_metadata(session_path)
	timestamps = load_request_timestamps(session_path)
	transcript_history = load_transcript_history(transcript_path)
	results: dict[int, dict[str, Any]] = {}
	emitted: set[int] = set()
	printed = 0
	for record in records:
		key = record.get("k")
		if not (
			isinstance(key, list)
			and len(key) == 3
			and key[0] == "requests"
			and isinstance(key[1], int)
		):
			continue
		index = key[1]
		if key[2] == "result" and isinstance(record.get("v"), dict):
			results[index] = record["v"]
			continue
		if key[2] != "response" or index in emitted or index not in results:
			continue
		response = record.get("v")
		if not isinstance(response, list) or not response_content(response):
			continue
		result = results[index]
		prompt = rendered_user_request(result)
		transcript_message = transcript_history.get(prompt) if prompt else None
		timestamp = transcript_message[0] if transcript_message else timestamps.get(index)
		content = transcript_message[1] if transcript_message else response_content(response)
		if prompt:
			print(f"[{format_timestamp(timestamp)}] [{session_id}] USER")
			print(prompt)
			print()
		metadata = result.get("metadata")
		model = metadata.get("resolvedModel") if isinstance(metadata, dict) else None
		print(f"[{format_timestamp(timestamp)}] [{session_id}] COPILOT")
		print(content)
		print_response_metadata(model, credits.get(index))
		emitted.add(index)
		printed += 1
	return printed


def session_log_path(transcript_path: Path) -> Path:
	return transcript_path.parents[2] / "chatSessions" / f"{transcript_path.stem}.jsonl"


def open_transcript(
	path: Path, from_start: bool, streams: dict[Path, TextIO]
) -> None:
	stream = path.open("r", encoding="utf-8")
	if not from_start:
		stream.seek(0, os.SEEK_END)
	streams[path] = stream
	print(f"Watching: {path}")


def escape_pressed() -> bool:
	if not msvcrt.kbhit():
		return False
	return msvcrt.getwch() == "\x1b"


def follow(
	path: Path, interval: float, from_start: bool, raw: bool, intermediate: bool
) -> None:
	counts: Counter[str] = Counter()
	streams: dict[Path, TextIO] = {}
	seen_requests: set[str] = set()
	seen_responses: set[int] = set()
	ready_responses: set[int] = set()
	request_timestamps: dict[int, Any] = {}
	request_models: dict[int, str] = {}
	request_credits: dict[int, float] = {}
	session_path = session_log_path(path)
	try:
		while True:
			if path not in streams:
				open_transcript(path, from_start, streams)
			if session_path.is_file() and session_path not in streams:
				request_timestamps.update(load_request_timestamps(session_path))
				if not from_start:
					ready_responses.update(load_completed_request_indices(session_path))
					models, credits = load_request_metadata(session_path)
					request_models.update(models)
					request_credits.update(credits)
				open_transcript(session_path, from_start, streams)

			if session_path in streams:
				process_session_lines(
					streams[session_path],
					path.stem,
					counts,
					seen_requests,
					seen_responses,
					ready_responses,
					request_timestamps,
					request_models,
					request_credits,
				)
			process_lines(
				streams[path],
				path.stem,
				counts,
				raw,
				intermediate,
				session_path if session_path.is_file() and not from_start else None,
				seen_responses,
			)

			if escape_pressed():
				print("Stopped.")
				break
			time.sleep(interval)
	except KeyboardInterrupt:
		print("\nStopped.")
	finally:
		for stream in streams.values():
			stream.close()

	if counts:
		summary = ", ".join(f"{name}={count}" for name, count in counts.most_common())
		print(f"Events: {summary}")


def parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(
		description="Watch Visual Studio Code Copilot Chat transcript messages."
	)
	parser.add_argument(
		"--path",
		type=Path,
		help="Path to a transcript JSONL file. Defaults to an interactive session picker.",
	)
	parser.add_argument(
		"--history",
		"--from-start",
		dest="history",
		action="store_true",
		help="Print completed request history and exit (--from-start is a compatibility alias).",
	)
	parser.add_argument(
		"--raw",
		action="store_true",
		help="Print original JSON Lines, including non-message transcript events.",
	)
	parser.add_argument(
		"--intermediate",
		action="store_true",
		help="Also print Copilot intermediate transcript messages.",
	)
	parser.add_argument(
		"--interval",
		type=float,
		default=0.25,
		help="Polling interval in seconds (default: 0.25).",
	)
	args = parser.parse_args()
	if args.interval <= 0:
		parser.error("--interval must be greater than zero.")
	return args


def main() -> int:
	args = parse_args()
	try:
		storage_root = default_storage_root()
		if args.path and not args.path.is_file():
			raise FileNotFoundError(f"Transcript file not found: {args.path}")
		paths = discover_transcripts(storage_root) if not args.path else []
		if not args.path and not paths:
			raise FileNotFoundError(
				f"No Copilot Chat transcripts found below {storage_root}. Start a chat session first."
		)
		path = args.path or select_transcript(paths)
		if path is None:
			return 0
		if args.history:
			session_path = session_log_path(path)
			if session_path.is_file():
				replay_history(session_path, path, path.stem)
			else:
				print(f"Error: Session log not found: {session_path}", file=sys.stderr)
				return 1
		else:
			follow(path, args.interval, False, args.raw, args.intermediate)
	except (FileNotFoundError, OSError, RuntimeError) as error:
		print(f"Error: {error}", file=sys.stderr)
		return 1
	return 0


if __name__ == "__main__":
	raise SystemExit(main())

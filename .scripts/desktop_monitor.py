from __future__ import annotations

import argparse
import ctypes
import datetime
import json
import sys
import time
from contextlib import nullcontext
from ctypes import wintypes
from pathlib import Path
from typing import Any, TextIO


def positive_float(value: str) -> float:
	try:
		parsed = float(value)
	except ValueError as exc:
		raise argparse.ArgumentTypeError("must be a number") from exc
	if parsed <= 0:
		raise argparse.ArgumentTypeError("must be greater than zero")
	return parsed


def positive_int(value: str) -> int:
	try:
		parsed = int(value)
	except ValueError as exc:
		raise argparse.ArgumentTypeError("must be an integer") from exc
	if parsed <= 0:
		raise argparse.ArgumentTypeError("must be greater than zero")
	return parsed


def parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(
		description="Monitor the foreground window using Windows UI Automation."
	)
	parser.add_argument(
		"-i", "--interval", type=positive_float, default=1.0,
		metavar="SECONDS", help="Polling interval (default: 1 second).",
	)
	parser.add_argument(
		"-o", "--output", type=Path,
		help="Append changed snapshots as JSON Lines to this file; stdout is the default.",
	)
	parser.add_argument(
		"--max-depth", type=int, default=3, metavar="N",
		help="Maximum UI element tree depth (default: 3).",
	)
	parser.add_argument(
		"--max-elements", type=positive_int, default=300, metavar="N",
		help="Maximum elements per snapshot (default: 300).",
	)
	parser.add_argument(
		"--redact-names", action="store_true",
		help="Omit UI element names, which may contain visible text.",
	)
	parser.add_argument(
		"--once", action="store_true",
		help="Capture one snapshot and exit.",
	)
	args = parser.parse_args()
	if args.max_depth < 0:
		parser.error("--max-depth must be zero or greater")
	return args


def load_uiautomation() -> Any:
	if sys.platform != "win32":
		raise RuntimeError("This script can only run on Windows.")
	try:
		import uiautomation
	except ImportError as exc:
		raise RuntimeError(
			"Missing dependency. Install it with: py -m pip install -r requirements.txt"
		) from exc
	return uiautomation


def read_property(control: Any, property_name: str) -> str:
	try:
		value = getattr(control, property_name)
	except Exception:
		return ""
	return str(value).strip() if value is not None else ""


def enumerate_top_level_windows() -> list[dict[str, Any]]:
	if sys.platform != "win32":
		raise RuntimeError("This function can only run on Windows.")

	user32 = ctypes.WinDLL("user32", use_last_error=True)
	kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
	callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
	user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
	user32.EnumWindows.restype = wintypes.BOOL
	user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
	user32.GetWindowTextLengthW.restype = ctypes.c_int
	user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
	user32.GetWindowTextW.restype = ctypes.c_int
	user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
	user32.GetClassNameW.restype = ctypes.c_int
	user32.IsWindowVisible.argtypes = [wintypes.HWND]
	user32.IsWindowVisible.restype = wintypes.BOOL
	user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
	user32.GetWindowThreadProcessId.restype = wintypes.DWORD
	kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
	kernel32.OpenProcess.restype = wintypes.HANDLE
	kernel32.QueryFullProcessImageNameW.argtypes = [
		wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
	]
	kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
	kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
	kernel32.CloseHandle.restype = wintypes.BOOL

	processes: dict[int, dict[str, Any]] = {}
	callback_errors: list[Exception] = []

	@callback_type
	def collect_window(hwnd: int, _lparam: int) -> bool:
		try:
			title_length = user32.GetWindowTextLengthW(hwnd)
			title = ctypes.create_unicode_buffer(title_length + 1)
			user32.GetWindowTextW(hwnd, title, len(title))
			class_name = ctypes.create_unicode_buffer(256)
			user32.GetClassNameW(hwnd, class_name, len(class_name))
			process_id = wintypes.DWORD()
			user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
			pid = process_id.value
			if pid not in processes:
				executable_path = None
				process = kernel32.OpenProcess(0x1000, False, pid)
				if process:
					try:
						path = ctypes.create_unicode_buffer(32768)
						path_length = wintypes.DWORD(len(path))
						if kernel32.QueryFullProcessImageNameW(process, 0, path, ctypes.byref(path_length)):
							executable_path = path.value
					finally:
						kernel32.CloseHandle(process)
				processes[pid] = {
					"process_id": pid or None,
					"executable_path": executable_path,
					"executable_name": Path(executable_path).name if executable_path else None,
					"windows": [],
				}
			processes[pid]["windows"].append({
				"hwnd": hwnd,
				"title": title.value,
				"class_name": class_name.value,
				"visible": bool(user32.IsWindowVisible(hwnd)),
			})
			return True
		except Exception as exc:
			callback_errors.append(exc)
			return False

	succeeded = user32.EnumWindows(collect_window, 0)
	if callback_errors:
		raise callback_errors[0]
	if not succeeded:
		raise ctypes.WinError(ctypes.get_last_error())
	return list(processes.values())


def capture_control(
	control: Any,
	depth: int,
	max_depth: int,
	remaining_elements: list[int],
	redact_names: bool,
) -> dict[str, Any]:
	remaining_elements[0] -= 1
	try:
		is_password = bool(getattr(control, "IsPassword", False))
	except Exception:
		is_password = False

	name = "[REDACTED]" if is_password or redact_names else read_property(control, "Name")
	node: dict[str, Any] = {
		"control_type": read_property(control, "ControlTypeName") or type(control).__name__,
	}
	if name:
		node["name"] = name
	for property_name, key in (("AutomationId", "automation_id"), ("ClassName", "class_name")):
		value = read_property(control, property_name)
		if value:
			node[key] = value

	if depth < max_depth and remaining_elements[0] > 0:
		try:
			children = control.GetChildren()
		except Exception:
			children = []
		node["children"] = [
			capture_control(child, depth + 1, max_depth, remaining_elements, redact_names)
			for child in children
			if remaining_elements[0] > 0
		]
	return node


def capture_snapshot(automation: Any, args: argparse.Namespace) -> dict[str, Any]:
	control = automation.GetForegroundControl()
	if control is None:
		raise RuntimeError("Windows UI Automation did not return a foreground window.")
	return capture_control(
		control,
		depth=0,
		max_depth=args.max_depth,
		remaining_elements=[args.max_elements],
		redact_names=args.redact_names,
	)


def write_control_children(stream: TextIO, control: list[dict[str, Any]], indent: int) -> None:
	for child in control:
		control_type = child.get("control_type", "")
		class_name = child.get("class_name", "")
		name = child.get("name", "")
		children = child.get("children", [])
		stream.write(f"{'  ' * indent}[{control_type}][{class_name}]:{name}\n")
		for item in child:
			if item == "children" or item == "control_type" or item == "class_name" or item == "name":
				continue
			stream.write(f"{'  ' * indent}{item}: {child[item]}\n")

		if children:
			write_control_children(stream, children, indent + 1)
	

def write_snapshot(stream: TextIO, snapshot: dict[str, Any], windows: list[dict[str, Any]]) -> None:
	indent = 0
	timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y/%m/%d %H:%M:%S.%f")[:-3]
	control_type = snapshot.get("control_type", "")
	children = snapshot.get("children", [])
	class_name = snapshot.get("class_name", "")
	name = snapshot.get("name", "")
	stream.write(f"[{timestamp}][{control_type}][{class_name}]:{name}\n")
	for window in windows:
		stream.write(f"{'  ' * indent}{window}\n")
	if children:
		write_control_children(stream, children, indent + 1)

	stream.flush()


def main() -> int:
	args = parse_args()
	try:
		automation = load_uiautomation()
	except RuntimeError as exc:
		print(exc, file=sys.stderr)
		return 1

	if args.output is None:
		output_context = nullcontext(sys.stdout)
	else:
		try:
			args.output.parent.mkdir(parents=True, exist_ok=True)
			output_context = args.output.open("a", encoding="utf-8", buffering=1)
		except OSError as exc:
			print(f"Cannot open output file: {exc}", file=sys.stderr)
			return 1

	previous_snapshot = None
	last_error = None
	try:
		with output_context as stream:
			while True:
				try:
					snapshot = capture_snapshot(automation, args)
#					windows = enumerate_top_level_windows()
					windows = []
				except Exception as exc:
					message = f"UI Automation read failed: {type(exc).__name__}: {exc}"
					if message != last_error:
						print(message, file=sys.stderr)
					last_error = message
					if args.once:
						return 1
				else:
					last_error = None
					if snapshot != previous_snapshot:
						write_snapshot(stream, snapshot, windows)
						previous_snapshot = snapshot
				if args.once:
					return 0
				time.sleep(args.interval)
	except KeyboardInterrupt:
		return 0


if __name__ == "__main__":
	sys.exit(main())
#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path
import datetime

time_stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
log_dir = Path(__file__).resolve().parent / f"test_excel_{time_stamp}"
log_dir.mkdir(exist_ok=True)

TARGET_WB = "sample\\test_macro.xlsm"
#TARGET_WB = "sample\\test_macro_365.xlsm"
test_args = {
    1: ["1", TARGET_WB],
    2: ["2", TARGET_WB],
    3: ["3", "sample", "コメント", "True", "False", "all"],
    4: ["4", TARGET_WB],
    5: ["5", TARGET_WB, "目次", "1:50"],
    6: ["6", TARGET_WB, "オートフィルタ", "B2:G22"],
    7: ["7", TARGET_WB, f"{log_dir}\\test7", ""],
    8: ["8", TARGET_WB, f"{log_dir}\\test8\\test_macro.vba"],
    9: ["9", TARGET_WB, "sample\\test_macro_diff.xlsm"],
    10: ["10", TARGET_WB],
    11: ["11", TARGET_WB, "表と式", "A1:U25", f"{log_dir}\\test11\\test_macro.png"],
    12: ["11", TARGET_WB, "グラフのみのシート", "A1:U25", f"{log_dir}\\test12\\test_macro.png"],
    13: ["11", TARGET_WB, "縦長", "B43:N122", f"{log_dir}\\test13\\test_macro.png"],
    14: ["10", TARGET_WB, "縦長"],
}

def main():
    script_path = Path(__file__).resolve().parent / ".scripts" / "mcp_excel.py"
    test_no = 1

    for test_no, test_arg in test_args.items():
        log_file_path = log_dir / f"test_{test_no:03d}.txt"
        with open(log_file_path, "w", encoding="utf-8") as log_file:
            print(f"============================================ test {test_no:3d} ============================================", file=log_file)
            print(f"============================================ test {test_no:3d} ============================================")
            cmd = [sys.executable, str(script_path), "--test", test_arg[0], "--test-args", ",".join(test_arg[1:])]
            print(f"Running command: {' '.join(cmd)}", file=log_file)
            print(f"Running command: {' '.join(cmd)}")
            result = subprocess.run(
                cmd,
                cwd=Path(__file__).resolve().parent,
                capture_output=True,
                text=True,
            )

            if result.stdout:
                print("-------------------------------------------- stdout -------------------------------------------", file=log_file)
                print(result.stdout, end="", file=log_file)

            if result.stderr:
                print("-------------------------------------------- stderr -------------------------------------------", file=log_file)
                print(result.stderr, end="", file=log_file)

            combined_output = result.stdout + result.stderr
            if "Unknown test case" in combined_output:
                print(f"Reached unknown test case at {test_no}")
                break

            if result.returncode != 0:
                print(f"Execution failed with exit code {result.returncode}")
                break

            test_no += 1


if __name__ == "__main__":
    main()

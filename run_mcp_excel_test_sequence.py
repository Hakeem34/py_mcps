#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path
import datetime

time_stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
log_dir = Path(__file__).resolve().parent / f"test_excel_{time_stamp}"
log_dir.mkdir(exist_ok=True)

test_args = {
    1: ["sample\\test_macro.xlsm"],
    2: ["sample\\test_macro.xlsm"],
    3: ["sample", "コメント", "True", "False", "all"],
    4: ["sample\\test_macro.xlsm"],
    5: ["sample\\test_macro.xlsm", "目次", "1:50"],
    6: ["sample\\test_macro.xlsm", "オートフィルタ", "B2:G22"],
    7: ["sample\\test_macro.xlsm", f"{log_dir}\\test7", ""],
    8: ["sample\\test_macro.xlsm", f"{log_dir}\\test8\\test_macro.vba"],
    9: ["sample\\test_macro.xlsm", "sample\\test_macro_diff.xlsm"],
    10: ["sample\\test_macro.xlsm"],
    11: ["sample\\test_macro.xlsm", "表と式", "A1:U25", f"{log_dir}\\test11\\test_macro.png"],
#   11: ["sample\\test_macro_25.xlsm", "グラフのみのシート", "A1:U25", f"{log_dir}\\test11\\test_macro.png"],
}

def main():
    script_path = Path(__file__).resolve().parent / ".scripts" / "mcp_excel.py"
    test_no = 1

    while True:
        log_file_path = log_dir / f"test_{test_no:03d}.txt"
        with open(log_file_path, "w", encoding="utf-8") as log_file:
            print(f"============================================ test {test_no:3d} ============================================", file=log_file)
            print(f"============================================ test {test_no:3d} ============================================")
            cmd = [sys.executable, str(script_path), "--test", str(test_no), "--test-args", ",".join(test_args.get(test_no, []))]
            print(f"Running command: {' '.join(cmd)}", file=log_file)
            print(f"Running command: {' '.join(cmd)}")
            result = subprocess.run(
                [sys.executable, str(script_path), "--test", str(test_no), "--test-args", ",".join(test_args.get(test_no, []))],
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

#!/usr/bin/env python3
import os
import subprocess
import sys
from pathlib import Path


def run_ts_scraper(days: int = 30) -> bool:
    print(f"[Python] Starting the hotspot capture pipeline (Last {days} days)...")
        
    # 1. Finds the folder where this Python script is saved
    current_dir = Path(__file__).parent.resolve()
    
    # 2. Builds the path pointing to the correct subfolder
    # Note: Updated 'dayly.ts' to 'daily.ts' to fix the spelling typo
    ts_script = current_dir / "webscrap" / "daily.ts"
    
    if not os.path.exists(ts_script):
        print(f"[Error] The file {ts_script} was not found in the current directory.")
        return False

    try:
        # Runs the command 'bun run daily.ts <days>'
        # text=True ensures the output comes as a String instead of Bytes
        # bufsize=1 enables line buffering so we can capture the log in real-time
        process = subprocess.Popen(
            ["bun", "run", ts_script, str(days)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1
        )

        # Captures Bun's stdout line by line as it executes (log streaming)
        if process.stdout:
            for line in process.stdout:
                print(f"[Bun] {line.strip()}")

        # Waits for the process to fully complete
        stdout, stderr = process.communicate()

        if process.returncode == 0:
            print("[Python] TypeScript script executed successfully and files compressed!")
            return True
        else:
            print(f"[Error] The TypeScript script failed with exit code: {process.returncode}")
            if stderr:
                print(f"[Bun Stderr]:\n{stderr}")
            return False

    except FileNotFoundError:
        print("[Error] The 'bun' executable was not found in the system PATH.")
        print("Make sure Bun is installed and properly configured.")
        return False
    except Exception as e:
        print(f"[Unexpected Error]: {str(e)}")
        return False


if __name__ == "__main__":
    # Allows passing the number of days via command line: python scraper_runner.py 60
    days_arg = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    run_ts_scraper(days_arg)
from datetime import datetime
from pathlib import Path
import os
import subprocess
import sys


def main() -> int:
    project_root = Path(__file__).resolve().parent
    tmp_dir = project_root / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f")
    log_path = tmp_dir / f"server.{timestamp}.log"

    print(f"日志文件: {log_path}", flush=True)

    with open(log_path, "a", encoding="utf-8") as log_file:
        log_file.write(f"启动时间: {datetime.now().isoformat()}\n")
        log_file.flush()

        process = subprocess.Popen(
            [sys.executable, "app.py", *sys.argv[1:]],
            cwd=project_root,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            env=os.environ.copy(),
        )

        try:
            return process.wait()
        except KeyboardInterrupt:
            process.terminate()
            try:
                return process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                return process.wait()


if __name__ == "__main__":
    raise SystemExit(main())

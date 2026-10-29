"""Run local development servers and stop their process trees on exit."""

import os
import signal
import subprocess
import sys
import time

from common import ENV, ROOT, command


def stop(_signum: int, _frame: object) -> None:
    raise KeyboardInterrupt


def main() -> int:
    signal.signal(signal.SIGTERM, stop)
    processes: list[subprocess.Popen] = []
    services = [
        (
            "backend",
            command(
                "uv",
                "run",
                "--locked",
                "uvicorn",
                "campus_inbox.main:app",
                "--reload",
                "--host",
                "127.0.0.1",
                "--port",
                "8000",
            ),
        ),
        ("frontend", command("npm", "run", "dev")),
    ]
    try:
        for directory, args in services:
            options = (
                {"start_new_session": True}
                if os.name != "nt"
                else {
                    "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP,
                }
            )
            processes.append(subprocess.Popen(args, cwd=ROOT / directory, env=ENV, **options))
        print("Frontend: http://127.0.0.1:5173 | API docs: http://127.0.0.1:8000/docs", flush=True)
        while all(process.poll() is None for process in processes):
            time.sleep(0.3)
        return next(
            (process.returncode or 1 for process in processes if process.poll() is not None), 1
        )
    except KeyboardInterrupt:
        return 0
    finally:
        for process in processes:
            if os.name == "nt":
                if process.poll() is None:
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], check=False)
            else:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                if os.name == "nt":
                    process.kill()
                else:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                process.wait()


if __name__ == "__main__":
    sys.exit(main())

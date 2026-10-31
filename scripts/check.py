"""Run the same primary checks as CI without fixing source files."""

from common import run

if __name__ == "__main__":
    run("backend", "uv", "run", "--locked", "ruff", "check", ".")
    run("backend", "uv", "run", "--locked", "ruff", "format", "--check", ".")
    run("backend", "uv", "run", "--locked", "ruff", "check", "../scripts")
    run("backend", "uv", "run", "--locked", "ruff", "format", "--check", "../scripts")
    run("backend", "uv", "run", "--locked", "pytest")
    run("frontend", "npm", "run", "lint:check")
    run("frontend", "npm", "run", "format:check")
    run("frontend", "npm", "run", "test:calendar")
    run("frontend", "npm", "run", "build")

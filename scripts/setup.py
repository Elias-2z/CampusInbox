"""Install locked dependencies without modifying lockfiles."""

from common import run

if __name__ == "__main__":
    run("backend", "uv", "sync", "--locked")
    run("frontend", "npm", "ci")

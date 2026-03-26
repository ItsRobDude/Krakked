import subprocess

print(
    subprocess.run(
        ["poetry", "run", "pre-commit", "run", "--all-files"],
        capture_output=True,
        text=True,
    ).stdout
)

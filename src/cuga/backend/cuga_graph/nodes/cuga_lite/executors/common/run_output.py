"""Format ``run_command`` tool return strings."""


def format_run_command_output(stdout: str, stderr: str, *, failed: bool) -> str:
    output = stdout
    if failed and stderr.strip():
        output += f"\n[stderr]\n{stderr}"
    return output or "(command completed with no output)"

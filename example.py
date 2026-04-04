import asyncio
from datetime import timedelta

from code_interpreter import CodeInterpreter, SupportedLanguage
from opensandbox import Sandbox
from opensandbox.models import WriteEntry


async def main() -> None:
    # 1. Create a sandbox
    sandbox = await Sandbox.create(
        "opensandbox/code-interpreter:v1.0.2",
        entrypoint=["/opt/opensandbox/code-interpreter.sh"],
        env={"PYTHON_VERSION": "3.11"},
        timeout=timedelta(minutes=10),
    )

    async with sandbox:
        # 2. Execute a shell command
        execution = await sandbox.commands.run("echo 'Hello OpenSandbox!'")
        print(execution.logs.stdout[0].text)

        # 3. Write a file
        await sandbox.files.write_files([WriteEntry(path="/tmp/hello.txt", data="Hello World", mode=644)])

        # 4. Read a file
        content = await sandbox.files.read_file("/tmp/hello.txt")
        print(f"Content: {content}")  # Content: Hello World

        # 5. Create a code interpreter
        interpreter = await CodeInterpreter.create(sandbox)

        # 6. Execute Python code (single-run, pass language directly)
        result = await interpreter.codes.run(
            """
                  import sys
                  print(sys.version)
                  result = 2 + 2
                  result
              """,
            language=SupportedLanguage.PYTHON,
        )

        print(result.result[0].text)  # 4
        print(result.logs.stdout[0].text)  # 3.11.14

    # 7. Cleanup the sandbox
    await sandbox.kill()


if __name__ == "__main__":
    asyncio.run(main())

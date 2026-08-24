#!/usr/bin/env python3
"""
Extract Python code from markdown-formatted text files and create/update a FastMCP server.
"""

import re
import ast
import argparse
from pathlib import Path
from typing import List, Dict, Tuple, Optional
from loguru import logger

# Assuming these are in your project structure
from cuga.backend.tools_env.code_sandbox.sandbox import get_premable
from cuga.config import settings


def extract_python_code_blocks(text_content: str) -> List[str]:
    """Extract all Python code blocks from markdown text."""
    pattern = r'```python\s*\n(.*?)\n```'
    return re.findall(pattern, text_content, re.DOTALL)


def extract_imports_and_functions_from_code(code_string: str) -> Tuple[List[Dict], List[Dict]]:
    """Extract imports and function definitions from a Python code string."""
    try:
        tree = ast.parse(code_string)
        functions = []
        imports = []
        all_found_functions = []
        skipped_functions = []
        code_lines = code_string.split('\n')

        # Only get top-level function definitions, not nested ones
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                all_found_functions.append(node.name)
                if node.name == "call_api":
                    skipped_functions.append(node.name)
                    continue
                # Reconstruct the function source from the original code
                func_lines = code_lines[node.lineno - 1 : node.end_lineno]
                func_source = '\n'.join(func_lines)
                functions.append({'name': node.name, 'source': func_source})
                func_type = "async" if isinstance(node, ast.AsyncFunctionDef) else "sync"
                logger.debug(f"Extracted {func_type} function '{node.name}' ({len(func_source)} chars)")
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                import_lines = code_lines[node.lineno - 1 : node.end_lineno or node.lineno]
                import_source = '\n'.join(import_lines)
                imports.append({'source': import_source})

        logger.debug(f"All top-level functions found: {all_found_functions}")
        if skipped_functions:
            logger.debug(f"Skipped functions: {skipped_functions}")
        if functions:
            logger.info(f"Extracted {len(functions)} function(s): {[f['name'] for f in functions]}")
        else:
            logger.error(
                f"No extractable functions found. Top-level items: {[type(node).__name__ for node in tree.body[:10]]}"
            )

        return imports, functions
    except SyntaxError as e:
        logger.error(f"Syntax error in code block: {e}")
        return [], []


def parse_existing_server(server_file: Path) -> Tuple[List[str], List[str], Optional[str]]:
    """Parse an existing server.py file to extract imports and tool function names."""
    try:
        content = server_file.read_text(encoding='utf-8')
        tree = ast.parse(content)
        existing_imports = set()
        existing_functions = []

        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                # Store the source code of the import statement
                existing_imports.add(ast.get_source_segment(content, node).strip())
            elif isinstance(node, ast.FunctionDef):
                # Check if it has the @mcp.tool decorator
                for decorator in node.decorator_list:
                    if (
                        isinstance(decorator, ast.Attribute)
                        and isinstance(decorator.value, ast.Name)
                        and decorator.value.id == 'mcp'
                        and decorator.attr == 'tool'
                    ):
                        existing_functions.append(node.name)
                        break
        return list(existing_imports), existing_functions, content
    except FileNotFoundError:
        return [], [], None
    except Exception as e:
        logger.warning(f"Error parsing existing server '{server_file}': {e}")
        return [], [], None


def validate_python_code(code_string: str) -> bool:
    """Check if the given string is valid Python code."""
    try:
        ast.parse(code_string)
        return True
    except SyntaxError as e:
        logger.error(f"Validation Error: Generated code has a syntax error: {e}")
        return False


def generate_or_update_server(
    all_functions_data: List[Dict], all_imports: List[Dict], output_file: Path, mode: str
) -> bool:
    """Generate a new server or update an existing one."""
    if mode == "create":
        # --- CREATE NEW SERVER ---
        seen_imports = {'from fastmcp import FastMCP'}
        unique_imports = []
        for imp in all_imports:
            import_line = imp['source'].strip()
            if import_line and import_line not in seen_imports:
                unique_imports.append(import_line)
                seen_imports.add(import_line)

        imports_section = '\n'.join(unique_imports) + '\n' if unique_imports else ''
        functions_section = "".join(f"\n@mcp.tool\n{func['source']}\n" for func in all_functions_data)

        server_content = f'''# {output_file.name}

from fastmcp import FastMCP

# This generated server runs as its own OS process — instrumentation from
# the main backend's process doesn't reach it (see traceloop_init.py).
import cuga.backend.observability.traceloop_init as _traceloop_init  # noqa: F401
{imports_section}
{get_premable(is_local=settings.features.local_sandbox)}

mcp = FastMCP("Demo 🚀")
{functions_section}
if __name__ == "__main__":
    mcp.run(transport="sse", host="127.0.0.1", port={settings.server_ports.saved_flows})
'''
        if not validate_python_code(server_content):
            logger.error("Aborting file creation due to syntax errors.")
            return False

        output_file.write_text(server_content, encoding='utf-8')
        logger.info(f"Generated new FastMCP server: {output_file}")
        logger.info(f"Added {len(all_functions_data)} function(s): {[f['name'] for f in all_functions_data]}")
        return True

    else:  # --- UPDATE EXISTING SERVER ---
        existing_imports, existing_functions, existing_content = parse_existing_server(output_file)
        if existing_content is None:
            logger.warning(f"Could not read '{output_file}', creating a new one instead.")
            return generate_or_update_server(all_functions_data, all_imports, output_file, "create")

        # Filter out functions and imports that already exist
        new_functions = [f for f in all_functions_data if f['name'] not in existing_functions]
        new_imports = [
            imp['source'].strip()
            for imp in all_imports
            if imp['source'].strip() not in existing_imports and 'from fastmcp import' not in imp['source']
        ]
        new_imports = sorted(list(set(new_imports)))  # Unique and sorted

        if not new_functions and not new_imports:
            logger.info("No new functions or imports to add. Server is already up-to-date.")
            return True  # This is still success - nothing needed updating

        # Prepare new code snippets
        functions_to_add = "\n".join(f"\n@mcp.tool\n{func['source']}" for func in new_functions)
        imports_to_add = "\n".join(new_imports)

        # Find insertion points
        lines = existing_content.split('\n')
        # Find last import to insert new ones after it
        import_insert_line = 0
        for i, line in enumerate(lines):
            if line.strip().startswith(('import ', 'from ')):
                import_insert_line = i + 1

        # Find the main block to insert functions before it
        main_block_line = len(lines)
        for i, line in enumerate(lines):
            if line.strip().startswith('if __name__ == "__main__":'):
                main_block_line = i
                break

        # Insert new code
        if imports_to_add:
            lines.insert(import_insert_line, imports_to_add + '\n')
            # Adjust main block line number if imports were added before it
            main_block_line += imports_to_add.count('\n') + 2

        if functions_to_add:
            lines.insert(main_block_line, functions_to_add + '\n')

        updated_content = '\n'.join(lines)

        # Final validation before writing to disk
        if not validate_python_code(updated_content):
            logger.error(
                "Critical Error: The updated code is not valid Python. Aborting update to prevent corruption."
            )
            return False

        output_file.write_text(updated_content, encoding='utf-8')
        logger.info(f"Updated FastMCP server: {output_file}")
        if new_functions:
            logger.info(f"Added {len(new_functions)} new function(s): {[f['name'] for f in new_functions]}")
        if new_imports:
            logger.info(f"Added {len(new_imports)} new import(s): {new_imports}")
        return True


def process_text_file(
    input_file: Optional[Path] = None,
    output_file: Optional[Path] = None,
    mode: Optional[str] = 'auto',
    input_text: Optional[str] = None,
) -> bool:
    """Main function to process the text file and generate/update server. Returns True on success, False on failure."""
    if mode == "auto":
        mode = "update" if output_file.exists() else "create"
        logger.debug(f"Auto-detected mode: '{mode}'")

    if input_text:
        content = input_text
    elif input_file:
        try:
            content = input_file.read_text(encoding='utf-8')
        except FileNotFoundError:
            logger.error(f"Error: File '{input_file}' not found")
            return False
    else:
        logger.error("Error: No input file or text provided.")
        return False

    code_blocks = extract_python_code_blocks(content)
    if not code_blocks:
        logger.error("No Python code blocks found in the input.")
        return False

    logger.info(f"Found {len(code_blocks)} Python code block(s). Processing...")

    all_functions_data, all_imports = [], []
    for idx, code_block in enumerate(code_blocks):
        logger.debug(f"Processing code block {idx + 1}/{len(code_blocks)} (length: {len(code_block)} chars)")
        imports, functions = extract_imports_and_functions_from_code(code_block)
        all_functions_data.extend(functions)
        all_imports.extend(imports)

    if not all_functions_data:
        logger.error("No functions found in any code block.")
        return False

    logger.info(f"Found {len(all_functions_data)} total function(s). Generating/updating server...")
    return generate_or_update_server(all_functions_data, all_imports, output_file, mode)


def main():
    """Command line interface."""
    parser = argparse.ArgumentParser(
        description="Extract Python functions from a markdown file to a FastMCP server.",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("input_file", type=Path, help="Path to the input markdown file (.txt, .md)")
    parser.add_argument(
        "output_file",
        type=Path,
        nargs="?",
        default=Path("server.py"),
        help="Path for the output server file (default: server.py)",
    )
    parser.add_argument(
        "-m",
        "--mode",
        choices=["auto", "create", "update"],
        default="auto",
        help="""Operation mode:
  - auto: update if output_file exists, otherwise create (default)
  - create: always create a new file, overwriting if exists
  - update: update an existing file, fail if it doesn't exist""",
    )
    args = parser.parse_args()

    print(f"🚀 Processing: {args.input_file}")
    print(f"📝 Output: {args.output_file}")
    print(f"🔧 Mode: {args.mode}")
    print("-" * 50)

    process_text_file(args.input_file, args.output_file, args.mode)

    print("-" * 50)
    print("✅ Process finished.")
    print(f"👉 To run your server: python {args.output_file}")


if __name__ == "__main__":
    main()

import json
import os
from typing import Any, Dict, Union

import yaml

from cuga.backend.llm.utils.helpers import get_caller_directory_path


def get_path_relative_to_dir(file, path):
    current_directory = os.path.dirname(os.path.abspath(file))
    return os.path.join(current_directory, path)


def read_yaml_file(file_path: str, relative: bool = True) -> Union[Dict[str, Any], Any]:
    """
    Reads a YAML file with robust cross-platform UTF-8 encoding handling.

    This function works correctly on Windows, macOS, and Linux by:
    - Explicitly handling UTF-8 encoding (avoiding Windows default cp1252)
    - Supporting UTF-8 BOM (Byte Order Mark) via 'utf-8-sig'
    - Providing a fallback for malformed encodings
    - Expanding environment variables in the content
    - Raising descriptive errors for invalid YAML

    Args:
        file_path: Path to the YAML file (relative or absolute)
        relative: If True, resolves path relative to caller's directory

    Returns:
        Parsed YAML data as dict or other YAML-supported type

    Raises:
        ValueError: If YAML parsing fails or unable to determine caller directory
        FileNotFoundError: If file doesn't exist
    """
    if relative:
        source_path = get_caller_directory_path()
        if source_path is None:
            raise ValueError("Unable to determine caller directory path")
        file_path = os.path.join(source_path, file_path)

    # Try UTF-8 (common), then UTF-8 with BOM (utf-8-sig), then a tolerant fallback
    def _read_text_with_encoding(path: str) -> str:
        for enc in ("utf-8", "utf-8-sig"):
            try:
                with open(path, "r", encoding=enc) as f:
                    return f.read()
            except UnicodeDecodeError:
                pass
        # Fallback: replace undecodable bytes so file still loads
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()

    content = _read_text_with_encoding(file_path)

    # Expand environment variables like $VAR and ${VAR}
    expanded_content = os.path.expandvars(content)

    try:
        data = yaml.safe_load(expanded_content)
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML in '{file_path}': {e}") from e

    return data


def read_json_file(file_path):
    """
    Read and parse a JSON file from the specified path.

    Args:
        file_path (str): Path to the JSON file

    Returns:
        dict: The parsed JSON data
    """
    try:
        with open(file_path, 'r') as file:
            data = json.load(file)
        return data
    except FileNotFoundError:
        print(f"Error: File not found at {file_path}")
    except json.JSONDecodeError:
        print(f"Error: Invalid JSON format in {file_path}")
    except Exception as e:
        print(f"Error reading file: {e}")

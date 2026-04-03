#!/usr/bin/env python3
"""
Script to embed frontend dist and extension assets as compressed base64 data.
This creates a single Python file with all assets embedded.
"""

import os
import io
import zipfile
import base64
from pathlib import Path


def create_zip_from_directory(directory_path, exclude_patterns=None):
    """Create a zip archive from a directory."""
    if exclude_patterns is None:
        exclude_patterns = ['.DS_Store', '__pycache__', '*.pyc']

    zip_buffer = io.BytesIO()

    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        for root, dirs, files in os.walk(directory_path):
            # Filter out excluded directories
            dirs[:] = [
                d for d in dirs if not any(d.startswith(pattern.rstrip('*')) for pattern in exclude_patterns)
            ]

            for file in files:
                # Skip excluded files
                if any(file.endswith(pattern.lstrip('*')) or file == pattern for pattern in exclude_patterns):
                    continue

                file_path = os.path.join(root, file)
                archive_name = os.path.relpath(file_path, directory_path)
                zip_file.write(file_path, archive_name)

    return zip_buffer.getvalue()


def embed_assets():
    """Generate embedded assets Python file."""
    base_dir = Path(__file__).parent.parent
    frontend_dist = base_dir / "cuga" / "frontend" / "dist"
    extension_dir = base_dir / "frontend_workspaces" / "extension" / "releases" / "chrome-mv3"

    print(f"Embedding frontend from: {frontend_dist}")
    print(f"Embedding extension from: {extension_dir}")

    # Create zip archives

    frontend_zip = create_zip_from_directory(frontend_dist)
    extension_zip = create_zip_from_directory(extension_dir)

    # Encode as base64
    frontend_b64 = base64.b64encode(frontend_zip).decode('utf-8')
    extension_b64 = base64.b64encode(extension_zip).decode('utf-8')

    # Generate Python code
    embedded_code = f'''"""
Embedded assets for CUGA application.
Generated automatically - do not edit manually.
"""

import base64
import io
import zipfile
import tempfile
import os
import shutil
from pathlib import Path
from typing import Optional

# Embedded frontend dist (base64 encoded zip)
FRONTEND_DIST_B64 = """{frontend_b64}"""

# Embedded extension (base64 encoded zip)
EXTENSION_B64 = """{extension_b64}"""

class EmbeddedAssets:
    """Manages embedded assets extraction and serving."""
    
    def __init__(self):
        self.temp_dir: Optional[Path] = None
        self.frontend_path: Optional[Path] = None
        self.extension_path: Optional[Path] = None
    
    def extract_assets(self) -> tuple[Path, Path]:
        """Extract embedded assets to temporary directory."""
        if self.temp_dir and self.temp_dir.exists():
            return self.frontend_path, self.extension_path
            
        # Create temporary directory
        self.temp_dir = Path(tempfile.mkdtemp(prefix="cuga_assets_"))
        
        # Extract frontend
        frontend_zip_data = base64.b64decode(FRONTEND_DIST_B64)
        self.frontend_path = self.temp_dir / "frontend_dist"
        self.frontend_path.mkdir(exist_ok=True)
        
        with zipfile.ZipFile(io.BytesIO(frontend_zip_data), 'r') as zip_file:
            zip_file.extractall(self.frontend_path)
        
        # Extract extension
        extension_zip_data = base64.b64decode(EXTENSION_B64)
        self.extension_path = self.temp_dir / "chrome_extension"
        self.extension_path.mkdir(exist_ok=True)
        
        with zipfile.ZipFile(io.BytesIO(extension_zip_data), 'r') as zip_file:
            zip_file.extractall(self.extension_path)
        
        print(f"Assets extracted to: {{self.temp_dir}}")
        return self.frontend_path, self.extension_path
    
    def cleanup(self):
        """Clean up temporary assets."""
        if self.temp_dir and self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)
            print(f"Cleaned up assets from: {{self.temp_dir}}")
    
    def __del__(self):
        """Cleanup on deletion."""
        self.cleanup()

# Global instance
embedded_assets = EmbeddedAssets()

def get_frontend_path() -> Path:
    """Get path to extracted frontend assets."""
    frontend_path, _ = embedded_assets.extract_assets()
    return frontend_path

def get_extension_path() -> Path:
    """Get path to extracted extension assets."""
    _, extension_path = embedded_assets.extract_assets()
    return extension_path
'''

    # Write to file
    output_file = base_dir / "cuga" / "backend" / "server" / "embedded_assets.py"
    with open(output_file, 'w') as f:
        f.write(embedded_code)

    print(f"✅ Embedded assets written to: {output_file}")
    print(f"📦 Frontend size: {len(frontend_zip) / 1024 / 1024:.2f} MB")
    print(f"📦 Extension size: {len(extension_zip) / 1024 / 1024:.2f} MB")
    print(f"📄 Total embedded size: {len(frontend_b64 + extension_b64) / 1024 / 1024:.2f} MB")


if __name__ == "__main__":
    embed_assets()

#!/usr/bin/env python3
"""
E2E test script for the CRM API
Tests the query: "Find all contacts from my CRM accounts for opportunities $10k and 50% likelihood"
"""

import subprocess
import sys


def run_e2e_tests():
    """Run the e2e test suite"""
    try:
        print("🧪 Running E2E Tests for CRM API")
        print("=" * 50)

        # Install test dependencies
        print("📦 Installing test dependencies...")
        install_result = subprocess.run(["uv", "sync"], capture_output=True, text=True)

        if install_result.returncode != 0:
            print("❌ Failed to install dependencies:")
            print(install_result.stderr)
            return False

        # Run tests with uv
        print("🚀 Running tests...")
        result = subprocess.run(
            ["uv", "run", "python", "-m", "pytest", "-v", "--tb=short", "tests/"],
            capture_output=True,
            text=True,
        )

        print("STDOUT:")
        print(result.stdout)

        if result.stderr:
            print("STDERR:")
            print(result.stderr)

        success = result.returncode == 0
        if success:
            print("✅ All E2E tests passed!")
        else:
            print("❌ Some tests failed.")

        return success

    except FileNotFoundError:
        print("❌ Error: uv not found. Please install uv first.")
        return False
    except Exception as e:
        print(f"❌ Error running tests: {e}")
        return False


if __name__ == "__main__":
    success = run_e2e_tests()
    sys.exit(0 if success else 1)

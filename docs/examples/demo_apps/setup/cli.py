#!/usr/bin/env python3
"""
CUGA Demo Setup CLI
A one-command solution to set up and run the CUGA Agent demo
"""

import os
import sys
import subprocess
import time
import signal
import atexit
from pathlib import Path
from typing import Optional, List, Tuple
import questionary
import argparse


# ANSI color codes for beautiful output
class Colors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'


# Track running processes for cleanup
running_processes: List[subprocess.Popen] = []


def cleanup():
    """Clean up all running processes on exit"""
    if running_processes:
        print(f"\n{Colors.WARNING}🧹 Cleaning up processes...{Colors.ENDC}")
        for proc in running_processes:
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass


atexit.register(cleanup)


def signal_handler(sig, frame):
    """Handle Ctrl+C gracefully"""
    print(f"\n{Colors.WARNING}👋 Shutting down gracefully...{Colors.ENDC}")
    cleanup()
    sys.exit(0)


signal.signal(signal.SIGINT, signal_handler)


def print_header():
    """Print a beautiful header"""
    header = f"""
{Colors.BOLD}{Colors.OKCYAN}
╔═══════════════════════════════════════════════════════════╗
║                                                           ║
║         🚀  CUGA Agent Demo Setup                        ║
║                                                           ║
║         Setting up your agentic workflow environment     ║
║                                                           ║
╚═══════════════════════════════════════════════════════════╝
{Colors.ENDC}
"""
    print(header)


def print_step(step_num: int, total: int, message: str):
    """Print a step with nice formatting"""
    print(f"\n{Colors.BOLD}{Colors.OKBLUE}[{step_num}/{total}] {message}{Colors.ENDC}")


def print_success(message: str):
    """Print a success message"""
    print(f"{Colors.OKGREEN}✓ {message}{Colors.ENDC}")


def print_error(message: str):
    """Print an error message"""
    print(f"{Colors.FAIL}✗ {message}{Colors.ENDC}")


def print_warning(message: str):
    """Print a warning message"""
    print(f"{Colors.WARNING}⚠ {message}{Colors.ENDC}")


def print_info(message: str):
    """Print an info message"""
    print(f"{Colors.OKCYAN}ℹ {message}{Colors.ENDC}")


def is_port_in_use(port: int) -> Optional[Tuple[int, str]]:
    """Check if a port is in use and return (PID, process_name) if found"""
    import platform

    system = platform.system()

    if system == "Windows":
        try:
            result = subprocess.run(['netstat', '-ano'], capture_output=True, text=True, timeout=3)

            if result.returncode == 0:
                for line in result.stdout.split('\n'):
                    if f':{port}' in line and 'LISTENING' in line:
                        parts = line.split()
                        if len(parts) >= 5:
                            try:
                                pid = int(parts[-1])
                                tasklist_result = subprocess.run(
                                    ['tasklist', '/FI', f'PID eq {pid}', '/FO', 'CSV', '/NH'],
                                    capture_output=True,
                                    text=True,
                                    timeout=2,
                                )
                                if tasklist_result.returncode == 0 and tasklist_result.stdout.strip():
                                    process_name = tasklist_result.stdout.split(',')[0].strip('"')
                                    return (pid, process_name)
                                return (pid, "Unknown")
                            except (ValueError, IndexError, subprocess.TimeoutExpired):
                                continue
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
    else:
        try:
            result = subprocess.run(
                ['lsof', '-i', f':{port}', '-sTCP:LISTEN', '-t', '-n', '-P'],
                capture_output=True,
                text=True,
                timeout=2,
            )

            if result.returncode == 0 and result.stdout.strip():
                pid_str = result.stdout.strip().split('\n')[0]
                try:
                    pid = int(pid_str)
                    proc_result = subprocess.run(
                        ['ps', '-p', str(pid), '-o', 'comm='], capture_output=True, text=True, timeout=1
                    )
                    process_name = proc_result.stdout.strip() if proc_result.returncode == 0 else "Unknown"
                    return (pid, process_name)
                except (ValueError, subprocess.TimeoutExpired):
                    pass
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

    return None


def kill_process(pid: int) -> bool:
    """Kill a process by PID"""
    import platform

    system = platform.system()

    if system == "Windows":
        try:
            result = subprocess.run(
                ['taskkill', '/F', '/PID', str(pid)], capture_output=True, text=True, timeout=5
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            print_error(f"Failed to kill process {pid}: {e}")
            return False
    else:
        try:
            os.kill(pid, signal.SIGTERM)

            for _ in range(10):
                time.sleep(0.3)
                try:
                    os.kill(pid, 0)
                except OSError:
                    return True

            try:
                os.kill(pid, signal.SIGKILL)
                return True
            except OSError:
                return False
        except OSError as e:
            if e.errno == 3:
                return True
            print_error(f"Failed to kill process {pid}: {e}")
            return False


def check_and_handle_ports(include_email: bool = False) -> bool:
    """Check if required ports are available and offer to kill processes if needed"""
    required_ports = {8007: "CUGA Agent", 8111: "CRM MCP Server", 8112: "File System MCP Server"}
    if include_email:
        required_ports.update({8000: "Email MCP Server", 1025: "Email SMTP Sink"})

    ports_in_use = {}
    for port, service in required_ports.items():
        result = is_port_in_use(port)
        if result:
            pid, process_name = result
            ports_in_use[port] = (service, pid, process_name)

    if not ports_in_use:
        return True

    print(f"\n{Colors.BOLD}{Colors.WARNING}⚠️  Port Availability Check{Colors.ENDC}\n")
    print(f"{Colors.OKCYAN}This demo requires the following ports to be available:{Colors.ENDC}")
    print(f"  • {Colors.BOLD}Port 8007{Colors.ENDC} - CUGA Agent")
    print(f"  • {Colors.BOLD}Port 8111{Colors.ENDC} - CRM MCP Server")
    print(f"  • {Colors.BOLD}Port 8112{Colors.ENDC} - File System MCP Server\n")

    print(f"{Colors.WARNING}The following ports are currently in use:{Colors.ENDC}\n")
    for port, (service, pid, process_name) in ports_in_use.items():
        print(
            f"  • {Colors.BOLD}Port {port}{Colors.ENDC} ({service}) - used by {Colors.BOLD}{process_name} (PID: {pid}){Colors.ENDC}"
        )

    print()

    choices = [
        questionary.Choice("🔧 Kill the processes and continue", value="kill"),
        questionary.Choice("❌ Cancel setup", value="cancel"),
    ]

    answer = questionary.select("What would you like to do?", choices=choices).ask()

    if answer == "cancel":
        print(f"\n{Colors.WARNING}Setup cancelled by user.{Colors.ENDC}")
        return False

    if answer == "kill":
        print(f"\n{Colors.BOLD}Stopping processes...{Colors.ENDC}")
        all_killed = True
        for port, (service, pid, process_name) in ports_in_use.items():
            if kill_process(pid):
                print_success(f"Stopped {process_name} (PID: {pid}) on port {port}")
            else:
                print_error(f"Failed to stop {process_name} (PID: {pid}) on port {port}")
                all_killed = False

        if not all_killed:
            print_warning("\nSome processes could not be stopped. You may need to stop them manually.")
            retry = questionary.confirm("Do you want to continue anyway?").ask()
            return retry

        time.sleep(1)
        return True

    return False


def check_prerequisites() -> bool:
    """Check if all prerequisites are installed"""
    print_step(1, 6, "Checking prerequisites")

    all_good = True

    # Check for Python
    try:
        python_version = sys.version_info
        if python_version.major >= 3 and python_version.minor >= 8:
            print_success(
                f"Python {python_version.major}.{python_version.minor}.{python_version.micro} installed"
            )
        else:
            print_error(f"Python 3.8+ required, found {python_version.major}.{python_version.minor}")
            all_good = False
    except Exception as e:
        print_error(f"Python check failed: {e}")
        all_good = False

    # Check for uvx
    try:
        result = subprocess.run(['uvx', '--version'], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            print_success(f"uvx installed: {result.stdout.strip()}")
        else:
            print_error("uvx not found or not working properly")
            print_info("Install with: pip install uv")
            all_good = False
    except FileNotFoundError:
        print_error("uvx not found in PATH")
        print_info("Install with: pip install uv")
        all_good = False
    except Exception as e:
        print_error(f"uvx check failed: {e}")
        all_good = False

    # Check for git
    try:
        result = subprocess.run(['git', '--version'], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            print_success(f"git installed: {result.stdout.strip()}")
        else:
            print_error("git not found or not working properly")
            all_good = False
    except FileNotFoundError:
        print_error("git not found in PATH")
        all_good = False
    except Exception as e:
        print_error(f"git check failed: {e}")
        all_good = False

    return all_good


def create_workspace(base_path: Optional[str] = None) -> Path:
    """Create the workspace directory and return its absolute path"""
    print_step(2, 6, "Setting up workspace")

    if base_path:
        workspace = Path(base_path).resolve()
    else:
        workspace = Path.cwd() / "cuga_workspace"
        workspace = workspace.resolve()

    try:
        workspace.mkdir(parents=True, exist_ok=True)
        print_success(f"Workspace created at: {Colors.BOLD}{workspace}{Colors.ENDC}")
        return workspace
    except Exception as e:
        print_error(f"Failed to create workspace: {e}")
        sys.exit(1)


def create_contacts_file(workspace: Path):
    """Create the contacts.txt file with sample emails"""
    print_step(3, 6, "Creating contacts file")

    contacts_content = """sarah.bell@gammadeltainc.partners.org
sharon.jimenez@upsiloncorp.innovation.org
ruth.ross@sigmasystems.operations.com
dorothy.richardson@nextgencorp.gmail.com
james.richardson@technovate.com
michael.torres@pinnacle-solutions.net
emma.larsson@nexus-digital.co"""

    contacts_file = workspace / "contacts.txt"
    try:
        contacts_file.write_text(contacts_content + "\n")
        print_success("contacts.txt created with 7 sample email addresses")
        print_info(f"Location: {contacts_file}")
    except Exception as e:
        print_error(f"Failed to create contacts file: {e}")
        sys.exit(1)


def start_filesystem_server(
    workspace: Path, no_cache: bool = False, local: bool = False, source_dir: Path = None
) -> subprocess.Popen:
    """Start the File System MCP server"""
    print_step(4, 6, "Starting File System MCP Server")

    workspace_str = str(workspace)
    if local:
        demo_base = source_dir or Path(__file__).parent.parent  # Go up to demo_apps directory
        filesystem_path = demo_base / "file_system"
        cmd = [
            'uv',
            'run',
            '--project',
            str(filesystem_path),
        ]
        if no_cache:
            cmd.append('--no-cache')
        cmd.extend(
            [
                'python',
                str(filesystem_path / "main.py"),
                workspace_str,
            ]
        )
    else:
        cmd = [
            'uvx',
        ]
        if no_cache:
            cmd.append('--no-cache')
        cmd.extend(
            [
                '--from',
                'git+https://github.com/cuga-project/cuga-agent.git#subdirectory=docs/examples/demo_apps/file_system',
                'filesystem-server',
                workspace_str,
            ]
        )

    try:
        print_info(f"Command: {' '.join(cmd)}")
        print_info(f"Workspace path: {Colors.BOLD}{workspace_str}{Colors.ENDC}")

        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)

        # Give it a moment to start
        time.sleep(3)

        if proc.poll() is None:
            running_processes.append(proc)
            print_success("File System server started successfully")
            print_info(f"Available at: {Colors.BOLD}http://localhost:8112/sse{Colors.ENDC}")
            return proc
        else:
            stderr = proc.stderr.read() if proc.stderr else "No error output"
            print_error(f"File System server failed to start: {stderr}")
            sys.exit(1)
    except Exception as e:
        print_error(f"Failed to start File System server: {e}")
        sys.exit(1)


def start_crm_server(
    no_cache: bool = False, local: bool = False, source_dir: Path = None
) -> subprocess.Popen:
    """Start the CRM MCP server"""
    print_step(5, 6, "Starting CRM MCP Server")

    if local:
        demo_base = source_dir or Path(__file__).parent.parent  # Go up to demo_apps directory
        crm_path = demo_base / "crm"
        cmd = [
            'uv',
            'run',
            '--project',
            str(crm_path),
        ]
        if no_cache:
            cmd.append('--no-cache')
        cmd.extend(
            [
                'python',
                '-m',
                'crm_api.run_all',
            ]
        )
    else:
        cmd = [
            'uvx',
        ]
        if no_cache:
            cmd.append('--no-cache')
        cmd.extend(
            [
                '--from',
                'git+https://github.com/cuga-project/cuga-agent.git#subdirectory=docs/examples/demo_apps/crm',
                'crm',
            ]
        )

    try:
        print_info(f"Command: {' '.join(cmd)}")

        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)

        # Give it a moment to start
        time.sleep(3)

        if proc.poll() is None:
            running_processes.append(proc)
            print_success("CRM server started successfully")
            print_info(f"Available at: {Colors.BOLD}http://localhost:8111/sse{Colors.ENDC}")
            return proc
        else:
            stderr = proc.stderr.read() if proc.stderr else "No error output"
            print_error(f"CRM server failed to start: {stderr}")
            sys.exit(1)
    except Exception as e:
        print_error(f"Failed to start CRM server: {e}")
        sys.exit(1)


def start_email_sink(
    no_cache: bool = False, local: bool = False, source_dir: Path = None
) -> subprocess.Popen:
    """Start the Email SMTP Sink"""
    print_info("Starting Email SMTP Sink")

    if local:
        demo_base = source_dir or Path(__file__).parent.parent  # Go up to demo_apps directory
        email_sink_path = demo_base / "email_mcp" / "mail_sink"
        cmd = [
            'uv',
            'run',
            '--project',
            str(email_sink_path),
        ]
        if no_cache:
            cmd.append('--no-cache')
        cmd.extend(
            [
                'python',
                str(email_sink_path / "server.py"),
            ]
        )
    else:
        cmd = [
            'uvx',
        ]
        if no_cache:
            cmd.append('--no-cache')
        cmd.extend(
            [
                '--from',
                'git+https://github.com/cuga-project/cuga-agent.git#subdirectory=docs/examples/demo_apps/email_mcp/mail_sink',
                'email_sink',
            ]
        )

    try:
        print_info(f"Command: {' '.join(cmd)}")
        print_info(f"Current working directory: {os.getcwd()}")

        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)

        # Give it a moment to start
        time.sleep(2)

        if proc.poll() is None:
            running_processes.append(proc)
            print_success("Email SMTP sink started successfully")
            print_info(f"Available at: {Colors.BOLD}localhost:1025{Colors.ENDC}")
            return proc
        else:
            stderr = proc.stderr.read() if proc.stderr else "No error output"
            print_error(f"Email SMTP sink failed to start: {stderr}")
            sys.exit(1)
    except Exception as e:
        print_error(f"Failed to start Email SMTP sink: {e}")
        sys.exit(1)


def start_email_server(
    no_cache: bool = False, local: bool = False, source_dir: Path = None
) -> subprocess.Popen:
    """Start the Email MCP server"""
    print_info("Starting Email MCP Server")

    if local:
        demo_base = source_dir or Path(__file__).parent.parent  # Go up to demo_apps directory
        email_server_path = demo_base / "email_mcp" / "mcp_server"
        cmd = [
            'uv',
            'run',
            '--project',
            str(email_server_path),
        ]
        if no_cache:
            cmd.append('--no-cache')
        cmd.extend(
            [
                'python',
                str(email_server_path / "server.py"),
            ]
        )
    else:
        cmd = [
            'uvx',
        ]
        if no_cache:
            cmd.append('--no-cache')
        cmd.extend(
            [
                '--from',
                'git+https://github.com/cuga-project/cuga-agent.git#subdirectory=docs/examples/demo_apps/email_mcp/mcp_server',
                'email_mcp',
            ]
        )

    try:
        print_info(f"Command: {' '.join(cmd)}")
        print_info(f"Current working directory: {os.getcwd()}")

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            cwd=os.path.dirname(os.path.abspath(__file__)),
        )

        # Give it a moment to start
        time.sleep(2)

        if proc.poll() is None:
            running_processes.append(proc)
            print_success("Email MCP server started successfully")
            print_info(f"Available at: {Colors.BOLD}http://localhost:8000/sse{Colors.ENDC}")
            return proc
        else:
            stderr = proc.stderr.read() if proc.stderr else "No error output"
            print_error(f"Email MCP server failed to start: {stderr}")
            sys.exit(1)
    except Exception as e:
        print_error(f"Failed to start Email MCP server: {e}")
        sys.exit(1)


def print_configuration_info(workspace: Path, include_email: bool = False):
    """Print configuration information for LangFlow"""
    print_step(6, 6, "Configuration Complete!")

    policy = f"""## Plan
For the filesystem application: write or read files only from `{workspace}`"""
    if include_email:
        policy += """
For the email application: send emails only using the local SMTP sink"""

    summary = f"""
{Colors.BOLD}{Colors.OKGREEN}
╔═══════════════════════════════════════════════════════════╗
║                                                           ║
║            ✓  Setup Complete!                            ║
║                                                           ║
╚═══════════════════════════════════════════════════════════╝
{Colors.ENDC}

{Colors.BOLD}📁 Workspace:{Colors.ENDC}
   {Colors.OKCYAN}{workspace}{Colors.ENDC}

{Colors.BOLD}🌐 Running Services:{Colors.ENDC}
   {Colors.OKGREEN}✓{Colors.ENDC} File System MCP: {Colors.BOLD}http://localhost:8112/sse{Colors.ENDC}
   {Colors.OKGREEN}✓{Colors.ENDC} CRM MCP:         {Colors.BOLD}http://localhost:8111/sse{Colors.ENDC}"""

    if include_email:
        summary += f"""
   {Colors.OKGREEN}✓{Colors.ENDC} Email MCP:        {Colors.BOLD}http://localhost:8000/sse{Colors.ENDC}
   {Colors.OKGREEN}✓{Colors.ENDC} Email SMTP Sink:  {Colors.BOLD}localhost:1025{Colors.ENDC}"""

    summary += f"""

{Colors.BOLD}📋 Files Created:{Colors.ENDC}
   {Colors.OKGREEN}✓{Colors.ENDC} {workspace}/contacts.txt (7 sample contacts)

{Colors.BOLD}🔧 LangFlow Configuration:{Colors.ENDC}

   {Colors.UNDERLINE}In your CUGA component 'policies' field, add:{Colors.ENDC}

   {Colors.OKCYAN}{policy}{Colors.ENDC}

   {Colors.UNDERLINE}Connect these MCP servers:{Colors.ENDC}
   • File System: http://localhost:8112/sse
   • CRM:         http://localhost:8111/sse"""

    if include_email:
        summary += """
   • Email:       http://localhost:8000/sse"""

    summary += f"""
   • Gmail:       Built-in LangFlow component

{Colors.BOLD}🎯 Demo Task:{Colors.ENDC}
   {Colors.OKBLUE}Given list of email in the file contacts.txt, Filter those who
   exists in the crm application, and retrieve their name, and
   associated account name, then send an email to example@gmail.com
   with the result{Colors.ENDC}

{Colors.BOLD}⚡ Quick Actions:{Colors.ENDC}
   • View contacts:  {Colors.OKCYAN}cat {workspace}/contacts.txt{Colors.ENDC}
   • Check status:   {Colors.OKCYAN}curl http://localhost:8112/sse{Colors.ENDC}
   • Stop servers:   {Colors.WARNING}Press Ctrl+C{Colors.ENDC}

{Colors.BOLD}{Colors.OKGREEN}🚀 Ready to run your demo in LangFlow!{Colors.ENDC}
"""

    print(summary)


def monitor_servers():
    """Monitor running servers and keep them alive"""
    print(f"\n{Colors.BOLD}{Colors.OKCYAN}🔄 Servers are running... Press Ctrl+C to stop{Colors.ENDC}\n")

    try:
        while True:
            time.sleep(1)
            # Check if any process has died
            for proc in running_processes:
                if proc.poll() is not None:
                    print_error(f"A server process has stopped unexpectedly (exit code: {proc.returncode})")
                    if proc.stderr:
                        stderr = proc.stderr.read()
                        if stderr:
                            print_error(f"Error output: {stderr}")
                    cleanup()
                    sys.exit(1)

                # 🚨 CRITICAL: Drain pipes to prevent buffer overflow and server hangs
                # Servers like CRM can produce lots of logs that fill up pipe buffers
                try:
                    # Drain stdout non-blockingly
                    if proc.stdout:
                        import select

                        # Use select to check if data is available without blocking
                        if hasattr(select, 'select'):
                            # Unix-like systems
                            ready, _, _ = select.select([proc.stdout], [], [], 0)
                            while ready:
                                line = proc.stdout.readline()
                                if not line:
                                    break
                                # Silently discard - servers are running in background
                                ready, _, _ = select.select([proc.stdout], [], [], 0)
                        else:
                            # Windows fallback - read available data
                            while True:
                                # Peek to see if data is available
                                if hasattr(proc.stdout, 'peek'):
                                    peek_data = proc.stdout.peek(1)
                                    if not peek_data:
                                        break
                                # Read line by line
                                line = proc.stdout.readline()
                                if not line:
                                    break
                                # Silently discard - servers are running in background
                except (OSError, ValueError, AttributeError):
                    # Ignore pipe errors - non-critical for monitoring
                    pass

                try:
                    # Drain stderr non-blockingly
                    if proc.stderr:
                        import select

                        # Use select to check if data is available without blocking
                        if hasattr(select, 'select'):
                            # Unix-like systems
                            ready, _, _ = select.select([proc.stderr], [], [], 0)
                            while ready:
                                line = proc.stderr.readline()
                                if not line:
                                    break
                                # Silently discard - servers are running in background
                                ready, _, _ = select.select([proc.stderr], [], [], 0)
                        else:
                            # Windows fallback
                            while True:
                                if hasattr(proc.stderr, 'peek'):
                                    peek_data = proc.stderr.peek(1)
                                    if not peek_data:
                                        break
                                line = proc.stderr.readline()
                                if not line:
                                    break
                                # Silently discard - servers are running in background
                except (OSError, ValueError, AttributeError):
                    # Ignore pipe errors - non-critical for monitoring
                    pass
    except KeyboardInterrupt:
        pass


def main():
    """Main entry point"""
    print_header()

    # Parse command line arguments
    parser = argparse.ArgumentParser(description='CUGA Demo Setup CLI')
    parser.add_argument('--email', action='store_true', help='Include email MCP server and SMTP sink')
    parser.add_argument(
        '--cache', action='store_true', help='Enable uv caching (default: disabled for fresh installations)'
    )
    parser.add_argument('--local', action='store_true', help='Use local demo apps instead of git installs')
    parser.add_argument(
        '--cuga_workspace',
        default=None,
        help='Path to cuga workspace; when set, configures policy env so all file operations use this dir',
    )
    parser.add_argument(
        'workspace_path', nargs='?', default=None, help='Path to workspace directory (optional)'
    )

    args = parser.parse_args()

    # Check environment variable for local mode
    if os.getenv('CUGA_LOCAL', '').lower() in ('1', 'true', 'yes'):
        args.local = True

    # Set the source directory for local mode
    if args.local:
        # When running from uvx temp directory, we need the original source path
        if os.getenv('CUGA_SOURCE_DIR'):
            args.source_dir = Path(os.getenv('CUGA_SOURCE_DIR'))
        else:
            # Try to find it relative to current working directory
            potential_source = Path.cwd() / "docs" / "examples" / "demo_apps"
            if potential_source.exists():
                args.source_dir = potential_source
            else:
                # Fallback to relative path from script location
                args.source_dir = Path(__file__).parent.parent
        print_info(f"Using local demo apps from: {args.source_dir}")
    else:
        print_info("Using remote demo apps from git repository")

    # Check prerequisites
    if not check_prerequisites():
        print_error("\n❌ Prerequisites check failed. Please install missing requirements.")
        sys.exit(1)

    # Check port availability
    if not check_and_handle_ports(include_email=args.email):
        sys.exit(1)

    workspace_path = args.cuga_workspace or args.workspace_path

    workspace = create_workspace(workspace_path)

    policy = f"## Plan\nFor the filesystem application: write or read files only from `{workspace}`"
    if args.email:
        policy += "\nFor the email application: send emails only using the local SMTP sink"
    os.environ["CUGA_POLICIES_CONTENT"] = policy
    os.environ["CUGA_LOAD_POLICIES"] = "true"

    # Create contacts file
    create_contacts_file(workspace)

    # Start servers
    start_filesystem_server(
        workspace, no_cache=not args.cache, local=args.local, source_dir=getattr(args, 'source_dir', None)
    )
    start_crm_server(no_cache=not args.cache, local=args.local, source_dir=getattr(args, 'source_dir', None))

    # Start email servers if requested
    if args.email:
        start_email_sink(
            no_cache=not args.cache, local=args.local, source_dir=getattr(args, 'source_dir', None)
        )
        start_email_server(
            no_cache=not args.cache, local=args.local, source_dir=getattr(args, 'source_dir', None)
        )

    # Print configuration
    print_configuration_info(workspace, include_email=args.email)

    # Monitor servers
    monitor_servers()


if __name__ == "__main__":
    main()

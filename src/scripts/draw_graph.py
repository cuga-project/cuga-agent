import asyncio
import json
import os
import webbrowser
import http.server
import socketserver
import time

from cuga.backend.cuga_graph.entry_graph import CugaEntryGraph as DynamicAgentGraph
from cuga.config import PACKAGE_ROOT
from langchain_core.runnables.graph import Graph


async def _main():
    agent = DynamicAgentGraph(None)
    await agent.build_graph()
    graph: Graph = agent.graph.get_graph(xray=True)

    # Write graph data to JSON file
    json_path = os.path.join(PACKAGE_ROOT, "..", "scripts", "graph_visualization", "graph.json")
    with open(json_path, "w") as f:
        f.write(json.dumps(graph.to_json()))

    # Start a local HTTP server to serve the files
    graph_viz_dir = os.path.join(PACKAGE_ROOT, "..", "scripts", "graph_visualization")

    from cuga.config import settings

    # Find an available port
    port = settings.server_ports.graph_visualization
    while port < 8090:  # Try ports 8080-8089
        try:
            test_socket = socketserver.TCPServer(("", port), http.server.SimpleHTTPRequestHandler)
            test_socket.server_close()
            break
        except OSError:
            port += 1
    else:
        print(
            f"Could not find an available port. Using port {settings.server_ports.graph_visualization} anyway..."
        )
        port = settings.server_ports.graph_visualization

    # Change to the graph visualization directory
    os.chdir(graph_viz_dir)

    # Start HTTP server
    handler = http.server.SimpleHTTPRequestHandler
    with socketserver.TCPServer(("", port), handler) as httpd:
        print(f"Graph data saved to: {json_path}")
        print(f"Starting HTTP server on port {port}...")
        print(f"Opening graph visualization: http://localhost:{port}/graph.html")

        # Open the HTML file in the default web browser
        webbrowser.open(f"http://localhost:{port}/graph.html")

        # Keep the server running for 60 seconds to allow viewing
        print("Server will run for 60 seconds. Press Ctrl+C to stop early.")
        try:
            # Set a timeout for the server
            httpd.timeout = 1
            start_time = time.time()
            while time.time() - start_time < 60:  # Run for 60 seconds
                httpd.handle_request()
        except KeyboardInterrupt:
            print("\nShutting down server...")
        finally:
            print("Server stopped.")


def main():
    asyncio.run(_main())


if __name__ == '__main__':
    main()

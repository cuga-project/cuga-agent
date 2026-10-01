# API Registry

A FastAPI server to register and query API/Application metadata.

---

## Features

- **List Applications** (`GET /applications`)  
- **List APIs for an Application** (`GET /applications/{app_name}/apis?include_response_schema={bool}`)  
- **List All APIs** (`GET /apis?include_response_schema={bool}`)  
- **Call an MCP-registered Function** (`POST /functions/call`)  

---

## Configuration

- **MCP Servers**  
  Define your MCP servers in `src/cuga/backend/tools_env/registry/config/mcp_servers.yaml`, or point the `MCP_SERVERS_FILE` environment variable at your own YAML file.  
- **Authentication**  
  `AppWorldAuthManager` handles per‑app tokens for AppWorld;
  - In order to support more authentication types just inherit from the `BaseAuthManager` class

---

## Running the Server
For dev mode (working on the server)

```bash
uv run python -m cuga.backend.tools_env.registry.registry.api_registry_server
```

By default, the server listens on `http://127.0.0.1:8001`.  
API docs are available at `http://127.0.0.1:8001/docs`.

You can also run the server using the scripts as described in the main README

---

## API Endpoints

### 1. List Applications

```
GET /applications
```

---

### 2. List APIs for an Application

```
GET /applications/{app_name}/apis?include_response_schema=false
```

- **app_name**: Name of the registered app  
- **include_response_schema** (optional, default `false`): Include detailed response schemas

---

### 3. List All APIs

```
GET /apis?include_response_schema=false
```

---

### 4. Call an MCP Function

```
POST /functions/call
Content-Type: application/json

{
  "app_name": "weather_service",
  "function_name": "getCurrentWeather",
  "args": { "city": "Tel Aviv" }
}
```

**Response**  
- Parses JSON if the function returns valid JSON text, otherwise returns raw content.


---

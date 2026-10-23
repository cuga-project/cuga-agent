
# **CUGA Evaluation**  

An evaluation framework for **CUGA**, enabling you to **test your APIs** against structured test cases with detailed scoring and reporting.

---

## **Features**
- ✅ Validate **API responses** against expected outputs  
- ✅ Score **keywords**, **tool calls**, and **response similarity**  
- ✅ Generate **JSON** and **CSV** reports for easy analysis  

---

## **Test File Schema**

Your test file must be a **JSON** following this structure:

```json
{
  "name": "name for the test suite",
  "title": "TestCases",
  "type": "object",
  "properties": {
    "test_cases": {
      "type": "array",
      "items": {
        "$ref": "#/definitions/TestCase"
      }
    }
  },
  "required": ["test_cases"],
  "definitions": {
    "ToolCall": {
      "type": "object",
      "properties": {
        "name": { "type": "string" },
        "args": { "type": "object" }
      },
      "required": ["name", "arguments"]
    },
    "ExpectedOutput": {
      "type": "object",
      "properties": {
        "response": { "type": "string" },
        "keywords": {
          "type": "array",
          "items": { "type": "string" }
        },
        "tool_calls": {
          "type": "array",
          "items": { "$ref": "#/definitions/ToolCall" }
        }
      },
      "required": ["response", "keywords", "tool_calls"]
    },
    "TestCase": {
      "type": "object",
      "properties": {
        "name": { "type": "string" },
        "description": { "type": "string" },
        "intent": { "type": "string" },
        "expected_output": { "$ref": "#/definitions/ExpectedOutput" }
      },
      "required": ["name", "description", "intent", "expected_output"]
    }
  }
}
```

---

### **Schema Overview**
| Entity           | Description                                  |
|------------------|----------------------------------------------|
| **ToolCall**     | Represents a tool invocation with `name` and `args`. |
| **ExpectedOutput** | Expected response, keywords, and tool calls. |
| **TestCase**     | Defines a single test case with intent and expected output. |

---

## **Output Format**

The evaluation generates **two files**:  
- `results.json`  
- `results.csv`  

### **JSON Structure**
```json
{
  "summary": {
    "total_tests": "...",
    "avg_keyword_score": "...",
    "avg_tool_call_score": "...",
    "avg_response_score": "..."
  },
  "results": [
    {
      "index": "...",
      "test_name": "...",
      "score": {
        "keyword_score": "...",
        "tool_call_score": "...",
        "response_score": "...",
        "response_scoring_type": "..."
      },
      "details": {
        "missing_keywords": "...",
        "expected_keywords": "...",
        "expected_tool_calls": "...",
        "tool_call_mismatches": "...",
        "response_expected": "...",
        "response_actual": "...",
        "response_scoring_type": "..."
      }
    }
  ]
}
```

## **Langfuse Tracing (Optional)**
### Setup Langfuse
In a different folder (not under Cuga) run
```bash
# Get a copy of the latest Langfuse repository
git clone https://github.com/langfuse/langfuse.git
cd langfuse

# Run the langfuse docker compose
docker compose up
```

### Get API Keys

1. Access the Langfuse UI: Open a web browser and navigate to the URL where your self-hosted Langfuse instance is running (e.g., http://localhost:3000 if running locally with default ports).
2. Log in: Sign in with the user account you created during the initial setup or create a new account.
3. Navigate to Project Settings:
    Click on the "Project" menu (usually in the sidebar or top navigation).
    Select "Settings".
4. View API Keys:
    In the settings area, you will find a section for API keys.
    You can view or regenerate your LANGFUSE_PUBLIC_KEY (username) and LANGFUSE_SECRET_KEY (password) there.
    The secret key is hidden by default; you may need to click an eye icon or a specific button to reveal and copy it.
5. Add the API keys and host to your .env file
```.dotenv
LANGFUSE_SECRET_KEY="your-secret-key"
LANGFUSE_PUBLIC_KEY="your-public-key"
LANGFUSE_HOST="http://localhost:3000"
```

### Update settings
Then in `vendor/cuga-agent/src/cuga/settings.toml` update
```
langfuse_tracing = true
```



---

## **Quick Start Example**

Run the evaluation on our default `digital_sales` API using our example test case.

This is the example input JSON:
```json
{
  "name": "digital-sales",
  "test_cases": [
    {
      "name": "test_get_top_account",
      "description": "gets the top account by revenue",
      "intent": "get my top account by revenue",
      "expected_output": {
        "response": "**Top Account by Revenue** - **Name:** Andromeda Inc. - **Revenue:** $9,700,000 - **Account ID:** acc_49",
        "keywords": ["Andromeda Inc.", "9,700,000"],
        "tool_calls": [
                  {
          "name": "digital_sales_get_my_accounts_my_accounts_get",
          "args": {
          }
        }
        ]
      }
    }
  ]
}

```

First set `tracker_enabled = true` in the `settings.toml`

Now you can start running the example.

1. **Update API URL** in [mcp_servers.yaml](../backend/tools_env/registry/config/mcp_servers.yaml):  
   ```yaml
   url: http://localhost:8000/openapi.json
   ```
2. **Start the API server**:  
   ```bash
   uv run digital_sales_openapi
   ```
3. **Run evaluation**:  
   ```bash
   cuga evaluate docs/examples/evaluation/input_example.json
   ```

You’ll get `results.json` and `results.csv` in the project root.

---

## **Usage**
```bash
cuga evaluate -t <test file path> -r <results file path>
```

Steps:
1. Update [mcp_servers.yaml](../backend/tools_env/registry/config/mcp_servers.yaml) with your APIs or create a new YAML file and run 
```shell
export MCP_SERVERS_FILE=<location>
```
2. Create a test file following the schema.
3. Run the evaluation command above.

---

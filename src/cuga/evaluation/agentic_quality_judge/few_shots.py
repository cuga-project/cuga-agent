"""Few-shot examples for the agentic_quality judges -- the one part of
scoring.py's prompts that's dataset/tool-specific. Swap these two constants
to re-target a different tool registry or domain (these assume the
digital_sales_* tools); nothing else in scoring.py needs to change.
"""

TOOL_SELECTION_QUALITY_EXAMPLES = """### Example 1 — single correct call → Good
User Goal: Find all emails from alice@example.com in the last 7 days.
Available Tools: search_emails(sender, date_range); get_email_by_id(email_id); list_contacts()
Trajectory:
Step 1: search_emails(sender="alice@example.com", date_range="last_7_days")
{"label": "Good", "reason": "The single tool call selected the right tool with correctly grounded arguments.", "steps": [{"step": 1, "tool": "search_emails", "verdict": "Correct", "reason": "Most appropriate tool; both arguments taken directly from the user's request."}]}

### Example 2 — wrong tool → Poor
User Goal: Delete the calendar event with ID EVT-991.
Available Tools: delete_calendar_event(event_id); update_calendar_event(event_id, fields); get_calendar_event(event_id)
Trajectory:
Step 1: update_calendar_event(event_id="EVT-991", fields={"status": "cancelled"})
{"label": "Poor", "reason": "Step 1 chose the wrong tool: update_calendar_event instead of the available delete_calendar_event.", "steps": [{"step": 1, "tool": "update_calendar_event", "verdict": "Incorrect", "reason": "delete_calendar_event was available and purpose-built; setting status to cancelled is not a deletion."}]}

### Example 3 — hallucinated argument + bad sequencing → Poor
User Goal: Get the details of my most recent order.
Available Tools: list_orders(user_id); get_order_details(order_id); get_user_profile()
Trajectory:
Step 1: get_order_details(order_id="ORD-1234")
{"label": "Poor", "reason": "Step 1 used a hallucinated order_id ('ORD-1234') and skipped the prerequisite lookup steps.", "steps": [{"step": 1, "tool": "get_order_details", "verdict": "Incorrect", "reason": "order_id 'ORD-1234' is hallucinated and prerequisite steps to obtain it were skipped."}]}

### Example 4 — multi-step, one fails → Poor
User Goal: Look up the account owner's job titles, then get contacts for that account filtered to those titles.
Available Tools: digital_sales_my_accounts(); digital_sales_accounts(account_id); digital_sales_contacts(account_id, job_title)
Trajectory:
Step 1: digital_sales_my_accounts() → {"accounts": [{"id": "acc_12", "name": "Acme Corp"}]}
Step 2: digital_sales_contacts(account_id="acc_99", job_title="VP Sales")
{"label": "Poor", "reason": "Step 2 used a hallucinated account_id ('acc_99') instead of 'acc_12' returned by step 1, and skipped the required digital_sales_accounts lookup.", "steps": [{"step": 1, "tool": "digital_sales_my_accounts", "verdict": "Correct", "reason": "Correct tool for listing the user's accounts; no arguments required."}, {"step": 2, "tool": "digital_sales_contacts", "verdict": "Incorrect", "reason": "account_id 'acc_99' is hallucinated; step 1 returned 'acc_12', which should have been used, and job_title was not grounded via digital_sales_accounts."}]}

### Example 5 — multi-step, all correct → Good
User Goal: Find third-party accounts for the "tech transformation" campaign, then list contacts for the first one.
Available Tools: digital_sales_third_party_accounts(campaign_name); digital_sales_contacts(account_id, job_title)
Trajectory:
Step 1: digital_sales_third_party_accounts(campaign_name="tech transformation") → {"accounts": [{"id": "acc_44", "name": "Globex"}]}
Step 2: digital_sales_contacts(account_id="acc_44")
{"label": "Good", "reason": "Both steps selected the right tools with grounded arguments in the correct order.", "steps": [{"step": 1, "tool": "digital_sales_third_party_accounts", "verdict": "Correct", "reason": "Correct tool for the campaign filter; campaign_name taken directly from the user's request."}, {"step": 2, "tool": "digital_sales_contacts", "verdict": "Correct", "reason": "account_id grounded in step 1's result; correctly sequenced after step 1."}]}"""

ACTION_ADVANCEMENT_EXAMPLES = """### Example 1 — wrong fact → Did Not Advance
User: What is the capital of Australia?
Tool Outputs: None
Assistant: The capital of Australia is Sydney.
{"label": "Did Not Advance", "reason": "The response states an incorrect fact."}

### Example 2 — necessary clarification → Partially Advanced
User: Can you find contacts for my account?
Tool Outputs: {"accounts": [{"id": "acc_12"}, {"id": "acc_31"}]}
Assistant: You have two accounts in your territory — which one would you like contacts for?
{"label": "Partially Advanced", "reason": "There are multiple matching accounts, so asking which one is necessary and useful."}

### Example 3 — accurate, tool-consistent, complete → Advanced
User: What state is Acme Corp in?
Tool Outputs: {"accounts": [{"id": "acc_12", "name": "Acme Corp", "state": "CA"}]}
Assistant: Acme Corp is in California.
{"label": "Advanced", "reason": "The response directly and accurately answers using the tool output."}

### Example 4 — contradicts tool output → Did Not Advance
User: What state is Acme Corp in?
Tool Outputs: {"accounts": [{"id": "acc_12", "name": "Acme Corp", "state": "CA"}]}
Assistant: Acme Corp is based in New York.
{"label": "Did Not Advance", "reason": "The response contradicts the tool output, which shows the account is in CA."}

### Example 5 — partial answer → Partially Advanced
User: Give me the job titles and email addresses of contacts at Acme Corp.
Tool Outputs: {"job_titles": [{"title": "VP Sales"}, {"title": "Buyer"}]}
Assistant: The job titles at Acme Corp are VP Sales and Buyer.
{"label": "Partially Advanced", "reason": "The job titles were answered correctly but the requested email addresses were not provided."}"""

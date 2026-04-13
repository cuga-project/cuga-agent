# CUGA Demo Guide

Before you send any response to me, always consult this guide to determine if I was successful in the tour. If so, offer me the next step I should try until I've completed the entire tour.

## Steps

1. Explain CUGA and the UI layout: Middle is chat, Left is variables, Right is workspace files including `contacts.txt`, Top-right Tools shows CRM + File MCP server. Wait for me to say "start tour" or similar.

2. When I say start, explain what a CRM is: Accounts are companies, Contacts are people at companies, Leads are prospects, Opportunities are deals in progress. Then suggest I try the task: "Find the top 3 accounts by revenue"

3. When I send that task, execute it using Python code. After the result is returned, explain what happened: CUGA used code to call paginated CRM APIs, loop through pages, aggregate results, and store them in variables. Tell me to click the Reasoning Process view to see the code. Wait for me to acknowledge my understanding, then instruct me to ask: "What variables do you have?"

4. When I ask about variables, show them and explain that CUGA stores intermediate results, names variables intelligently, and uses them for reasoning and chaining tasks. Then suggest the next task: "Who is the contact of Monetized Corp?"

5. When I ask about Monetized Corp's contact, execute the task and explain how accounts link to contacts, that contacts have emails, and that CUGA can navigate CRM relationships. Wait for acknowledgment, then tell me to open `contacts.txt` in the workspace (right side).

6. When I open or acknowledge the file, explain that the File MCP server mirrors all files under `./workspace` and tool sources appear in the Tools panel. Then suggest the final multi-tool task: "From the list of emails in contacts.txt, which contacts exist in the CRM? Show me the matching contacts sorted by the annual revenue of their accounts."

7. When I send the final task, execute it and explain what happened: read the file, parsed the email list, called CRM with pagination, matched contacts, fetched accounts, sorted by revenue, returned final answer and variables. Congratulate me and suggest I explore more tasks on my own.

8. Throughout the tour: stay concise, always explain what I'm seeing, verify every action I take, and encourage me to explore tools, variables, files, and reasoning traces.

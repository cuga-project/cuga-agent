---
name: data_analyst
description: "Analyzes data and returns summary statistics"
tools: []
skill_tools: []
tool_definitions:
  - name: summarise_list
    description: "Summarise a list of numbers"
    module: tests.fixtures.agents.data_analyst.tools
    function: summarise_list_async
model: null
thread_id_prefix: data_analyst
max_steps: 4
inherit_parent_tools: false
---
You are a data analyst. Use summarise_list to analyze numeric data.

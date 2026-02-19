# Memory for CUGA

This document explains how to enable the use of the memory feature in CUGA.

## 🎯 Overview

CUGA execution can be enhanced by enabling episodic memory, which introduces the ability to levearge previously identified insights and relevant experiences when generating the final answer.
Some key features include:

### 1. Kaizen-Based Memory Component

- Extract and store entities in Milvus via Kaizen.
- In-process memory access through `cuga.backend.memory.memory.Memory`.
- Entity types used by CUGA:
  - `fact`
  - `tip`
  - `run`
  - `run_step`
- Database dependencies:
  - Milvus (entity vectors + metadata)
  - SQLite (Kaizen namespace catalog)

### 2. Integration with cuga-agent

- Adds `enable_memory` flag to control memory features.
- No standalone memory sidecar service required
- Uses retrieved memory in agents:
  - Task analyzer
  - Task decomposition
  - API shortlist
  - Code agent
  - API Code Planner
    (Will expand to other agents with more experiments)
 - Extracts tips at the end of a run in activity tracker:
		self.memory.end_run(namespace_id="memory", run_id=self.experiment_folder)



## 🚀  Quick Start

0. Ensure local Kaizen checkout exists at `./kaizen`:
   `git clone https://github.com/AgentToolkit/kaizen.git ./kaizen`
1. Set `enable_memory=true` in `settings.toml`
2. Start CUGA normally:
	`cuga start demo`
Memory is initialized in-process from the same runtime.

### Configuration

- Kaizen DB paths default to CUGA DB directory:
  - `KAIZEN_URI=<CUGA_DBS_DIR>/entities.milvus.db`
  - `KAIZEN_SQLITE_URI=<CUGA_DBS_DIR>/entities.sqlite.db`
- Configure Kaizen LLMs in `src/cuga/configurations/models/settings.<provider>.toml`:
  - `[memory.kaizen.fact_extraction.model]`
  - `[memory.kaizen.tips.model]`
  - `[memory.kaizen.conflict_resolution.model]`



## 📁 Prompt Location
```
cuga/
└── backend/memory/utils/
                              └── prompts.py      # CUGA step prompt mapping hooks

kaizen/
└── kaizen/llm/
               ├── tips/
               └── fact_extraction/
```            
            
## 🔧 How It Works
The use cases which motivate the need for memory for CUGA include:
1. Generate insights from successful/failed trajectories 
- During execution,  `memory.add_step` captures  summary of step output and any relevant information
- The Activity Tracker activates `memory.end_run` at the final step of the FinalAnswerAgent execution
- A background process is triggered, to `analyze_run` of the stored trajectory
- Analysis invokes LLM with prompt tailored to  `extract_cuga_tips_from_data`, resulting in identification, extraction and classification of tips per Cuga sub-agent
- `create_and_store_fact `is invoked for each tip, along with relevant metadata  

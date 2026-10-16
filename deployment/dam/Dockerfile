# DAM custom image for CUGA. Build from the repository root:
#   docker build --platform linux/amd64 -f deployment/dam/Dockerfile -t cuga-agent-dam .
# Contract: https://pages.github.ibm.com/dam-agents/docs/guides/custom-image/
ARG DAM_BASE_VERSION=0.2.21
FROM quay.io/dam-agents/claude-code:${DAM_BASE_VERSION}

USER root

# CUGA and its locked dependencies in /opt/venv (first on the agent's PATH).
# Built from the checked-out source with uv.lock; the `acp` extra provides `cuga-acp`.
# The image's WORKDIR is kept; the build happens in /tmp/cuga-build.
COPY pyproject.toml uv.lock README.md /tmp/cuga-build/
COPY src/ /tmp/cuga-build/src/
RUN cd /tmp/cuga-build \
    && UV_PROJECT_ENVIRONMENT=/opt/venv UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy \
        uv sync --frozen --no-dev --no-editable --extra acp --python 3.12

# Bake model weights: the agent has no direct network access at runtime.
ENV FASTEMBED_CACHE_PATH=/opt/cuga/cache/fastembed \
    DOCLING_ARTIFACTS_PATH=/opt/cuga/cache/docling \
    HF_HOME=/opt/cuga/cache/huggingface \
    SENTENCE_TRANSFORMERS_HOME=/opt/cuga/cache/sentence-transformers \
    TIKTOKEN_CACHE_DIR=/opt/cuga/cache/tiktoken \
    CUSTOM_TIKTOKEN_CACHE_DIR=/opt/cuga/cache/tiktoken \
    LITELLM_LOCAL_MODEL_COST_MAP=True
RUN cd /tmp/cuga-build \
    && /opt/venv/bin/python src/scripts/preload_models.py \
    && chmod -R a+rX /opt/cuga /opt/venv \
    && rm -rf /tmp/cuga-build

ENV HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    HF_DATASETS_OFFLINE=1 \
    CUGA_DBS_DIR=/home/agent/.cuga/dbs \
    CUGA_LOGGING_DIR=/home/agent/.cuga/logging

# Session launchers (Step 3 of the DAM guide).
COPY deployment/dam/harness-chat.sh /usr/local/bin/harness-chat
COPY deployment/dam/harness-terminal.sh /usr/local/bin/harness-terminal
RUN chmod +x /usr/local/bin/harness-chat /usr/local/bin/harness-terminal

# Seed files; copied once into the agent's home on first boot.
COPY --chown=65532:0 deployment/dam/workspace/ /app/working-dir/

USER 65532

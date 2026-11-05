# DAM's default agent image: Debian with the agent-runtime and the platform's
# own harnesses. This image adds CUGA as the one harness it runs.
ARG DAM_BASE=quay.io/dam-agents/default@sha256:c20ef03ae17b108f90a5e185320feaa192bc1334facd31f72dc4ef72f32818b6

FROM ${DAM_BASE} AS cuga-builder

USER root
WORKDIR /opt/cuga

COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/
COPY deployment/dam/requirements.txt ./dam-requirements.txt

# Build on the base image's own Python so the venv runs there unchanged. The
# exclusions are Dockerfile.ubi.slim's: no document processing or Evolve.
# Bytecode is compiled here: the image's tree is read-only to the agent, so
# without it every start compiles the thousands of modules CUGA imports again.
RUN --mount=type=cache,target=/root/.cache/uv \
    UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_COMPILE_BYTECODE=1 \
    uv sync --frozen --no-dev --no-editable --extra acp \
    --python "$(command -v python3.12)" \
    --no-install-package torch \
    --no-install-package torchvision \
    --no-install-package easyocr \
    --no-install-package docling \
    --no-install-package docling-core \
    --no-install-package docling-ibm-models \
    --no-install-package docling-parse \
    --no-install-package docling-slim \
    --no-install-package langchain-docling \
    --no-install-package transformers \
    --no-install-package opencv-python \
    --no-install-package opencv-python-headless \
    --no-install-package scikit-image \
    --no-install-package scipy \
    --no-install-package saxonche \
    --no-install-package sympy \
    --no-install-package rapidocr \
    --no-install-package faker \
    --no-install-package networkx \
    --no-install-package pypdfium2 \
    --no-install-package shapely \
    --no-install-package tree-sitter \
    --no-install-package tree-sitter-c \
    --no-install-package tree-sitter-javascript \
    --no-install-package tree-sitter-python \
    --no-install-package tree-sitter-typescript \
    --no-install-package python-docx \
    --no-install-package python-pptx \
    --no-install-package accelerate \
    --no-install-package rtree \
    --no-install-package openpyxl \
    --no-install-package pyclipper \
    --no-install-package pylatexenc \
    --no-install-package latex2mathml \
    --no-install-package polyfactory \
    --no-install-package marko \
    --no-install-package doclang \
    --no-install-package mail-parser \
    --no-install-package defusedxml

# SQLite persistence belongs to the DAM integration, not CUGA's ACP extra.
RUN --mount=type=cache,target=/root/.cache/uv \
    UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 uv pip install --python /opt/cuga/.venv/bin/python --no-deps \
    -r /opt/cuga/dam-requirements.txt \
    && /opt/cuga/.venv/bin/python -c \
    "from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver; assert AsyncSqliteSaver is not None"

# Policy storage initializes this local embedding model on the first chat turn.
# Cache it at build time so the agent does not need Hugging Face egress.
RUN /opt/cuga/.venv/bin/python -c \
    "from fastembed import TextEmbedding; list(TextEmbedding('BAAI/bge-small-en-v1.5', cache_dir='/opt/cuga/fastembed-cache').embed(['ready']))"

FROM ${DAM_BASE}

COPY --from=cuga-builder --chown=65532:0 /opt/cuga/.venv/ /opt/cuga/.venv/
COPY --from=cuga-builder --chown=65532:0 /opt/cuga/fastembed-cache/ /opt/cuga/fastembed-cache/
# The DAM integration: session store, checkpoints, DAM's MCP servers and the
# terminal console, run by the launchers below.
COPY --chown=65532:0 deployment/dam/cuga_dam/ /opt/cuga/dam-integration/deployment/dam/cuga_dam/
RUN /opt/cuga/.venv/bin/python -m compileall -q /opt/cuga/dam-integration
# The image's own manifest and launchers replace the default image's. With
# no runtime-manifests/cuga.yaml, the runtime reads /app/runtime-manifest.yaml
# alone, so it knows CUGA as its one harness.
COPY deployment/dam/rootfs/ /

# The runtime reads PLATFORM_HARNESS to name the harness. No credential is
# baked in: the Model Router on the agent's loopback serves every model call.
# DAM starts the harness when the agent starts, so CUGA_ACP_PREWARM has it load
# its graph then rather than on the first prompt. The bundled tool-search
# few-shots are a made-up HR conversation the model then quotes in answers.
ENV PLATFORM_HARNESS=cuga \
    PLATFORM_ROOT_GENERATION=default-1/cuga-1 \
    AGENT_SETTING_CONFIG=settings.openai.toml \
    CUGA_SESSION_HEADER=x-platform-session \
    CUGA_INSTRUCTION_FILES=/etc/AGENTS.md:/home/agent/AGENTS.md:/home/agent/work/AGENTS.md \
    CUGA_LOGGING_DIR=/tmp/cuga-logging \
    DYNACONF_SKILLS__ENABLED=true \
    DYNACONF_SKILLS__ROOT=agents \
    DYNACONF_ADVANCED_FEATURES__ENABLE_SHELL_TOOL=true \
    DYNACONF_ADVANCED_FEATURES__ENABLE_FILESYSTEM_TOOLS=true \
    DYNACONF_ADVANCED_FEATURES__SANDBOX_MODE=local \
    DYNACONF_ADVANCED_FEATURES__CUGA_LITE_BIND_TOOLS_MODE=all \
    DYNACONF_ADVANCED_FEATURES__CUGA_LITE_NL_AUTO_CONTINUE=true \
    DYNACONF_ADVANCED_FEATURES__OPENSANDBOX_SANDBOX=false \
    DYNACONF_ADVANCED_FEATURES__CUGA_LITE_ENABLE_FEW_SHOTS=false \
    CUGA_ACP_PREWARM=1 \
    FASTEMBED_CACHE_PATH=/opt/cuga/fastembed-cache \
    HF_HUB_OFFLINE=1 \
    LITELLM_LOCAL_MODEL_COST_MAP=True

ARG CUGA_SOURCE_COMMIT
RUN test -n "$CUGA_SOURCE_COMMIT"
LABEL org.opencontainers.image.revision="${CUGA_SOURCE_COMMIT}" \
      org.opencontainers.image.source="https://github.com/cuga-project/cuga-agent"

USER 65532

ARG DAM_BASE=quay.io/dam-agents/platform-base@sha256:ddd4bf60c00c4ad53192a9ec679c61eebdc86828a172cb4a33164528099be715

FROM ${DAM_BASE} AS cuga-builder

USER root
WORKDIR /opt/cuga

COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/

# Keep CUGA separate from DAM's /app runtime. Use the package exclusions from
# Dockerfile.ubi.slim to omit document processing and Evolve dependencies while
# retaining the ACP extra needed by DAM chat.
RUN --mount=type=cache,target=/root/.cache/uv \
    UV_LINK_MODE=copy uv sync --frozen --no-dev --no-editable --extra acp \
    --python 3.12 --no-python-downloads \
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

# Policy storage initializes this local embedding model on the first chat turn.
# Cache it at build time so DAM does not need Hugging Face egress at runtime.
RUN /opt/cuga/.venv/bin/python -c \
    "from fastembed import TextEmbedding; list(TextEmbedding('BAAI/bge-small-en-v1.5', cache_dir='/opt/cuga/fastembed-cache').embed(['ready']))"

FROM ${DAM_BASE}

USER root

# Keep DAM's runtime libraries; the exclusions above affect Python packages.
RUN dnf install -y --setopt=install_weak_deps=False \
    mesa-libGL libxcb glib2 libXext libSM libXrender \
    && dnf clean all && rm -rf /var/cache/dnf

COPY --from=cuga-builder --chown=65532:0 /opt/cuga/.venv/ /opt/cuga/.venv/
COPY --from=cuga-builder --chown=65532:0 /opt/cuga/fastembed-cache/ /opt/cuga/fastembed-cache/
COPY --chmod=0755 deployment/dam/prepare-workspace.sh /usr/local/bin/cuga-dam-prepare-workspace
COPY --chmod=0755 deployment/dam/harness-chat.sh /usr/local/bin/harness-chat
COPY --chmod=0755 deployment/dam/harness-terminal.sh /usr/local/bin/harness-terminal
COPY --chown=65532:0 deployment/dam/shell-command-approval.md /opt/cuga/default-policies/shell-command-approval.md
RUN ln -s /opt/cuga/.venv/bin/cuga /usr/local/bin/cuga \
    && ln -s /opt/cuga/.venv/bin/cuga-acp /usr/local/bin/cuga-acp

# DAM's model-provider connection supplies OPENAI_BASE_URL and replaces the
# placeholder authorization header at the gateway. No credential is baked in.
ENV AGENT_SETTING_CONFIG=settings.openai.toml \
    MODEL_NAME=azure/gpt-5.6-sol \
    OPENAI_API_KEY=dummy-placeholder \
    CUGA_FOLDER=/home/agent/work/.cuga \
    DYNACONF_POLICY__CUGA_FOLDER=/home/agent/work/.cuga \
    CUGA_DBS_DIR=/home/agent/work/.cuga/dbs \
    CUGA_LOGGING_DIR=/tmp/cuga-logging \
    DYNACONF_SKILLS__ENABLED=true \
    DYNACONF_SKILLS__ROOT=agents \
    DYNACONF_ADVANCED_FEATURES__ENABLE_SHELL_TOOL=true \
    DYNACONF_ADVANCED_FEATURES__ENABLE_FILESYSTEM_TOOLS=true \
    DYNACONF_ADVANCED_FEATURES__SANDBOX_MODE=local \
    DYNACONF_ADVANCED_FEATURES__CUGA_LITE_BIND_TOOLS_MODE=all \
    DYNACONF_ADVANCED_FEATURES__CUGA_LITE_NL_AUTO_CONTINUE=true \
    FASTEMBED_CACHE_PATH=/opt/cuga/fastembed-cache \
    HF_HUB_OFFLINE=1 \
    DYNACONF_ADVANCED_FEATURES__OPENSANDBOX_SANDBOX=false \
    LITELLM_LOCAL_MODEL_COST_MAP=True

ARG CUGA_SOURCE_COMMIT
RUN test -n "$CUGA_SOURCE_COMMIT"
LABEL org.opencontainers.image.revision="${CUGA_SOURCE_COMMIT}" \
      org.opencontainers.image.source="https://github.com/cuga-project/cuga-agent"

USER 65532
WORKDIR /app

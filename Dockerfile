FROM python:3.12-slim-trixie

# The installer requires curl (and certificates) to download the release archive
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Download the latest installer
ADD https://astral.sh/uv/install.sh /uv-installer.sh

# Run the installer then remove it
RUN sh /uv-installer.sh && rm /uv-installer.sh

# Ensure the installed binary is on the `PATH`
ENV PATH="/root/.local/bin/:$PATH"

# Set working directory
WORKDIR /app

# Copy dependency files
COPY pyproject.toml uv.lock ./

# Copy source code
COPY src/ ./src/
COPY docs/ ./docs/

# Install dependencies
RUN uv sync

# Create cuga_workspace directory
RUN mkdir -p /app/cuga_workspace

# Copy example files from huggingface examples
COPY docs/examples/huggingface/contacts.txt /app/cuga_workspace/contacts.txt
COPY docs/examples/huggingface/cuga_knowledge.md /app/cuga_workspace/cuga_knowledge.md
COPY docs/examples/huggingface/cuga_playbook.md /app/cuga_workspace/cuga_playbook.md
COPY docs/examples/huggingface/email_template.md /app/cuga_workspace/email_template.md

# Expose port 7860 (Hugging Face Spaces default)
EXPOSE 7860

# Set host to 0.0.0.0 to allow external connections
ENV CUGA_HOST=0.0.0.0

# Override the demo port to match HF Spaces
ENV DYNACONF_SERVER_PORTS__DEMO=7860

# Start the demo_crm service with read-only filesystem and no email services
CMD ["uv", "run", "cuga", "start", "manager", "--host", "0.0.0.0", "--crm", "--email", "--filesystem", "--cuga-workspace", "/app/cuga_workspace"]


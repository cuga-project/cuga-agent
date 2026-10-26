#!/bin/sh
set -eu

mkdir -p "$CUGA_FOLDER" "$CUGA_DBS_DIR" "$CUGA_FOLDER/tool_approvals"

# A workspace policy can be edited after creation; do not overwrite it.
shell_policy="$CUGA_FOLDER/tool_approvals/shell-command-approval.md"
if [ ! -e "$shell_policy" ]; then
    cp /opt/cuga/default-policies/shell-command-approval.md "$shell_policy"
fi

# DAM installs skills under /home/agent/.agents/skills. CUGA's "agents" root
# resolves to a sibling of CUGA_FOLDER on the persistent workspace volume.
skills_link="$(dirname "$CUGA_FOLDER")/.agents"
if [ -L "$skills_link" ]; then
    if [ "$(readlink "$skills_link")" != /home/agent/.agents ]; then
        echo "CUGA skills link points somewhere other than DAM's skill directory" >&2
        exit 1
    fi
elif [ -e "$skills_link" ]; then
    echo "CUGA skills path exists and is not a DAM skill-directory link" >&2
    exit 1
else
    ln -s /home/agent/.agents "$skills_link"
fi

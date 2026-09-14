# Commit

1. Start with `git status` (and `git diff` / `git log` as needed). Selectively stage files — do not blindly `git add .` unless the user asks.
2. Commit current changes, bundle relevant changes together. **Exception:** build/generated files may go in one commit together.
3. Follow [Conventional Commits](https://www.conventionalcommits.org) with a scope when useful.
4. Add bullet points in the commit description body explaining why / what changed.
5. Always pass `-s` / `--signoff` (repo requires DCO on every commit).

Example:

```bash
git commit -s -m "$(cat <<'EOF'
feat(scope): short summary

- bullet one
- bullet two

EOF
)"
```

Do not update git config. Do not skip hooks unless the user explicitly asks. Do not push unless asked.

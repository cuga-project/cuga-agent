# Schema-trim corpus fixtures

`public_ops.json` is vendored: extracted operations from 12 public OpenAPI / JSON Schema
demo shapes plus 10 MCP tool `inputSchema`s. Tests run offline from this file.

AppWorld (`appworld_ops.json`) is **not** vendored (~473KB, 473 operations). Generate it
locally from cuga-eval:

```text
cuga-eval/benchmarks/appworld/appworld/data/api_docs/openapi/*.json
```

Use `extract_openapi_operations` in `corpus_loss.py`. If the fixture is present,
`test_schema_trim_appworld_corpus.py` runs; otherwise those tests skip.

Neither AppWorld nor `public_ops.json` contains `$ref` / `$defs` (AppWorld specs are
inlined). Nested-reference coverage remains in `test_find_tools_schema_trim.py`.

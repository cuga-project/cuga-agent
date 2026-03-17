# postgres-pgvector

PostgreSQL with pgvector extension for Cuga agent production storage (policies, embeddings).

## Install

```bash
helm install postgres-pgvector ./deployment/helm/postgres-pgvector \
  --set auth.password=YOUR_SECURE_PASSWORD
```

Or with existing secret:

```bash
kubectl create secret generic pg-secret --from-literal=password=YOUR_SECURE_PASSWORD
helm install postgres-pgvector ./deployment/helm/postgres-pgvector \
  --set auth.existingSecret=pg-secret
```

## Cuga integration

Set `storage.mode=prod` and `storage.postgres_url` in Cuga:

```
postgresql://cuga:<password>@postgres-pgvector.<namespace>.svc.cluster.local:5432/cuga
```

Or via env: `STORAGE_POSTGRES_URL=postgresql://...`

## Values

| Key | Default | Description |
|-----|---------|-------------|
| auth.database | cuga | Database name |
| auth.username | cuga | PostgreSQL user |
| auth.password | "" | Password (required unless existingSecret) |
| auth.existingSecret | "" | Existing secret name |
| auth.existingSecretKey | password | Key in existing secret |
| persistence.enabled | true | Use PVC for data |
| persistence.size | 10Gi | PVC size |
| image.repository | pgvector/pgvector | Image |
| image.tag | pg16 | Image tag |

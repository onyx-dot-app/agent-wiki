# agent-wiki helm charts

This branch is auto-managed by `helm/chart-releaser-action`. It hosts the chart
index for `https://onyx-dot-app.github.io/agent-wiki/`.

```bash
helm repo add agent-wiki https://onyx-dot-app.github.io/agent-wiki
helm repo update
helm install agent-wiki agent-wiki/agent-workspace
```

Charts are published from `deploy/helm/*/` on `main` by
`.github/workflows/helm-release.yml`.

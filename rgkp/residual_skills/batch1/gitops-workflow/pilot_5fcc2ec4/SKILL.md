---
name: rgkp-residual
description: Repository-grounded guidance for the target task.
---

# Residual Skill

## Execution Guidance

### 1

When writing `hack/gitops-demo/apps/overlays/dev/kustomization.yaml`, replace deprecated fields with their current equivalents as used in `manifests/openshift/kustomization.yaml`, `manifests/test/kustomization.yaml`, and `manifests/install/kustomization.yaml`:

- Replace `bases:` with `resources:`:
```yaml
resources:
  - ../../base
```

- Replace `patchesStrategicMerge:` with `patches:` using a `path:` key:
```yaml
patches:
  - path: patch-replicas.yaml
```

{{/*
Expand the name of the chart.
*/}}
{{- define "agent-workspace.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "agent-workspace.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "agent-workspace.labels" -}}
app.kubernetes.io/name: {{ include "agent-workspace.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
{{- end -}}

{{- define "agent-workspace.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "agent-workspace.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{/*
Shared backend env. Used by both backend and worker so the Postgres URL
and wiki path stay in lockstep.
*/}}
{{- define "agent-workspace.backendEnv" -}}
- name: DATABASE_URL
  valueFrom:
    secretKeyRef:
      name: {{ include "agent-workspace.fullname" . }}-secrets
      key: database-url
- name: REDIS_URL
  valueFrom:
    secretKeyRef:
      name: {{ include "agent-workspace.fullname" . }}-secrets
      key: redis-url
- name: OPENSEARCH_URL
  valueFrom:
    secretKeyRef:
      name: {{ include "agent-workspace.fullname" . }}-secrets
      key: opensearch-url
- name: WIKI_DIR
  value: /wiki
- name: SECRET_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "agent-workspace.fullname" . }}-secrets
      key: secret-key
- name: ALLOWED_EMAILS
  value: {{ .Values.allowedEmails | quote }}
- name: MAX_QUEUE_SIZE
  value: {{ .Values.maxQueueSize | quote }}
- name: AUTH_MODE
  value: {{ .Values.auth.mode | quote }}
- name: SECURE_COOKIES
  value: {{ .Values.secureCookies | quote }}
{{- if eq .Values.auth.mode "oidc" }}
- name: OIDC_ISSUER
  value: {{ .Values.auth.oidc.issuer | quote }}
- name: OIDC_CLIENT_ID
  value: {{ .Values.auth.oidc.clientId | quote }}
- name: OIDC_CLIENT_SECRET
  valueFrom:
    secretKeyRef:
      name: {{ include "agent-workspace.fullname" . }}-secrets
      key: oidc-client-secret
- name: OIDC_REDIRECT_URI
  value: {{ default (printf "https://%s/api/auth/oidc/callback" .Values.ingress.host) .Values.auth.oidc.redirectUri | quote }}
{{- end }}
{{- end -}}

{{- define "agent-workspace.backendVolumeMounts" -}}
- name: wiki-data
  mountPath: /wiki
{{- end -}}

{{- define "agent-workspace.backendVolumes" -}}
- name: wiki-data
  persistentVolumeClaim:
    claimName: {{ include "agent-workspace.fullname" . }}-wiki-data
{{- end -}}

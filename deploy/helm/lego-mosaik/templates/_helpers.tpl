{{- define "lego-mosaik.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "lego-mosaik.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else if contains .Chart.Name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "lego-mosaik.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{ include "lego-mosaik.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "lego-mosaik.selectorLabels" -}}
app.kubernetes.io/name: {{ include "lego-mosaik.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "lego-mosaik.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "lego-mosaik.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{- define "lego-mosaik.image" -}}
{{- printf "%s:%s" .Values.image.repository (.Values.image.tag | default .Chart.AppVersion) -}}
{{- end -}}

{{/* Namen der Subchart-Ressourcen (siehe _helpers.tpl der Subcharts) */}}
{{- define "lego-mosaik.postgresql.fullname" -}}
{{- printf "%s-postgresql" .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "lego-mosaik.garage.fullname" -}}
{{- printf "%s-garage" .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "lego-mosaik.s3SecretName" -}}
{{- .Values.s3.existingSecret | default (printf "%s-s3" (include "lego-mosaik.fullname" .)) -}}
{{- end -}}

{{- define "lego-mosaik.s3Endpoint" -}}
{{- if .Values.s3.endpoint -}}
{{- .Values.s3.endpoint -}}
{{- else if .Values.garage.enabled -}}
{{- printf "http://%s:3900" (include "lego-mosaik.garage.fullname" .) -}}
{{- else -}}
{{- fail "s3.endpoint fehlt (oder garage.enabled=true setzen)" -}}
{{- end -}}
{{- end -}}

{{/* Umgebung für App, Migration und Jobs */}}
{{- define "lego-mosaik.env" -}}
{{- if .Values.postgresql.enabled }}
- name: DB_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ .Values.postgresql.auth.existingSecret | default (include "lego-mosaik.postgresql.fullname" .) }}
      key: password
- name: LEGO_MOSAIK_DATABASE_URL
  value: {{ printf "postgresql+psycopg://%s:$(DB_PASSWORD)@%s:5432/%s" .Values.postgresql.auth.username (include "lego-mosaik.postgresql.fullname" .) .Values.postgresql.auth.database | quote }}
{{- else if .Values.externalDatabase.existingSecret }}
- name: LEGO_MOSAIK_DATABASE_URL
  valueFrom:
    secretKeyRef:
      name: {{ .Values.externalDatabase.existingSecret }}
      key: {{ .Values.externalDatabase.existingSecretKey }}
{{- else if .Values.externalDatabase.url }}
- name: LEGO_MOSAIK_DATABASE_URL
  valueFrom:
    secretKeyRef:
      name: {{ include "lego-mosaik.fullname" . }}-db
      key: url
{{- else }}
{{- fail "Datenbank fehlt: postgresql.enabled=true oder externalDatabase.url/existingSecret setzen" }}
{{- end }}
- name: LEGO_MOSAIK_S3_ACCESS_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "lego-mosaik.s3SecretName" . }}
      key: accessKeyId
- name: LEGO_MOSAIK_S3_SECRET_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "lego-mosaik.s3SecretName" . }}
      key: secretAccessKey
{{- if .Values.config.otlpEndpoint }}
- name: OTEL_EXPORTER_OTLP_ENDPOINT
  value: {{ .Values.config.otlpEndpoint | quote }}
- name: OTEL_SERVICE_NAME
  value: {{ include "lego-mosaik.fullname" . | quote }}
{{- end }}
{{- with .Values.config.extraEnv }}
{{ toYaml . }}
{{- end }}
{{- end -}}

{{- define "lego-mosaik.envFrom" -}}
- configMapRef:
    name: {{ include "lego-mosaik.fullname" . }}
{{- with .Values.config.extraEnvFrom }}
{{ toYaml . }}
{{- end }}
{{- end -}}

{{/* Volumes für /tmp und die optionale Studio-Farbtabelle */}}
{{- define "lego-mosaik.volumes" -}}
- name: tmp
  emptyDir:
    sizeLimit: {{ .Values.tmpVolume.sizeLimit }}
{{- if .Values.studioColors.configMap }}
- name: studio
  configMap:
    name: {{ .Values.studioColors.configMap }}
{{- end }}
{{- end -}}

{{- define "lego-mosaik.volumeMounts" -}}
- name: tmp
  mountPath: /tmp
{{- if .Values.studioColors.configMap }}
- name: studio
  mountPath: /app/studio
  readOnly: true
{{- end }}
{{- end -}}

{{/* Job/initContainer-Container mit einem Befehl der CLI */}}
{{- define "lego-mosaik.commandContainer" -}}
- name: {{ .name }}
  image: {{ include "lego-mosaik.image" .root }}
  imagePullPolicy: {{ .root.Values.image.pullPolicy }}
  args: {{ toJson .args }}
  env:
    {{- include "lego-mosaik.env" .root | nindent 4 }}
    {{- with .extraEnv }}
    {{- toYaml . | nindent 4 }}
    {{- end }}
  envFrom:
    {{- include "lego-mosaik.envFrom" .root | nindent 4 }}
  securityContext:
    {{- toYaml .root.Values.securityContext | nindent 4 }}
  {{- with .resources }}
  resources:
    {{- toYaml . | nindent 4 }}
  {{- end }}
  volumeMounts:
    {{- include "lego-mosaik.volumeMounts" .root | nindent 4 }}
{{- end -}}

{{/* Pod-Spec des Katalog-Imports (Job und CronJob) */}}
{{- define "lego-mosaik.catalogImportPod" -}}
restartPolicy: OnFailure
serviceAccountName: {{ include "lego-mosaik.serviceAccountName" . }}
automountServiceAccountToken: false
{{- with .Values.imagePullSecrets }}
imagePullSecrets:
  {{- toYaml . | nindent 2 }}
{{- end }}
securityContext:
  {{- toYaml .Values.podSecurityContext | nindent 2 }}
initContainers:
  {{- include "lego-mosaik.commandContainer" (dict "root" . "name" "migrieren" "args" (list "migrieren" "--warten" "300")) | nindent 2 }}
containers:
  {{- include "lego-mosaik.commandContainer" (dict "root" . "name" "katalog-import" "args" (list "katalog-import" "--warten" "300") "resources" .Values.catalogImport.resources) | nindent 2 }}
volumes:
  {{- include "lego-mosaik.volumes" . | nindent 2 }}
{{- end -}}

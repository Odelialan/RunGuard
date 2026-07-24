{{- define "runguard.name" -}}
runguard
{{- end }}

{{- define "runguard.fullname" -}}
{{- printf "%s-%s" .Release.Name (include "runguard.name" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "runguard.labels" -}}
app.kubernetes.io/name: {{ include "runguard.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

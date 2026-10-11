{{- define "att.labels" -}}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/part-of: ask-the-tower
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
{{- end -}}

{{/* A value kept across upgrades: the existing Secret's data[key] if there is one, else `fresh` (base64). */}}
{{- define "att.keep" -}}
{{- $data := .existing | default dict -}}
{{- if hasKey $data .key -}}{{ index $data .key }}{{- else -}}{{ .fresh }}{{- end -}}
{{- end -}}

{{/* Helpers for the binding-page chart (each chart carries its own copy: named templates are global in an umbrella). */}}
{{- define "binding-page.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* Fixed object names (= the chart name, like the compose service names) so services find each other as
     http://mock-carrier:8443 etc. One Ask the Tower release per namespace. */}}
{{- define "binding-page.fullname" -}}
{{- default .Chart.Name .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "binding-page.selectorLabels" -}}
app.kubernetes.io/name: {{ include "binding-page.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "binding-page.labels" -}}
{{ include "binding-page.selectorLabels" . }}
app.kubernetes.io/part-of: ask-the-tower
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end -}}

{{- define "binding-page.image" -}}
{{- $g := .Values.global | default dict -}}
{{- $reg := .Values.image.registry | default ($g.imageRegistry | default "") -}}
{{- $tag := .Values.image.tag | default ($g.imageTag | default "latest") -}}
{{- if $reg }}{{ $reg }}/{{ end }}{{ .Values.image.repository }}:{{ $tag }}
{{- end -}}

{{- define "binding-page.pullPolicy" -}}
{{- $g := .Values.global | default dict -}}
{{- .Values.image.pullPolicy | default ($g.imagePullPolicy | default "IfNotPresent") -}}
{{- end -}}

{{- define "binding-page.serviceAccountName" -}}
{{- if and .Values.serviceAccount .Values.serviceAccount.create -}}
{{- default (include "binding-page.fullname" .) .Values.serviceAccount.name -}}
{{- else if .Values.serviceAccount -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- else -}}
default
{{- end -}}
{{- end -}}

{{/* Environment: the shared store/crypto variables from global (when useStoreEnv), then the chart's own `env`
     (wins on a clash); empty values are dropped (an unset variable, as on AWS: DYNAMO_ENDPOINT). Then secret
     references: global.storeSecretEnv (local keys on kind) and the chart's `secretEnv`, each {name, key}. */}}
{{- define "binding-page.env" -}}
{{- $g := .Values.global | default dict -}}
{{- $env := dict -}}
{{- if .Values.useStoreEnv }}{{ $env = merge $env ($g.storeEnv | default dict) }}{{ end -}}
{{- $env = merge (deepCopy (.Values.env | default dict)) $env -}}
{{- range $k := keys $env | sortAlpha }}
{{- $v := index $env $k }}
{{- if ne (toString $v) "" }}
- name: {{ $k }}
  value: {{ tpl (toString $v) $ | quote }}
{{- end }}
{{- end }}
{{- $sec := dict -}}
{{- if .Values.useStoreEnv }}{{ $sec = merge $sec ($g.storeSecretEnv | default dict) }}{{ end -}}
{{- $sec = merge (deepCopy (.Values.secretEnv | default dict)) $sec -}}
{{- range $k := keys $sec | sortAlpha }}
{{- $ref := index $sec $k }}
{{- if $ref }}
- name: {{ $k }}
  valueFrom:
    secretKeyRef:
      name: {{ tpl (default ($g.secretName | default "att-secrets") $ref.name) $ }}
      key: {{ $ref.key }}
{{- end }}
{{- end }}
{{- end -}}

{{- define "binding-page.podSecurityContext" -}}
{{ toYaml .Values.podSecurityContext }}
{{- end -}}

{{- define "binding-page.securityContext" -}}
{{ toYaml .Values.securityContext }}
{{- end -}}

{{- define "binding-page.scheduling" -}}
{{- $g := .Values.global | default dict -}}
{{- $ns := merge (deepCopy (.Values.nodeSelector | default dict)) ($g.nodeSelector | default dict) -}}
{{- with $ns }}
nodeSelector:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with .Values.tolerations }}
tolerations:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with .Values.affinity }}
affinity:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- end -}}

{{/* NetworkPolicy `from` entries: pods by app.kubernetes.io/name, plus CIDRs (the ALB's VPC on EKS). */}}
{{- define "binding-page.npFrom" -}}
{{- range .names }}
- podSelector:
    matchLabels:
      app.kubernetes.io/name: {{ . }}
{{- end }}
{{- range .cidrs }}
- ipBlock:
    cidr: {{ . }}
{{- end }}
{{- end -}}

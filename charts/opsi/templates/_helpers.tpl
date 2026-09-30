{{- define "opsi.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- define "opsi.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else if contains (include "opsi.name" .) .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name (include "opsi.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- define "opsi.selectorLabels" -}}
app.kubernetes.io/name: {{ include "opsi.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}
{{- define "opsi.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{ include "opsi.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}
{{- define "opsi.image" -}}
{{- if .Values.image.digest -}}
{{- printf "%s@%s" .Values.image.repository .Values.image.digest -}}
{{- else -}}
{{- printf "%s:%s" .Values.image.repository .Values.image.tag -}}
{{- end -}}
{{- end -}}
{{- define "opsi.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "opsi.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}
{{- define "opsi.dataClaim" -}}
{{- default (printf "%s-data" (include "opsi.fullname" .)) .Values.persistence.existingClaim -}}
{{- end -}}
{{- define "opsi.tftpClaim" -}}
{{- default (printf "%s-tftp" (include "opsi.fullname" .)) .Values.pxe.tftpPersistence.existingClaim -}}
{{- end -}}
{{- define "opsi.validate" -}}
{{- if .Values.fileStorage.enabled -}}
{{- if not .Values.fileStorage.existingClaim -}}{{- fail "fileStorage.existingClaim requires a prepared separate PVC; see docs/storage.md" -}}{{- end -}}
{{- if or (eq .Values.fileStorage.existingClaim (include "opsi.dataClaim" .)) (eq .Values.fileStorage.existingClaim (include "opsi.tftpClaim" .)) -}}{{- fail "fileStorage must use a separate claim from identity and TFTP storage" -}}{{- end -}}
{{- if ne (empty .Values.fileStorage.mappedUid) (empty .Values.fileStorage.mappedGid) -}}{{- fail "fileStorage.mappedUid and mappedGid must be set together" -}}{{- end -}}
{{- end -}}
{{- if not (has (int .Values.replicaCount) (list 0 1)) -}}
{{- fail "replicaCount must be 0 or 1; this config server cannot share mutable state between replicas" -}}
{{- end -}}
{{- if and .Values.pxe.enabled (not .Values.pxe.hostNetwork) -}}
{{- fail "pxe.enabled requires pxe.hostNetwork=true; a UDP/69 Service does not forward TFTP transfer ports" -}}
{{- end -}}
{{- if ne .Values.admin.username "adminuser" -}}
{{- fail "admin.username must be adminuser, as created by the official image" -}}
{{- end -}}
{{- range $key := list "hostId" "externalUrl" "ipAddress" "depotRemoteUrl" "depotWebdavUrl" "repositoryRemoteUrl" "workbenchRemoteUrl" -}}
{{- if not (get $.Values.server $key) -}}
{{- fail (printf "server.%s must be explicitly configured" $key) -}}
{{- end -}}
{{- end -}}
{{- if not .Values.server.configServiceUrls -}}{{- fail "server.configServiceUrls must contain at least one HTTPS URL" -}}{{- end -}}
{{- range $key := list "admin" "mysql" "redis" -}}
{{- if not (get (get $.Values $key) "existingSecret") -}}{{- fail (printf "%s.existingSecret is required" $key) -}}{{- end -}}
{{- end -}}
{{- if or (not .Values.mysql.host) (not .Values.redis.host) -}}{{- fail "mysql.host and redis.host are required" -}}{{- end -}}
{{- if and .Values.connector.enabled (not .Values.connector.existingSecret) -}}{{- fail "connector.existingSecret is required when connector.enabled=true" -}}{{- end -}}
{{- end -}}

variable "project_id" {
  description = "Google Cloud プロジェクトID"
  type        = string
}

variable "region" {
  description = "デプロイするリージョン"
  type        = string
  default     = "asia-northeast1"
}

variable "function_name" {
  description = "Cloud Functionの名前"
  type        = string
  default     = "download_file_from_drive"
}

variable "runtime" {
  description = "Cloud Functionのランタイム"
  type        = string
  default     = "python310"
}

variable "entry_point" {
  description = "Cloud Functionのエントリポイント"
  type        = string
  default     = "download_file_from_drive"
}

variable "slack_token" {
  description = "The Slack API token for accessing the Slack workspace."
  type        = string
}

variable "slack_token_secret_name" {
  description = "Secret Managerで管理しているSlackトークンのシークレット名"
  type        = string
  default     = "slack-token"
}

variable "bigquery_dataset_id" {
  description = "BigQueryのデータセットID"
  type        = string
  default     = "lake"
}

variable "bigquery_table_id" {
  description = "BigQueryのテーブルID"
  type        = string
  default     = "messages"
}

variable "slack_api_message_limit" {
  description = "Slack APIで一度に取得するメッセージの最大数"
  type        = number
  default     = 200 # デフォルト値として200を設定
}

variable "appsheet_api_key" {
  description = "AppSheet APIキー"
  type        = string
}

variable "app_id" {
  description = "AppSheet アプリケーションID"
  type        = string
}



terraform {
  required_version = ">= 1.9.6" # 最新の安定バージョンへ更新
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 6.3.0"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = ">= 6.3.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.2.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.1.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# ランダムなサフィックスを生成
resource "random_id" "bucket_suffix" {
  byte_length = 4
}

# Cloud Storageバケットの作成（ユーザーの1on1動画ファイル用）
resource "google_storage_bucket" "user_videos_bucket" {
  name          = "${var.project_id}-user-1on1-videos-${random_id.bucket_suffix.hex}"
  location      = var.region
  force_destroy = true

  uniform_bucket_level_access = true

  lifecycle_rule {
    action {
      type = "Delete"
    }
    condition {
      age = 365
    }
  }
}

# Cloud Storageバケットの作成（Cloud Functionのソースコード用）
resource "google_storage_bucket" "function_bucket" {
  name          = "${var.project_id}-function-source-${random_id.bucket_suffix.hex}"
  location      = var.region
  force_destroy = true

  uniform_bucket_level_access = true
}

resource "google_storage_bucket" "slack_messages_assets" {
  name          = "${var.project_id}-slack-messages-assets"
  location      = "us-central1"
  force_destroy = false
}

resource "google_storage_bucket" "strengthsfinder_assets_2024" {
  name          = "${var.project_id}-strengthsfinder-assets-2024"
  location      = "us-central1"
  force_destroy = false
}


# Cloud FunctionのソースコードをZIP化（download_file_from_drive）
data "archive_file" "function_zip_download" {
  type        = "zip"
  output_path = "${path.module}/function_source/download_file_from_drive.zip"
  source_dir  = "${path.module}/cloud_functions/download_file_from_drive"
}

# Cloud FunctionのソースコードをZIP化（gemini_analysis）
data "archive_file" "function_zip_gemini" {
  type        = "zip"
  output_path = "${path.module}/function_source/gemini_analysis.zip"
  source_dir  = "${path.module}/cloud_functions/gemini_analysis"
}

# ファイル変更をトリガーするためのnull_resource
resource "null_resource" "zip_function_code" {
  provisioner "local-exec" {
    command = "echo Function code updated!"
  }

  triggers = {
    python_code_gemini  = filesha256("${path.module}/cloud_functions/gemini_analysis/main.py")
    requirements_gemini = filesha256("${path.module}/cloud_functions/gemini_analysis/requirements.txt")
  }
}

# Slack Tokenを格納するためのGoogle Secret Manager Secretの作成
resource "google_secret_manager_secret" "slack_token_secret" {
  secret_id = "slack-token" # Secret IDを設定
  project   = var.project_id

  replication {
    auto {} # 自動レプリケーションを指定
  }
}

# Slack Tokenのシークレットバージョンを追加
resource "google_secret_manager_secret_version" "slack_token_secret_version" {
  secret      = google_secret_manager_secret.slack_token_secret.id
  secret_data = var.slack_token # 変数でSlackトークンを渡す
}

# Cloud Function用のサービスアカウントにSecret Managerアクセス権を付与
resource "google_project_iam_member" "function_service_account_secret_accessor" {
  project = var.project_id
  role    = "roles/secretmanager.secretAccessor"
  member  = "serviceAccount:${google_service_account.function_service_account.email}"
}


# Cloud FunctionのソースコードをCloud Storageにアップロード（download_file_from_drive）
resource "google_storage_bucket_object" "function_source_download" {
  name   = "download_file_from_drive-source.zip"
  bucket = google_storage_bucket.function_bucket.name
  source = data.archive_file.function_zip_download.output_path

  depends_on = [null_resource.zip_function_code]
}

# Cloud FunctionのソースコードをCloud Storageにアップロード（gemini_analysis）
resource "google_storage_bucket_object" "function_source_gemini" {
  name   = "gemini_analysis-source.zip"
  bucket = google_storage_bucket.function_bucket.name
  source = data.archive_file.function_zip_gemini.output_path

  depends_on = [null_resource.zip_function_code]
}

# Cloud Function用のサービスアカウントの作成
resource "google_service_account" "function_service_account" {
  account_id   = "cloud-functions-sa"
  display_name = "Cloud Functions Service Account"
}

# Cloud Functionの作成（download_file_from_drive）
resource "google_cloudfunctions2_function" "function_download" {
  name     = "download_file_from_drive"
  location = var.region

  build_config {
    runtime     = "python311"
    entry_point = "main"
    source {
      storage_source {
        bucket = google_storage_bucket.function_bucket.name
        object = google_storage_bucket_object.function_source_download.name
      }
    }
  }

  service_config {
    available_memory      = "256Mi"
    timeout_seconds       = 600
    service_account_email = google_service_account.function_service_account.email
    environment_variables = {
      GCP_PROJECT_ID    = var.project_id
      BUCKET_NAME = google_storage_bucket.strengthsfinder_assets_2024.name
      APP_ID      = var.app_id # App ID
    }
  }

  lifecycle {
    replace_triggered_by = [
      google_storage_bucket_object.function_source_download
    ]
  }

  depends_on = [
    google_project_service.cloudfunctions,
    google_project_service.cloudbuild,
    google_project_service.artifactregistry,
    google_project_service.run
  ]
}

# Cloud Functionの作成（gemini_analysis）
resource "google_cloudfunctions2_function" "function_gemini" {
  name     = "gemini_analysis"
  location = var.region

  build_config {
    runtime     = "python311"
    entry_point = "main"
    source {
      storage_source {
        bucket = google_storage_bucket.function_bucket.name
        object = google_storage_bucket_object.function_source_gemini.name
      }
    }
  }

  service_config {
    available_cpu         = "1"
    available_memory      = "2048Mi"
    timeout_seconds       = 600
    service_account_email = google_service_account.function_service_account.email
    environment_variables = {
      GCP_PROJECT_ID  = var.project_id
      GCS_BUCKET_NAME = google_storage_bucket.slack_messages_assets.name # ここでバケット名を参照
      APP_ID          = var.app_id                                       # App ID
    }
  }

  lifecycle {
    replace_triggered_by = [
      google_storage_bucket_object.function_source_gemini,
    ]
  }

  depends_on = [
    google_project_service.cloudfunctions,
    google_project_service.cloudbuild,
    google_project_service.artifactregistry,
    google_project_service.run,
    google_storage_bucket.slack_messages_assets # 依存関係にバケットを追加
  ]
}

# Slack messages Cloud FunctionのソースコードをZIP化
data "archive_file" "function_zip_slack_messages" {
  type        = "zip"
  output_path = "/tmp/slack_messages_to_bigquery/function-source.zip"
  source_dir  = "${path.module}/cloud_functions/slack_messages_to_bigquery"
}

# Cloud StorageにCloud Functionのソースコードをアップロード
resource "google_storage_bucket_object" "function_source_slack_messages" {
  name   = "slack_messages_to_bigquery/function-source.zip"
  bucket = google_storage_bucket.function_bucket.name
  source = data.archive_file.function_zip_slack_messages.output_path # ZIP化されたファイルのパス
}

# Slack messages Cloud Functionのデプロイ
resource "google_cloudfunctions2_function" "function_slack_messages" {
  name     = "slack_messages_to_bigquery"
  location = "asia-northeast1" # Cloud Functionのデプロイ先

  labels = {
    deployment-tool = "cli-gcloud"
  }

  build_config {
    runtime     = "python311"
    entry_point = "main" # エントリーポイントの関数名
    source {
      storage_source {
        bucket = google_storage_bucket.function_bucket.name
        object = google_storage_bucket_object.function_source_slack_messages.name
      }
    }
  }

  lifecycle {
    replace_triggered_by = [ # ソースコードが変更された場合に再デプロイ
      google_storage_bucket_object.function_source_slack_messages
    ]
  }

  service_config {
    available_cpu    = "1"
    available_memory = "2048Mi"
    timeout_seconds  = 600
    environment_variables = {
      GCP_PROJECT_ID          = var.project_id
      SLACK_TOKEN_SECRET_NAME = var.slack_token_secret_name
      BIGQUERY_DATASET_ID     = var.bigquery_dataset_id
      BIGQUERY_TABLE_ID       = var.bigquery_table_id
      SLACK_API_MESSAGE_LIMIT = var.slack_api_message_limit
    }
    service_account_email = google_service_account.function_service_account.email
  }

  depends_on = [
    google_project_service.cloudfunctions,
    google_project_service.cloudbuild,
    google_project_service.artifactregistry,
    google_project_service.run,
  ]
}

# poll_dataform_job Cloud FunctionのURLを取得
output "poll_dataform_job_url" {
  value = google_cloudfunctions2_function.function_poll_dataform_job.service_config[0].uri
}

# AppSheet APIキーを格納するためのGoogle Secret Manager Secretの作成
resource "google_secret_manager_secret" "appsheet_api_key_secret" {
  secret_id = "appsheet-api-key" # Secret IDを設定
  project   = var.project_id

  replication {
    auto {} # 自動レプリケーションを指定
  }
}

# AppSheet APIキーのシークレットバージョンを追加
resource "google_secret_manager_secret_version" "appsheet_api_key_secret_version" {
  secret      = google_secret_manager_secret.appsheet_api_key_secret.id
  secret_data = var.appsheet_api_key # 変数でAppSheet APIキーを渡す
}


# Cloud Functionの作成（kick_dataform_job）
resource "google_cloudfunctions2_function" "function_kick_dataform_job" {
  name     = "kick_dataform_job"
  location = var.region

  build_config {
    runtime     = "python311"
    entry_point = "kick_dataform_job"
    source {
      storage_source {
        bucket = google_storage_bucket.function_bucket.name
        object = google_storage_bucket_object.function_source_kick_dataform_job.name
      }
    }
  }

  service_config {
    available_cpu         = "1"
    available_memory      = "2048Mi"
    timeout_seconds       = 600
    service_account_email = google_service_account.function_service_account.email
    environment_variables = {
      GCP_PROJECT_ID    = var.project_id
      REGION            = var.region
      POLL_FUNCTION_URL = google_cloudfunctions2_function.function_poll_dataform_job.service_config[0].uri # poll_dataform_jobのURL
      APP_ID            = var.app_id                                                                       # App IDは環境変数から取得
    }
  }

  lifecycle {
    replace_triggered_by = [
      google_storage_bucket_object.function_source_kick_dataform_job
    ]
  }

  depends_on = [
    google_project_service.cloudfunctions,
    google_project_service.cloudbuild,
    google_project_service.artifactregistry,
    google_project_service.run,
    google_project_iam_member.function_service_account_secret_accessor
  ]
}


# poll_dataform_job Cloud Functionのデプロイ
resource "google_cloudfunctions2_function" "function_poll_dataform_job" {
  name     = "poll_dataform_job"
  location = var.region

  labels = {
    deployment-tool = "cli-gcloud"
  }

  build_config {
    runtime     = "python311"
    entry_point = "poll_dataform_job"
    source {
      storage_source {
        bucket = google_storage_bucket.function_bucket.name
        object = google_storage_bucket_object.function_source_kick_dataform_job.name
      }
    }
  }

  lifecycle {
    replace_triggered_by = [
      google_storage_bucket_object.function_source_kick_dataform_job
    ]
  }

  service_config {
    available_cpu         = "1"
    available_memory      = "1024Mi"
    timeout_seconds       = 600
    service_account_email = google_service_account.function_service_account.email
    environment_variables = {
      GCP_PROJECT_ID = var.project_id # プロジェクトID
      REGION         = var.region     # リージョン
      APP_ID         = var.app_id     # App ID
    }
  }

  depends_on = [
    google_project_service.cloudfunctions,
    google_project_service.cloudbuild,
    google_project_service.artifactregistry,
    google_project_service.run
  ]
}


# kick_dataform_job Cloud FunctionのソースコードをZIP化
data "archive_file" "function_zip_kick_dataform_job" {
  type        = "zip"
  output_path = "/tmp/kick_dataform_job/function-source.zip"
  source_dir  = "${path.module}/cloud_functions/kick_dataform_job"
}

# Cloud StorageにCloud Functionのソースコードをアップロード
resource "google_storage_bucket_object" "function_source_kick_dataform_job" {
  name   = "kick_dataform_job/function-source.zip"
  bucket = google_storage_bucket.function_bucket.name
  source = data.archive_file.function_zip_kick_dataform_job.output_path # ZIP化されたファイルのパス
}

# Cloud Tasksキューを作成するリソース
resource "google_cloud_tasks_queue" "dataform_completion_checker" {
  name     = "dataform-completion-checker"
  location = var.region

  rate_limits {
    max_dispatches_per_second = 5 # 1秒あたりの最大ディスパッチ数
  }

  retry_config {
    max_attempts  = 10     # 最大リトライ回数を増やす
    min_backoff   = "20s"  # 最小待機時間を20秒に設定
    max_backoff   = "120s" # 最大待機時間を2分に設定
    max_doublings = 5      # バックオフの倍増回数
  }
}

# Cloud Tasksキューにタスクを追加するためのサービスアカウントに必要なIAMロールを付与
resource "google_project_iam_member" "task_queue_invoker" {
  project = var.project_id
  role    = "roles/cloudtasks.enqueuer"                                               # Cloud Tasksにタスクを追加する権限
  member  = "serviceAccount:${google_service_account.function_service_account.email}" # サービスアカウントのメールアドレス
}

# poll_dataform_job を呼び出す権限を付与
resource "google_cloudfunctions2_function_iam_member" "poll_dataform_job_invoker" {
  project        = google_cloudfunctions2_function.function_poll_dataform_job.project
  location       = google_cloudfunctions2_function.function_poll_dataform_job.location
  cloud_function = google_cloudfunctions2_function.function_poll_dataform_job.name
  role           = "roles/cloudfunctions.invoker"
  member         = "serviceAccount:${google_service_account.function_service_account.email}"

  depends_on = [
    google_service_account.function_service_account,           # サービスアカウントの作成に依存
    google_cloudfunctions2_function.function_poll_dataform_job # poll_dataform_job 関数に依存
  ]
}

# 必要なAPIを有効化
resource "google_project_service" "cloudfunctions" {
  service            = "cloudfunctions.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "cloudbuild" {
  service            = "cloudbuild.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "artifactregistry" {
  service            = "artifactregistry.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "run" {
  service            = "run.googleapis.com"
  disable_on_destroy = false
}

# Cloud Tasks APIを有効化
resource "google_project_service" "cloudtasks" {
  project            = var.project_id
  service            = "cloudtasks.googleapis.com"
  disable_on_destroy = false
}


# サービスアカウントに必要なロールを付与
resource "google_project_iam_member" "function_service_account_user" {
  project = var.project_id
  role    = "roles/iam.serviceAccountUser"
  member  = "serviceAccount:${google_service_account.function_service_account.email}"
}

resource "google_project_iam_member" "function_service_account_storage_object_creator" {
  project = var.project_id
  role    = "roles/storage.objectCreator"
  member  = "serviceAccount:${google_service_account.function_service_account.email}"
}

resource "google_project_iam_member" "cloudfunctions_artifactregistry_reader" {
  project = var.project_id
  role    = "roles/artifactregistry.reader"
  member  = "serviceAccount:${google_service_account.function_service_account.email}"
}

resource "google_project_iam_member" "function_service_account_bigquery_admin" {
  project = var.project_id
  role    = "roles/bigquery.admin"
  member  = "serviceAccount:${google_service_account.function_service_account.email}"
}

resource "google_project_iam_member" "function_service_account_token_creator" {
  project = var.project_id
  role    = "roles/iam.serviceAccountTokenCreator"
  member  = "serviceAccount:${google_service_account.function_service_account.email}"
}

# 出力
output "function_url_download_file_from_drive" {
  value = google_cloudfunctions2_function.function_download.service_config[0].uri
}

output "function_url_gemini_analysis" {
  value = google_cloudfunctions2_function.function_gemini.service_config[0].uri
}

# 出力
output "function_url_slack_messages_to_bigquery" {
  value = google_cloudfunctions2_function.function_slack_messages.service_config[0].uri
}

# jkamiya@g-gen.co.jpに対してサービスアカウントの権限を付与
resource "google_project_iam_member" "impersonation_role_jkamiya" {
  project = var.project_id
  role    = "roles/iam.serviceAccountUser" # サービスアカウントを代わりに実行する権限
  member  = "user:jkamiya@g-gen.co.jp"
}

resource "google_project_iam_member" "token_creator_role_jkamiya" {
  project = var.project_id
  role    = "roles/iam.serviceAccountTokenCreator" # サービスアカウントのトークンを生成する権限
  member  = "user:jkamiya@g-gen.co.jp"
}

resource "google_project_iam_member" "function_service_account_log_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.function_service_account.email}"
}

# Cloud Function用のサービスアカウントにCloud Storageアクセス権を付与
resource "google_project_iam_member" "function_service_account_storage_object_viewer" {
  project = var.project_id
  role    = "roles/storage.objectViewer" # Cloud Storage内のオブジェクトを一覧表示する権限
  member  = "serviceAccount:${google_service_account.function_service_account.email}"
}

# Cloud Function用のサービスアカウントにCloud Storage Object Adminアクセス権を付与
resource "google_project_iam_member" "function_service_account_storage_object_admin" {
  project = var.project_id
  role    = "roles/storage.objectAdmin" # Cloud Storage内のオブジェクトに対する全権限
  member  = "serviceAccount:${google_service_account.function_service_account.email}"
}




import os
import re
import requests
import logging
from google.cloud import storage
from google.cloud import secretmanager
from flask import Request
import functions_framework
import google.cloud.logging
import mimetypes

# Cloud Loggingの初期化
logging_client = google.cloud.logging.Client()
logging_client.setup_logging()

# ファイル名のサニタイズ
def sanitize_filename(filename):
    return re.sub(r'[\\/:*?"<>|]', "_", filename)

# ファイル名と拡張子をURLから取得する関数
def parse_filename_from_url(file_url):
    """URLからファイル名と拡張子を解析する"""
    try:
        match = re.search(r'fileName=([^&]+)', file_url)
        if match:
            file_name = match.group(1)
            file_name = re.sub(r'%2F', '/', file_name)
            logging.info(f"Parsed file name: {file_name}")
            return file_name
        else:
            logging.error("Failed to parse file name from URL")
            return None
    except Exception as e:
        logging.error(f"Error parsing file name from URL: {e}")
        raise

# MIMEタイプをファイル名から決定する関数
def get_mime_type(file_name):
    """ファイル名からMIMEタイプを決定"""
    mime_type, _ = mimetypes.guess_type(file_name)
    if mime_type:
        logging.info(f"Determined MIME type: {mime_type}")
        return mime_type
    else:
        logging.warning(
            f"Could not determine MIME type for file: {file_name}, using 'application/octet-stream'")
        return 'application/octet-stream'

# AppSheetのファイルURLからファイルをバイナリでダウンロードする関数
def download_file_from_appsheet(file_url):
    """AppSheetのURLからファイルをバイナリでダウンロード"""
    try:
        logging.info(f"Downloading file from AppSheet URL: {file_url}")
        response = requests.get(file_url)
        if response.status_code == 200:
            file_content = response.content  # バイナリデータを取得
            logging.info(f"File downloaded from AppSheet. Size: {len(file_content)} bytes.")
            return file_content
        else:
            logging.error(f"Failed to download file from AppSheet. Status code: {response.status_code}")
            return None
    except Exception as e:
        logging.error(f"Error downloading file: {e}")
        raise

# 指定されたユーザーID配下のすべてのファイルを削除する関数
def delete_existing_files(bucket, user_id):
    """ユーザーID配下のすべてのファイルを削除"""
    try:
        blobs = bucket.list_blobs(prefix=f"{user_id}/")
        for blob in blobs:
            logging.info(f"Deleting file: {blob.name}")
            blob.delete()
        logging.info(f"All files under user_id {user_id} have been deleted.")
    except Exception as e:
        logging.error(f"Error deleting files for user_id {user_id}: {e}")
        raise

# HTTPトリガー用のメイン関数
@functions_framework.http
def main(request: Request):
    try:
        logging.info(f"Received request: {request}")
        request_json = request.get_json(silent=True)
        logging.info(f"Request JSON: {request_json}")

        if not request_json:
            logging.error("No JSON data provided in the request")
            return "Invalid request: No JSON data provided", 400

        target_table_name = request_json.get("target_table_name")
        dest_column_name = request_json.get("dest_column_name")
        appsheet_file_path = request_json.get("appsheet_file_path")
        key_name = request_json.get("key_name")  # ユーザーIDとして使用
        key_value = request_json.get("key_value")  # ユーザーIDの値

        logging.info(f"Parsed input parameters: target_table_name={target_table_name}, dest_column_name={dest_column_name}, appsheet_file_path={appsheet_file_path}, key_name={key_name}, key_value={key_value}")

        if not all([target_table_name, dest_column_name, appsheet_file_path, key_name, key_value]):
            logging.error("Invalid request: Missing required fields")
            return "Invalid request: Missing required fields", 400

        logging.info(f"AppSheet File Path: {appsheet_file_path}")

        # ファイル名をURLからパース
        file_name = parse_filename_from_url(appsheet_file_path)
        if not file_name:
            return "Failed to parse file name from URL", 400

        # MIMEタイプをファイル名から決定
        mime_type = get_mime_type(file_name)

        # AppSheetのファイルURLからファイルをダウンロード
        file_content = download_file_from_appsheet(appsheet_file_path)
        if not file_content:
            logging.error("Failed to download file from AppSheet")
            return "Failed to download file from AppSheet", 404

        # ファイル名のサニタイズ
        safe_file_name = sanitize_filename(file_name)
        logging.info(f"Sanitized file name: {safe_file_name}")

        # Cloud Storageにバイナリデータとしてアップロード
        bucket_name = os.getenv('BUCKET_NAME')
        if not bucket_name:
            logging.error("Environment variable BUCKET_NAME is not set")
            return "Environment variable BUCKET_NAME is not set", 500

        storage_client = storage.Client()
        bucket = storage_client.bucket(bucket_name)

        # ユーザーID配下のすべてのファイルを削除
        delete_existing_files(bucket, key_value)

        # 新しいファイルをアップロード
        logging.info(f"Uploading file to Cloud Storage bucket '{bucket_name}' at path '{key_value}/{safe_file_name}'")
        blob = bucket.blob(f"{key_value}/{safe_file_name}")
        blob.upload_from_string(file_content, content_type=mime_type)  # MIMEタイプを指定してアップロード
        logging.info(f"File uploaded to Cloud Storage: gs://{bucket_name}/{key_value}/{safe_file_name}")

        # アップロードしたファイルのパスを生成
        cloud_storage_path = f"gs://{bucket_name}/{key_value}/{safe_file_name}"

        logging.info("AppSheet table updated successfully")
        return {"status": "success", dest_column_name: cloud_storage_path}, 200

    except Exception as e:
        logging.error(f"An error occurred: {e}", exc_info=True)
        return {"error": str(e)}, 500

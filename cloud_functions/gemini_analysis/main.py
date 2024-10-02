import os
import logging
from google.cloud import storage
import vertexai
from vertexai.generative_models import GenerativeModel, Part
from flask import Request
import functions_framework
import google.cloud.logging
import logging

# Cloud Loggingの初期化
logging_client = google.cloud.logging.Client()
logging_client.setup_logging()

# MIMEタイプの判定


def get_mime_type(file_uri):
    if file_uri.endswith(".mp4"):
        return "video/mp4"
    elif file_uri.endswith(".pdf"):
        return "application/pdf"
    elif file_uri.endswith(".csv"):
        return "text/csv"
    else:
        return "application/octet-stream"

# GCSからCSVファイルを取得する関数


def list_csv_files_in_gcs(bucket_name, target_path):
    try:
        logging.info(
            f"Fetching CSV files from GCS path: {target_path} in bucket: {bucket_name}")
        storage_client = storage.Client()
        bucket = storage_client.bucket(bucket_name)
        blobs = bucket.list_blobs(prefix=target_path)

        csv_files = [
            f"gs://{bucket_name}/{blob.name}" for blob in blobs if blob.name.endswith(".csv")]
        logging.info(f"CSV files found: {csv_files}")
        return csv_files
    except Exception as e:
        logging.error(f"Error listing CSV files: {e}")
        raise

# HTTPトリガー用のメイン関数
@functions_framework.http
def main(request: Request):
    try:
        logging.info(f"Received request: {request}")
        request_json = request.get_json(silent=True)
        if not request_json:
            logging.error("No JSON data provided in the request")
            return "Invalid request: No JSON data provided", 400

        # Prompt and analysis_id are required parameters
        prompt = request_json.get("prompt")
        analysis_id = request_json.get("analysis_id")

        logging.info(f"Prompt: {prompt}")
        logging.info(f"Analysis ID: {analysis_id}")

        if not all([prompt, analysis_id]):
            return "Invalid request: Missing prompt or analysis_id", 400

        # Vertex AIの初期化
        project_id = os.getenv("PROJECT_ID")
        vertexai.init(project=project_id, location="asia-northeast1")
        logging.info(f"Vertex AI initialized with project: {project_id}")

        contents = [prompt]

        # Handle strength PDF if strength_flag is "Y"
        strength_flag = request_json.get("strength_flag", "N")
        if strength_flag == "Y":
            strength_pdf_path = request_json.get("strength_pdf_path")
            if not strength_pdf_path:
                logging.error("Missing strength_pdf_path in the request")
                return "Invalid request: Missing strength_pdf_path", 400

            logging.info(
                f"Strength PDF Path from Cloud Storage: {strength_pdf_path}")
            mime_type = get_mime_type(strength_pdf_path)
            pdf_part = Part.from_uri(strength_pdf_path, mime_type=mime_type)
            contents.append(pdf_part)

        # Handle CSV files if analysis_target is "slack messages"
        analysis_target = request_json.get("analysis_target")
        if analysis_target == "slack messages":
            logging.info(
                f"Listing CSV files in GCS path: {request_json.get('target_file_path')}")
            csv_file_uris = list_csv_files_in_gcs(
                os.getenv("GCS_BUCKET_NAME"), request_json.get("target_file_path"))
            if not csv_file_uris:
                logging.error(
                    f"No CSV files found at the specified path: {request_json.get('target_file_path')}")
                return "No CSV files found at the specified path", 404

            for csv_uri in csv_file_uris:
                mime_type = get_mime_type(csv_uri)
                part = Part.from_uri(csv_uri, mime_type=mime_type)
                contents.append(part)

        # Set up generation model configuration
        generation_config = {
            "temperature": 0,
            "top_p": 0.95,
            "top_k": 40,
            "candidate_count": 1,
            "max_output_tokens": 8192,
        }
        logging.info(f"Generation Config: {generation_config}")

        model = GenerativeModel(
            "gemini-1.5-pro", generation_config=generation_config)

        # Generate content
        logging.info(f"Generating content with prompt and files: {contents}")
        response = model.generate_content(contents)

        logging.info("Content generated successfully")
        return {"status": "success", "generated_content": response.text}, 200

    except Exception as e:
        logging.error(f"An error occurred: {e}", exc_info=True)
        return {"error": str(e)}, 500

import functions_framework
import requests
import pandas as pd
from google.cloud import bigquery
from google.cloud import secretmanager
from datetime import datetime, timedelta, timezone
import os
import logging
import google.cloud.logging
import json

# Cloud Loggingクライアントの初期化とロギングの設定
logging_client = google.cloud.logging.Client()
logging_client.setup_logging()

# シークレットマネージャーからシークレットを取得する関数


def get_secret(secret_name, project_id):
    try:
        client = secretmanager.SecretManagerServiceClient()
        secret_path = f"projects/{project_id}/secrets/{secret_name}/versions/latest"
        response = client.access_secret_version(request={"name": secret_path})
        return response.payload.data.decode("UTF-8")
    except Exception as e:
        logging.error(f"Failed to retrieve secret: {e}")
        raise

# Cloud Functions のエントリポイント


@functions_framework.http
def main(request):
    try:
        logging.info(request)
        logging.info("Request data: %s", request.data)

        # リクエストから引数を取得
        request_json = request.get_json(silent=True)

        if request_json is None:
            logging.error("Invalid request: Missing parameters")
            return json.dumps({"status": "エラー", "export_path": None, "message": "リクエストパラメータが不足しています"}), 400

        logging.info("Received request parameters: %s", request_json)
        logging.info(request_json)

        # 環境変数から設定を取得
        project_id = os.getenv("GCP_PROJECT_ID")
        secret_name = os.getenv("SLACK_TOKEN_SECRET_NAME")
        dataset_id = os.getenv("BIGQUERY_DATASET_ID")
        table_id = os.getenv("BIGQUERY_TABLE_ID")
        slack_message_limit = int(os.getenv("SLACK_API_MESSAGE_LIMIT", 200))

        if not project_id or not secret_name or not dataset_id or not table_id:
            logging.error("Missing environment variables")
            return json.dumps({"status": "エラー", "export_path": None, "message": "環境変数が不足しています"}), 500

        # シークレットマネージャーからSlackトークンを取得
        try:
            slack_token = get_secret(secret_name, project_id)
        except Exception as e:
            logging.error(f"Failed to get Slack token: {e}")
            return json.dumps({"status": "エラー", "export_path": None, "message": "Slackトークンの取得に失敗しました"}), 500

        # channel_idsの処理
        channel_ids_str = request_json.get("channel_ids", "")
        channel_ids = [channel_id.strip()
                       for channel_id in channel_ids_str.split(",")]
        target_user_id = request_json.get("target_user_id")
        start_date_str = request_json.get("start_date")
        end_date_str = request_json.get("end_date")
        job_id = request_json.get("job_id")  # 新たに追加するjob_id

        logging.info(f"channel_ids: {channel_ids}")
        logging.info(f"target_user_id: {target_user_id}")
        logging.info(f"start_date_str: {start_date_str}")
        logging.info(f"end_date_str: {end_date_str}")
        logging.info(f"job_id: {job_id}")  # job_idのログ

        if not slack_token or not channel_ids or not target_user_id or not start_date_str or not end_date_str or not job_id:
            logging.error("Missing required parameters")
            return json.dumps({"status": "エラー", "export_path": None, "message": "必要なパラメータが不足しています"}), 400

        def convert_to_unix_timestamp_jst(date_str):
            jst = timezone(timedelta(hours=9))
            date_obj = datetime.strptime(date_str, '%Y/%m/%d')
            return int(datetime(date_obj.year, date_obj.month, date_obj.day, 0, 0, 0, tzinfo=jst).timestamp())

        try:
            start_time = convert_to_unix_timestamp_jst(start_date_str)
            end_time = convert_to_unix_timestamp_jst(end_date_str) + 86399
        except ValueError as e:
            logging.error(f"Invalid date format: {e}")
            return json.dumps({"status": "エラー", "export_path": None, "message": "無効な日付形式です"}), 400

        history_url = "https://slack.com/api/conversations.history"
        replies_url = "https://slack.com/api/conversations.replies"
        headers = {"Authorization": f"Bearer {slack_token}"}

        user_messages = []

        for channel_id in channel_ids:

            logging.info(f"channel_id: {channel_id}")

            has_more = True
            next_cursor = None

            while has_more:
                params = {
                    "channel": channel_id,
                    "oldest": start_time,
                    "latest": end_time,
                    "limit": slack_message_limit,
                }
                if next_cursor:
                    params["cursor"] = next_cursor

                try:
                    response = requests.get(
                        history_url, headers=headers, params=params)
                    data = response.json()
                except requests.RequestException as e:
                    logging.error(f"Failed to fetch Slack data: {e}")
                    return json.dumps({"status": "エラー", "export_path": None, "message": "Slackデータの取得に失敗しました"}), 500

                if data.get("ok"):
                    messages = data.get("messages", [])
                    for message in messages:
                        if message.get("user") == target_user_id:
                            message['channel_id'] = channel_id
                            user_messages.append(message)
                        if "thread_ts" in message:
                            thread_ts = message["thread_ts"]
                            thread_params = {
                                "channel": channel_id, "ts": thread_ts}
                            try:
                                thread_response = requests.get(
                                    replies_url, headers=headers, params=thread_params)
                                thread_data = thread_response.json()
                            except requests.RequestException as e:
                                logging.error(
                                    f"Failed to fetch thread messages: {e}")
                                continue

                            if thread_data.get("ok"):
                                thread_messages = thread_data.get(
                                    "messages", [])
                                for thread_message in thread_messages:
                                    if thread_message.get("user") == target_user_id:
                                        thread_message['channel_id'] = channel_id
                                        user_messages.append(thread_message)

                    has_more = data.get("has_more", False)
                    next_cursor = data.get(
                        "response_metadata", {}).get("next_cursor")
                else:
                    logging.error(f"Error from Slack API: {data.get('error')}")
                    return json.dumps({"status": "エラー", "export_path": None, "message": f"Slack APIエラー: {data.get('error')}"}), 500

        # メッセージが0件の場合は処理を中断して正常終了
        if len(user_messages) == 0:
            logging.info("No messages found for the given parameters")
            return json.dumps({"status": "成功", "export_path": None, "message": "メッセージが見つかりませんでした"}), 200

        sorted_messages = sorted(
            user_messages, key=lambda msg: float(msg['ts']))
        df = pd.DataFrame(sorted_messages)

        logging.info("Initial dataframe columns: %s", df.columns)
        logging.info("Initial dataframe sample: %s",
                     df.head().to_dict(orient="records"))

        def concatenate_reactions(reactions):
            if isinstance(reactions, list):
                return ', '.join([f"{reaction['name']}:{reaction['count']}" for reaction in reactions])
            return ''

        if 'reactions' in df.columns:
            df['reactions_concatenated'] = df['reactions'].apply(
                concatenate_reactions)
        else:
            logging.warning("'reactions' column not found in dataframe")
            df['reactions_concatenated'] = ''

        def convert_to_jst(timestamp):
            utc_dt = datetime.fromtimestamp(float(timestamp), timezone.utc)
            jst = timezone(timedelta(hours=9))
            return utc_dt.astimezone(jst).strftime('%Y-%m-%d %H:%M:%S')

        df['ts'] = df['ts'].apply(convert_to_jst)
        df.rename(columns={'user': 'user_id'}, inplace=True)
        df_cleaned = df[['ts', 'user_id', 'text',
                         'reactions_concatenated', 'channel_id']].drop_duplicates()

        # job_idを追加
        df_cleaned['job_id'] = job_id

        client = bigquery.Client()

        table_ref = f"{client.project}.{dataset_id}.{table_id}"
        job_config = bigquery.LoadJobConfig(
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND)

        try:
            job = client.load_table_from_dataframe(
                df_cleaned, table_ref, job_config=job_config)
            job.result()
        except Exception as e:
            logging.error(f"Failed to load data to BigQuery: {e}")
            return json.dumps({"status": "エラー", "export_path": None, "message": "BigQueryへのデータ書き込みに失敗しました"}), 500

        logging.info("Total processed messages: %d", len(user_messages))
        logging.info("Total rows in dataframe: %d", len(df_cleaned))

        response = {"status": "実行中", "export_path": table_ref,
                    "message": f"中間データ作成が完了しました。{len(df_cleaned)} 件のメッセージをアップロードしました。"}
        return json.dumps(response), 200

    except Exception as e:
        logging.error(f"Unexpected error occurred: {e}")
        return json.dumps({"status": "エラー", "export_path": None, "message": "予期しないエラーが発生しました"}), 500

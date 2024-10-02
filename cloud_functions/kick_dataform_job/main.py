# -*- coding: utf-8 -*-
from google.cloud import dataform_v1beta1, tasks_v2, secretmanager
from flask import jsonify
import functions_framework
import os
from google.protobuf import timestamp_pb2
import json
import time
from datetime import datetime
import requests
import google.cloud.logging
import logging

# Cloud Loggingクライアントの初期化とロギングの設定
logging_client = google.cloud.logging.Client()
logging_client.setup_logging()

# シークレットマネージャーからAPIキーを取得する関数


def get_secret(secret_name):
    try:
        client = secretmanager.SecretManagerServiceClient()
        project_id = os.getenv('GCP_PROJECT_ID')
        secret_path = f"projects/{project_id}/secrets/{secret_name}/versions/latest"

        # シークレットの値を取得
        response = client.access_secret_version(name=secret_path)
        logging.info(f"Secret fetched for {secret_name}.")
        return response.payload.data.decode('UTF-8')
    except Exception as e:
        logging.error(f"Failed to retrieve secret: {e}")
        raise

# AppSheetの特定の行を更新する関数 (job_idをPKとして使用)


def update_appsheet_job_data(job_id, export_path, message):
    try:
        # 環境変数からAPP_IDを取得
        app_id = os.getenv('APP_ID')
        logging.info(
            f"Updating AppSheet job data for job_id: {job_id}, export_path: {export_path}, message: {message}")

        # シークレットマネージャーからAPPSHEET_API_KEYを取得
        api_key = get_secret('appsheet-api-key')

        url = f'https://api.appsheet.com/api/v2/apps/{app_id}/tables/job/records'
        logging.info(f"AppSheet API URL: {url}")

        headers = {
            'ApplicationAccessKey': api_key,
            'Content-Type': 'application/json',
        }
        logging.info(f"Headers: {headers}")

        # export_pathとmessageを一度に更新
        payload = {
            "Action": "Edit",
            "Properties": {
                "Locale": "en-US",
                "Location": "47.6154086,-122.3349685",
                "Timezone": "Pacific Standard Time"
            },
            "Rows": [
                {
                    "job_id": job_id,  # 更新したいレコードのjob_id (PK)
                    "export_path": export_path,  # Dataformのexport_pathをセット
                    "message": message,  # 更新するmessageカラム
                    "status": "成功"  # 更新するstatusカラム
                }
            ]
        }

        logging.info(f"Payload: {json.dumps(payload, indent=2)}")

        # AppSheet APIを呼び出してデータを更新
        response = requests.post(url, headers=headers, json=payload)

        if response.status_code == 200:
            logging.info(
                f"Successfully updated row with job_id {job_id} in AppSheet.")
        else:
            logging.error(
                f"Failed to update row with job_id {job_id}. Status code: {response.status_code}")
            logging.error(response.text)
    except Exception as e:
        logging.error(f"Failed to update AppSheet: {e}")
        raise

# Dataformジョブをキックするエンドポイント


@functions_framework.http
def kick_dataform_job(request):
    try:
        logging.info("Received request")

        # リクエストからparentとworkspace、job_idの値を取得
        request_data = request.get_json()
        logging.info(
            f"Received request data: {json.dumps(request_data, indent=2)}")

        parent = request_data.get('parent')
        workspace = request_data.get('workspace')
        # job_idはリクエストパラメータから取得、ない場合は"ALL"をデフォルト値とする
        job_id = request_data.get('job_id', 'ALL')

        logging.info(
            f"Parent: {parent}, Workspace: {workspace}, Job ID: {job_id}")

        if not parent or not workspace:
            return jsonify({'error': 'Missing required parameters: parent or workspace'}), 400

        # export_pathの生成（例: /exports/job123_202409221234567/*.csv）
        timestamp_str = datetime.now().strftime('%Y%m%d%H%M%S%f')[:-3]
        export_path = f"exports/{job_id}_{timestamp_str}"

        logging.info(f"Generated export path: {export_path}")

        # Dataformクライアントの作成
        client = dataform_v1beta1.DataformClient()

        # コンパイル変数としてjob_idとexport_pathを渡す
        code_compilation_config = dataform_v1beta1.CodeCompilationConfig(
            vars={
                "job_id": job_id,  # リクエストから受け取ったjob_id（デフォルトは"ALL"）
                "export_path": export_path  # 動的に生成されたexport_pathを設定
            }
        )

        # コンパイル結果の作成
        create_compilation_request = dataform_v1beta1.CreateCompilationResultRequest(
            parent=parent,
            compilation_result=dataform_v1beta1.CompilationResult(
                workspace=workspace,
                code_compilation_config=code_compilation_config
            )
        )

        # CompilationResults.createリクエストを送信し、コンパイル結果を作成
        compilation_result_response = client.create_compilation_result(
            request=create_compilation_request)
        compilation_result_id = compilation_result_response.name
        logging.info(f"Compilation result ID: {compilation_result_id}")

        # WorkflowInvocationの初期化
        workflow_invocation = dataform_v1beta1.WorkflowInvocation(
            compilation_result=compilation_result_id  # コンパイル結果を使用
        )

        # WorkflowInvocationリクエストの初期化
        create_workflow_request = dataform_v1beta1.CreateWorkflowInvocationRequest(
            parent=parent,
            workflow_invocation=workflow_invocation,
        )

        # Dataformジョブをキック
        response = client.create_workflow_invocation(
            request=create_workflow_request)
        workflow_invocation_name = response.name
        logging.info(f"Workflow invocation name: {workflow_invocation_name}")

        # Cloud Tasksクライアントの作成
        tasks_client = tasks_v2.CloudTasksClient()
        project = os.getenv('GCP_PROJECT_ID')  # プロジェクトIDを環境変数から取得
        location = os.getenv('REGION')  # リージョンを環境変数から取得
        queue = "dataform-completion-checker"  # Cloud Tasksのキュー名
        task_url = os.getenv('POLL_FUNCTION_URL')  # poll_dataform_jobのURL

        logging.info(
            f"Task details - Project: {project}, Location: {location}, Queue: {queue}, Task URL: {task_url}")

        # タスクの作成
        parent_path = tasks_client.queue_path(project, location, queue)
        task = {
            "http_request": {
                "http_method": tasks_v2.HttpMethod.POST,
                "url": task_url,  # poll_dataform_jobエンドポイント
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({
                    "parent": parent,
                    "workflow_invocation_name": workflow_invocation_name,
                    "job_id": job_id,  # AppSheetのjob_idを指定
                    "export_path": export_path
                }).encode()
            }
        }

        # 30秒後に実行するタスクをスケジュール
        scheduled_time = timestamp_pb2.Timestamp()
        scheduled_time.FromSeconds(int(time.time() + 30))
        task['schedule_time'] = scheduled_time

        # タスクをキューに追加
        tasks_client.create_task(parent=parent_path, task=task)
        logging.info(f"Task created successfully with name: {task_url}")

        # レスポンスを返す
        return jsonify({'message': 'Dataform job kicked successfully, polling scheduled', 'workflow_invocation_name': workflow_invocation_name}), 200

    except Exception as e:
        logging.error(f"Error in kick_dataform_job: {e}")
        return jsonify({'error': str(e)}), 500

# Dataformジョブの完了をポーリングするエンドポイント


@functions_framework.http
def poll_dataform_job(request):
    try:
        logging.info("Polling Dataform job")

        # リクエストからparent、workflow_invocation_name、job_id、export_pathの値を取得
        request_data = request.get_json()
        logging.info(
            f"Received request data for polling: {json.dumps(request_data, indent=2)}")

        parent = request_data.get('parent')
        workflow_invocation_name = request_data.get('workflow_invocation_name')
        job_id = request_data.get('job_id')  # AppSheetのjob_id
        export_path = request_data.get('export_path')  # Dataformのexport_path

        if not parent or not workflow_invocation_name or not job_id or not export_path:
            logging.error("Missing required parameters in poll_dataform_job")
            return jsonify({'error': 'Missing required parameters: parent, workflow_invocation_name, job_id, or export_path'}), 400

        # Dataformクライアントの作成
        client = dataform_v1beta1.DataformClient()

        # ワークフローのステータスを取得
        response = client.get_workflow_invocation(
            name=workflow_invocation_name)

        # ステータスのチェック
        logging.info(f"Current job status: {response.state}")

        if response.state == dataform_v1beta1.WorkflowInvocation.State.SUCCEEDED:
            message = 'ジョブが正常に完了しました'
            update_appsheet_job_data(job_id, export_path, message)
            logging.info(
                f"Dataform job completed successfully for workflow_invocation_name: {workflow_invocation_name}")
            return jsonify({'status': 'COMPLETED', 'workflow_invocation_name': workflow_invocation_name}), 200
        elif response.state == dataform_v1beta1.WorkflowInvocation.State.FAILED:
            logging.error(
                f"Dataform job failed for workflow_invocation_name: {workflow_invocation_name}")
            return jsonify({'status': 'FAILED', 'workflow_invocation_name': workflow_invocation_name}), 200
        else:
            logging.info(
                f"Dataform job is still pending for workflow_invocation_name: {workflow_invocation_name}")
            return jsonify({'status': 'PENDING', 'workflow_invocation_name': workflow_invocation_name}), 200

    except Exception as e:
        logging.error(f"Error in poll_dataform_job: {e}")
        return jsonify({'error': str(e)}), 500

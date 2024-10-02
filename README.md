# ai-hackathon-strength-2024

AI Hackathon with Google Cloud 向けに組織内個人の強みを活かすAIマネジメントツール

## 概要

本プロジェクトは、Slackメッセージデータ、感情分析、生成AI（Gemini）を活用し、組織内個人の強みを活かすための分析・可視化ツールを構築することを目的としています。
ストレングスファインダー等のビジネス心理テストの結果をコンテキスト情報として活用することで、よりパーソナライズされた分析を実現し、マネージャーの業務負担軽減と個人のキャリア開発支援を目指します。

## システムアーキテクチャ

- **データソース:** Slack
- **データ収集:** Cloud Functions を利用して Slack メッセージを BigQuery に格納
- **データ分析:** 
    - BigQuery のリモート関数で感情分析を実施
    - Dataform で BigQuery 内のデータパイプラインと分析クエリを管理
- **生成AI分析:** Gemini を使用して、ストレングスファインダーの結果を考慮しつつメッセージを分析
- **UI/UX:** AppSheet を用いて分析結果を表示・活用
- **インフラストラクチャ:** Terraform でインフラをコード化 (IaC)

## 主要技術

- **Google Cloud Platform:**
    - BigQuery: データウェアハウス
    - Cloud Functions: サーバーレスコンピューティング
    - Dataform: データパイプライン管理
    - Gemini: 生成AI
- **その他:**
    - Slack: メッセージングプラットフォーム
    - AppSheet: ノーコードアプリケーション開発プラットフォーム
    - Terraform: インフラストラクチャ自動化ツール

## コード構成

- `cloud_functions`: 各 Cloud Functions のソースコードと依存関係
    - `download_file_from_drive`: Google Drive からファイルをダウンロードする関数
    - `gemini_analysis`: Gemini API を利用した分析を行う関数
    - `kick_dataform_job`: Dataform ジョブを実行する関数
    - `slack_messages_to_bigquery`: Slack メッセージを BigQuery にロードする関数
- `dataform`: Dataform の設定ファイルと SQLX ファイル
    - `definitions`: Dataform の定義ファイル（テーブル定義など）
    - `workflow_settings.yaml`: Dataform のワークフロー設定ファイル
- `function_source`: デプロイ用の関数ソースコードの zip ファイル
- `main.tf`, `variables.tf`: Terraform の設定ファイル

## 環境構築

1. **Google Cloud Project の作成:** Google Cloud Platform で新しいプロジェクトを作成します。
2. **必要な API の有効化:** BigQuery, Cloud Functions, Dataform, Gemini などの API を有効化します。
3. **Terraform のインストール:** Terraform をインストールし、Google Cloud プロバイダーを構成します。
4. **インフラストラクチャのデプロイ:** `terraform apply` コマンドを実行して、BigQuery データセット、Cloud Functions、Dataform リポジトリなどのインフラストラクチャをデプロイします。
5. **Cloud Functions のデプロイ:** 各 Cloud Functions を `gcloud functions deploy` コマンドでデプロイします。
6. **AppSheet アプリの作成:** AppSheet を使用して、BigQuery データに接続し、分析結果を表示するアプリケーションを作成します。

## 今後の開発

- より多くのユーザーデータの収集と分析精度の向上
- 他のビジネス心理テストや評価指標との連携
- リアルタイムなフィードバックシステムの構築
- AI による個別化されたキャリア開発提案機能の追加
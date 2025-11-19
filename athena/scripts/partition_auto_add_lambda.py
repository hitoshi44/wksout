"""
AWS Lambda: S3イベントトリガーでAthenaパーティション自動追加

【概要】
S3に新しいログファイルがアップロードされたら、
自動的にAthenaテーブルにパーティションを追加する。

【設定】
1. Lambda関数作成:
   - Runtime: Python 3.11
   - Timeout: 60秒
   - Memory: 256MB

2. IAM Role（Lambda実行ロール）:
   - AmazonS3ReadOnlyAccess
   - AmazonAthenaFullAccess
   - AWSGlueConsoleFullAccess（Glue Data Catalog書き込み）

3. S3イベントトリガー:
   - Event type: s3:ObjectCreated:*
   - Prefix: application/
   - Suffix: .log

4. 環境変数:
   - ATHENA_DATABASE: データベース名（例: logs_db）
   - ATHENA_TABLE: テーブル名（例: application_logs）
   - ATHENA_OUTPUT_LOCATION: クエリ結果保存先（例: s3://athena-results/）

【動作フロー】
S3イベント → Lambda起動 → パスからパーティション抽出 →
ALTER TABLE実行 → パーティション追加完了
"""

import os
import re
import json
import boto3
from urllib.parse import unquote_plus
from typing import Dict, Tuple, Optional

# Boto3クライアント初期化
athena_client = boto3.client('athena')
s3_client = boto3.client('s3')

# 環境変数から設定取得
ATHENA_DATABASE = os.environ.get('ATHENA_DATABASE', 'logs_db')
ATHENA_TABLE = os.environ.get('ATHENA_TABLE', 'application_logs')
ATHENA_OUTPUT_LOCATION = os.environ.get('ATHENA_OUTPUT_LOCATION', 's3://athena-results/')


def extract_partition_from_path(s3_key: str) -> Optional[Tuple[str, str, str]]:
    """
    S3キーからHive形式のパーティション情報を抽出

    Args:
        s3_key: S3オブジェクトキー
                例: 'application/year=2025/month=01/day=15/file.log'

    Returns:
        (year, month, day) のタプル、または抽出失敗時はNone
    """
    # Hive形式のパターンマッチング
    pattern = r'year=(\d{4})/month=(\d{2})/day=(\d{2})/'
    match = re.search(pattern, s3_key)

    if match:
        year, month, day = match.groups()
        return (year, month, day)

    print(f"⚠️  Path doesn't match Hive format: {s3_key}")
    return None


def partition_exists(database: str, table: str, partition_values: Dict[str, str]) -> bool:
    """
    パーティションが既に存在するか確認

    Args:
        database: データベース名
        table: テーブル名
        partition_values: パーティション値の辞書 {'year': '2025', 'month': '01', 'day': '15'}

    Returns:
        存在する場合True、存在しない場合False
    """
    glue_client = boto3.client('glue')

    try:
        response = glue_client.get_partition(
            DatabaseName=database,
            TableName=table,
            PartitionValues=list(partition_values.values())
        )
        return True
    except glue_client.exceptions.EntityNotFoundException:
        return False
    except Exception as e:
        print(f"⚠️  Error checking partition: {e}")
        return False


def add_partition(
    bucket: str,
    database: str,
    table: str,
    year: str,
    month: str,
    day: str
) -> str:
    """
    Athenaテーブルにパーティションを追加

    Args:
        bucket: S3バケット名
        database: データベース名
        table: テーブル名
        year: 年
        month: 月（2桁）
        day: 日（2桁）

    Returns:
        Athena QueryExecutionId
    """
    # パーティション追加クエリ（IF NOT EXISTSで冪等性確保）
    location = f"s3://{bucket}/application/year={year}/month={month}/day={day}/"

    query = f"""
    ALTER TABLE {database}.{table}
    ADD IF NOT EXISTS PARTITION (year='{year}', month='{month}', day='{day}')
    LOCATION '{location}';
    """

    print(f"🔧 Executing query:\n{query}")

    # Athenaクエリ実行
    response = athena_client.start_query_execution(
        QueryString=query,
        QueryExecutionContext={'Database': database},
        ResultConfiguration={
            'OutputLocation': ATHENA_OUTPUT_LOCATION
        }
    )

    query_execution_id = response['QueryExecutionId']
    print(f"✅ Query submitted: {query_execution_id}")

    return query_execution_id


def wait_for_query_completion(query_execution_id: str, max_wait: int = 30) -> str:
    """
    Athenaクエリの完了を待つ（オプション）

    Args:
        query_execution_id: クエリ実行ID
        max_wait: 最大待機秒数

    Returns:
        クエリの最終ステータス（SUCCEEDED, FAILED, CANCELLED）
    """
    import time

    waited = 0
    while waited < max_wait:
        response = athena_client.get_query_execution(
            QueryExecutionId=query_execution_id
        )
        status = response['QueryExecution']['Status']['State']

        if status in ['SUCCEEDED', 'FAILED', 'CANCELLED']:
            return status

        time.sleep(1)
        waited += 1

    return 'TIMEOUT'


def lambda_handler(event, context):
    """
    Lambda関数のエントリーポイント

    Args:
        event: S3イベント
        context: Lambdaコンテキスト

    Returns:
        レスポンス辞書
    """
    print(f"📥 Received event: {json.dumps(event, indent=2)}")

    # S3イベントから情報抽出
    try:
        record = event['Records'][0]
        bucket = record['s3']['bucket']['name']
        key = unquote_plus(record['s3']['object']['key'])

        print(f"📄 Processing: s3://{bucket}/{key}")

    except (KeyError, IndexError) as e:
        print(f"❌ Invalid event structure: {e}")
        return {
            'statusCode': 400,
            'body': json.dumps({'error': 'Invalid S3 event'})
        }

    # パーティション情報を抽出
    partition_info = extract_partition_from_path(key)

    if not partition_info:
        print(f"⚠️  Skipping non-partitioned file: {key}")
        return {
            'statusCode': 200,
            'body': json.dumps({'message': 'Not a partitioned file, skipped'})
        }

    year, month, day = partition_info
    print(f"📅 Partition: year={year}, month={month}, day={day}")

    # パーティション存在チェック（オプション、コメントアウト可）
    partition_values = {'year': year, 'month': month, 'day': day}
    if partition_exists(ATHENA_DATABASE, ATHENA_TABLE, partition_values):
        print(f"ℹ️  Partition already exists, skipping")
        return {
            'statusCode': 200,
            'body': json.dumps({'message': 'Partition already exists'})
        }

    # パーティション追加
    try:
        query_execution_id = add_partition(
            bucket=bucket,
            database=ATHENA_DATABASE,
            table=ATHENA_TABLE,
            year=year,
            month=month,
            day=day
        )

        # オプション: クエリ完了を待つ
        # status = wait_for_query_completion(query_execution_id)
        # print(f"📊 Query status: {status}")

        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'Partition added successfully',
                'partition': {'year': year, 'month': month, 'day': day},
                'queryExecutionId': query_execution_id
            })
        }

    except Exception as e:
        print(f"❌ Error adding partition: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }


# ローカルテスト用
if __name__ == '__main__':
    # テストイベント
    test_event = {
        'Records': [{
            's3': {
                'bucket': {'name': 'my-logs-bucket'},
                'object': {'key': 'application/year=2025/month=01/day=15/test.log'}
            }
        }]
    }

    # 環境変数設定（ローカルテスト用）
    os.environ['ATHENA_DATABASE'] = 'logs_db'
    os.environ['ATHENA_TABLE'] = 'application_logs'
    os.environ['ATHENA_OUTPUT_LOCATION'] = 's3://athena-results/'

    result = lambda_handler(test_event, None)
    print(f"\n📊 Result: {json.dumps(result, indent=2)}")

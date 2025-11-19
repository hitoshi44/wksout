-- =====================================================
-- AWS Athena テーブル作成DDL
-- カスタムログ形式（XXX TIMESTAMP <JSON>）用
-- =====================================================

-- データベース作成（初回のみ）
CREATE DATABASE IF NOT EXISTS logs_db
COMMENT 'Application logs database'
LOCATION 's3://my-logs-bucket/';


-- =====================================================
-- 1. CSV/テキスト形式のログテーブル（基本版）
-- =====================================================

CREATE EXTERNAL TABLE IF NOT EXISTS logs_db.application_logs (
  log_level STRING COMMENT 'ログレベル（INFO, ERROR, WARN等）',
  timestamp STRING COMMENT 'タイムスタンプ',
  json_data STRING COMMENT 'JSON部分（文字列として保存）'
)
COMMENT 'Application logs with custom format: XXX TIMESTAMP <JSON>'
PARTITIONED BY (
  year STRING COMMENT 'Year partition',
  month STRING COMMENT 'Month partition (01-12)',
  day STRING COMMENT 'Day partition (01-31)'
)
ROW FORMAT SERDE 'org.apache.hadoop.hive.serde2.RegexSerDe'
WITH SERDEPROPERTIES (
  "input.regex" = "^(\\w+) +(\\S+) +(.*)$"
  -- キャプチャグループの説明:
  -- グループ1: (\w+)      -> log_level（英数字とアンダースコア）
  -- グループ2: (\S+)      -> timestamp（非空白文字）
  -- グループ3: (.*)       -> json_data（残り全て）
)
STORED AS TEXTFILE
LOCATION 's3://my-logs-bucket/application/'
TBLPROPERTIES (
  'has_encrypted_data'='false',
  'projection.enabled'='false'
);

-- パーティション追加例（手動）
-- ALTER TABLE logs_db.application_logs
-- ADD PARTITION (year='2025', month='01', day='15')
-- LOCATION 's3://my-logs-bucket/application/year=2025/month=01/day=15/';

-- パーティション自動検出（Hive形式の場合）
-- MSCK REPAIR TABLE logs_db.application_logs;


-- =====================================================
-- 2. Parquet形式のログテーブル（最適化版）
-- =====================================================

-- CTAS（CREATE TABLE AS SELECT）でCSV → Parquet変換
-- 実行前に上記のapplication_logsテーブルにデータが必要

CREATE TABLE logs_db.application_logs_parquet
WITH (
  format = 'PARQUET',
  parquet_compression = 'SNAPPY',
  partitioned_by = ARRAY['year', 'month', 'day'],
  external_location = 's3://my-logs-bucket/parquet/'
)
AS
SELECT
  log_level,
  timestamp,
  -- JSONフィールドを事前に抽出（クエリ時のパース不要）
  json_extract_scalar(json_data, '$.user_id') AS user_id,
  json_extract_scalar(json_data, '$.action') AS action,
  json_extract_scalar(json_data, '$.status') AS status,
  json_extract_scalar(json_data, '$.error') AS error_message,
  -- 必要に応じてフィールドを追加
  year,
  month,
  day
FROM logs_db.application_logs
WHERE year='2025' AND month='01' AND day='15'; -- 初回は1日分のみ


-- =====================================================
-- 3. Partition Projection版（高度な設定）
-- =====================================================

-- 大量パーティション（数十万以上）の場合に推奨
-- メタデータアクセス不要で高速化

CREATE EXTERNAL TABLE IF NOT EXISTS logs_db.application_logs_projected (
  log_level STRING,
  timestamp STRING,
  json_data STRING
)
PARTITIONED BY (
  year STRING,
  month STRING,
  day STRING
)
ROW FORMAT SERDE 'org.apache.hadoop.hive.serde2.RegexSerDe'
WITH SERDEPROPERTIES (
  "input.regex" = "^(\\w+) +(\\S+) +(.*)$"
)
STORED AS TEXTFILE
LOCATION 's3://my-logs-bucket/application/'
TBLPROPERTIES (
  -- Partition Projection有効化
  'projection.enabled' = 'true',

  -- yearパーティションの設定
  'projection.year.type' = 'integer',
  'projection.year.range' = '2020,2030',
  'projection.year.digits' = '4',

  -- monthパーティションの設定
  'projection.month.type' = 'integer',
  'projection.month.range' = '1,12',
  'projection.month.digits' = '2',

  -- dayパーティションの設定
  'projection.day.type' = 'integer',
  'projection.day.range' = '1,31',
  'projection.day.digits' = '2',

  -- S3ロケーションテンプレート
  'storage.location.template' = 's3://my-logs-bucket/application/year=${year}/month=${month}/day=${day}'
);


-- =====================================================
-- クエリ例
-- =====================================================

-- 1. 基本的なエラーログ検索
SELECT
  log_level,
  timestamp,
  json_extract_scalar(json_data, '$.user_id') AS user_id,
  json_extract_scalar(json_data, '$.action') AS action,
  json_extract_scalar(json_data, '$.error') AS error_message
FROM logs_db.application_logs
WHERE year='2025' AND month='01' AND day='15'
  AND log_level = 'ERROR'
ORDER BY timestamp DESC
LIMIT 100;


-- 2. 日別エラー件数の集計
SELECT
  year, month, day,
  log_level,
  COUNT(*) AS error_count
FROM logs_db.application_logs
WHERE year='2025' AND month='01'
  AND log_level IN ('ERROR', 'WARN')
GROUP BY year, month, day, log_level
ORDER BY year, month, day;


-- 3. ユーザーアクション分析
SELECT
  json_extract_scalar(json_data, '$.user_id') AS user_id,
  json_extract_scalar(json_data, '$.action') AS action,
  COUNT(*) AS action_count
FROM logs_db.application_logs
WHERE year='2025' AND month='01' AND day='15'
GROUP BY
  json_extract_scalar(json_data, '$.user_id'),
  json_extract_scalar(json_data, '$.action')
ORDER BY action_count DESC
LIMIT 20;


-- =====================================================
-- メンテナンスクエリ
-- =====================================================

-- パーティション一覧表示
SHOW PARTITIONS logs_db.application_logs;

-- テーブル定義確認
SHOW CREATE TABLE logs_db.application_logs;

-- テーブル削除（メタデータのみ、S3データは削除されない）
-- DROP TABLE logs_db.application_logs;

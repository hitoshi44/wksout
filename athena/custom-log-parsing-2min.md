# カスタムログ形式（XXX TIMESTAMP <JSON>）のAthena対応（2分版）

## 課題

現行ログ形式 `XXX TIMESTAMP <JSON>` をAthenaで認識・クエリできるようにする。

**ログ例:**
```
INFO 2025-01-20T10:30:45Z {"user_id": 12345, "action": "login", "status": "success"}
ERROR 2025-01-20T11:15:22Z {"user_id": 67890, "error": "connection_timeout", "retry": 3}
```

---

## 推奨アプローチ: 2段階パース

### 方法1: RegexSerDe + json_extract()（推奨）

**ステップ1: RegexSerDeでプレフィックスとJSONを分離**

```sql
CREATE EXTERNAL TABLE application_logs (
  log_level STRING,
  timestamp STRING,
  json_data STRING  -- JSON部分を文字列として保存
)
PARTITIONED BY (year STRING, month STRING, day STRING)
ROW FORMAT SERDE 'org.apache.hadoop.hive.serde2.RegexSerDe'
WITH SERDEPROPERTIES (
  "input.regex" = "^(\\w+) +(\\S+) +(.*)$"
  -- グループ1: log_level（INFO, ERROR等）
  -- グループ2: timestamp
  -- グループ3: json_data（JSON全体）
)
STORED AS TEXTFILE
LOCATION 's3://my-logs-bucket/application/';
```

**ステップ2: クエリ時にJSONをパース**

```sql
SELECT
  log_level,
  timestamp,
  json_extract_scalar(json_data, '$.user_id') AS user_id,
  json_extract_scalar(json_data, '$.action') AS action,
  json_extract_scalar(json_data, '$.status') AS status,
  json_extract_scalar(json_data, '$.error') AS error_message
FROM application_logs
WHERE year='2025' AND month='01' AND day='20'
  AND log_level = 'ERROR';
```

**メリット:**
- ログ形式が変わってもテーブル定義不要
- JSON内のフィールド追加に柔軟対応
- クエリごとに必要なフィールドだけ抽出

**デメリット:**
- 毎回JSONパースでやや遅い → Parquet化で解決

---

## パフォーマンス改善: 事前パース

頻繁にクエリする場合、日次でパース済みテーブルを作成：

```sql
-- 日次バッチでパース済みテーブル作成
CREATE TABLE logs_parsed_daily
WITH (format = 'PARQUET', partitioned_by = ARRAY['year','month','day'])
AS
SELECT
  log_level,
  timestamp,
  json_extract_scalar(json_data, '$.user_id') AS user_id,
  json_extract_scalar(json_data, '$.action') AS action,
  json_extract_scalar(json_data, '$.status') AS status,
  json_extract_scalar(json_data, '$.error') AS error_message,
  year, month, day
FROM application_logs
WHERE year='2025' AND month='01' AND day='20';
```

**効果:**
- クエリ速度: 10倍以上高速化
- コスト: 90%削減（Parquet圧縮効果）

---

## 代替案（非推奨）

### 方法2: ログ形式をJSON Linesに変更

アプリケーション側でログ形式を変更：

**Before:**
```
INFO 2025-01-20T10:30:45Z {"user_id": 12345}
```

**After（JSON Lines形式）:**
```json
{"level":"INFO","timestamp":"2025-01-20T10:30:45Z","user_id":12345}
```

**テーブル定義:**
```sql
CREATE EXTERNAL TABLE application_logs (
  level STRING,
  timestamp STRING,
  user_id BIGINT,
  action STRING
)
PARTITIONED BY (year STRING, month STRING, day STRING)
ROW FORMAT SERDE 'org.openx.data.jsonserde.JsonSerDe'
LOCATION 's3://my-logs-bucket/application/';
```

**デメリット:**
- アプリケーション改修が必要
- 既存ログとの互換性なし

→ **現実的でないため非推奨**

---

## 制約事項

### RegexSerDe の制限

- ❌ 複数行にまたがるログは非対応
- ❌ JSON内に改行が含まれる場合は失敗
- ⚠️ 正規表現が複雑になるとメンテナンス困難

**対策:**
- ログは1行1レコードを徹底
- JSON は minify（改行なし）で出力

---

## 検証手順

1. **サンプルログで正規表現テスト**
   ```sql
   SELECT * FROM application_logs LIMIT 10;
   ```
   → すべてのカラムにデータが入っているか確認

2. **JSONパース動作確認**
   ```sql
   SELECT json_extract_scalar(json_data, '$.user_id') FROM application_logs LIMIT 5;
   ```
   → NULL が返る場合はJSON形式を確認

3. **本番ログで検証**
   - 1日分のログでテーブル作成
   - 各種クエリパターンを試す
   - パフォーマンス・コスト測定

---

## まとめ

| 項目 | 推奨方法 |
|------|---------|
| **テーブル定義** | RegexSerDe でプレフィックス分離 |
| **JSON処理** | クエリ時に `json_extract_scalar()` |
| **高速化** | 日次でParquetパース済みテーブル作成 |
| **制約** | 1行1レコード、JSON内改行禁止 |

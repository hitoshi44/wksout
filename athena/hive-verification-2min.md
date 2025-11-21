# Athena Hive形式 動作検証ガイド（2分版）

## 検証目的

Hive形式のパーティショニングが正しく機能するか、実データでの動作を確認する。

---

## 検証ステップ（3ステップ）

### Step 1: サンプルデータ配置

最小構成でHive形式を試す：

```bash
# S3にサンプルログ配置
s3://my-test-bucket/logs/
  year=2025/
    month=01/
      day=20/
        test-001.log  # 内容: "INFO 2025-01-20T10:00:00Z {\"user_id\":123}"
```

**ポイント**: まず1日分だけ配置して検証

### Step 2: テーブル作成と検証

```sql
-- テーブル作成
CREATE EXTERNAL TABLE test_logs (
  log_level STRING,
  timestamp STRING,
  json_data STRING
)
PARTITIONED BY (year STRING, month STRING, day STRING)
ROW FORMAT SERDE 'org.apache.hadoop.hive.serde2.RegexSerDe'
WITH SERDEPROPERTIES ("input.regex" = "^(\\w+) +(\\S+) +(.*)$")
STORED AS TEXTFILE
LOCATION 's3://my-test-bucket/logs/';

-- パーティション自動検出
MSCK REPAIR TABLE test_logs;

-- 検証クエリ
SHOW PARTITIONS test_logs;
-- 期待結果: year=2025/month=01/day=20

SELECT * FROM test_logs WHERE year='2025' LIMIT 5;
-- データが返ればOK
```

### Step 3: トラブルシューティング

**よくある問題と対処:**

| 問題 | 原因 | 対処 |
|------|------|------|
| パーティションが見えない | ディレクトリ名が間違い | `year=YYYY` 形式を確認 |
| データが全てNULL | 正規表現が不一致 | サンプルログで regex をテスト |
| `HIVE_PARTITION_SCHEMA_MISMATCH` | データ型の不一致 | すべて STRING で定義 |

---

## 高度な検証: Partition Projection

大量パーティション時の代替手段：

```sql
CREATE EXTERNAL TABLE test_logs_projected (
  log_level STRING,
  timestamp STRING,
  json_data STRING
)
PARTITIONED BY (year STRING, month STRING, day STRING)
ROW FORMAT SERDE 'org.apache.hadoop.hive.serde2.RegexSerDe'
WITH SERDEPROPERTIES ("input.regex" = "^(\\w+) +(\\S+) +(.*)$")
STORED AS TEXTFILE
LOCATION 's3://my-test-bucket/logs/'
TBLPROPERTIES (
  "projection.enabled" = "true",
  "projection.year.type" = "integer",
  "projection.year.range" = "2024,2030",
  "projection.month.type" = "integer",
  "projection.month.range" = "1,12",
  "projection.month.digits" = "2",
  "projection.day.type" = "integer",
  "projection.day.range" = "1,31",
  "projection.day.digits" = "2"
);
```

**メリット**: `MSCK REPAIR TABLE` 不要、パーティション数が数十万でも高速

---

## チェックリスト

検証完了の確認項目：

- [ ] `SHOW PARTITIONS` でパーティションが見える
- [ ] `SELECT * ... LIMIT 5` でデータが返る
- [ ] NULL値がないことを確認（`WHERE log_level IS NOT NULL`）
- [ ] JSON部分が正しく `json_data` カラムに入っている
- [ ] パーティション指定クエリで高速化されている（`WHERE year='2025'`）

---

## 次のアクション

- ✅ 検証成功 → 本番ログ形式でテーブル作成
- ❌ 失敗 → 正規表現パターンを調整、またはログ形式を変更検討

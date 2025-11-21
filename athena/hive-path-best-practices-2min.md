# Hiveパス変換のベストプラクティス

## 課題

アプリケーションログをS3にアップロードする際、Hive形式のパス（`year=2025/month=01/day=20/`）に変換する実装パターンを知りたい。

---

## パターン1: アプリケーション側で直接配置（推奨）

### Python実装例

```python
import boto3
import datetime

def upload_log_hive_format(log_data, bucket, base_prefix='application'):
    """Hive形式でS3にログアップロード"""
    s3 = boto3.client('s3')
    now = datetime.datetime.utcnow()

    # Hive形式のパス生成
    s3_key = (
        f"{base_prefix}/"
        f"year={now.year}/"
        f"month={now.month:02d}/"
        f"day={now.day:02d}/"
        f"app-{now.timestamp()}.log"
    )

    s3.put_object(Bucket=bucket, Key=s3_key, Body=log_data)
    print(f"Uploaded: s3://{bucket}/{s3_key}")

# 使用例
log_content = "INFO 2025-01-20T10:30:45Z {\"user_id\": 12345}"
upload_log_hive_format(log_content, 'my-logs-bucket')
```

**出力パス:**
```
s3://my-logs-bucket/application/year=2025/month=01/day=20/app-1737367845.123.log
```

**メリット:**
- シンプルで確実
- アプリケーションから直接制御可能
- Lambda/パイプライン不要

---

## パターン2: S3イベント + Lambda で後処理

既存ログを移動・変換：

```python
import boto3
import re
from datetime import datetime

def lambda_handler(event, context):
    """既存ログをHive形式に再配置"""
    s3 = boto3.client('s3')

    for record in event['Records']:
        bucket = record['s3']['bucket']['name']
        old_key = record['s3']['object']['key']

        # ファイル名から日付抽出: app-2025-01-20.log
        match = re.search(r'(\d{4})-(\d{2})-(\d{2})', old_key)
        if not match:
            continue

        year, month, day = match.groups()

        # Hive形式のパス生成
        new_key = f"application/year={year}/month={month}/day={day}/{old_key.split('/')[-1]}"

        # コピー & 元削除
        s3.copy_object(
            Bucket=bucket,
            CopySource={'Bucket': bucket, 'Key': old_key},
            Key=new_key
        )
        s3.delete_object(Bucket=bucket, Key=old_key)
```

**メリット:**
- 既存システム変更不要
- 段階的移行可能

**デメリット:**
- Lambda実行コスト
- 一時的に2重にファイル存在

---

## パーティション粒度の選択

### 日次パーティション（推奨）

```
s3://bucket/logs/year=2025/month=01/day=20/
```

**適用ケース:**
- ログ量: 〜数百GB/日
- クエリパターン: 過去数日〜数週間の範囲指定

**メリット:**
- メタデータオーバーヘッドが少ない
- 管理しやすい

### 時間次パーティション

```
s3://bucket/logs/year=2025/month=01/day=20/hour=10/
```

**適用ケース:**
- ログ量: 数TB/日以上
- クエリパターン: 数時間単位の細かい範囲指定

**デメリット:**
- パーティション数が膨大（1年で8760個）
- メタデータ処理に1秒/100パーティションのオーバーヘッド

### 目安

| ログ量/日 | 推奨粒度 | パーティション数/年 |
|----------|---------|------------------|
| 〜100GB | 日次 | 365 |
| 100GB〜1TB | 日次 or 時間次 | 365 or 8,760 |
| 1TB以上 | 時間次 + Partition Projection | 8,760 |

---

## パーティション更新の自動化

### 方法1: MSCK REPAIR TABLE（簡易）

```bash
# 日次バッチで実行
aws athena start-query-execution \
  --query-string "MSCK REPAIR TABLE application_logs;" \
  --query-execution-context Database=logs_db \
  --result-configuration OutputLocation=s3://athena-results/
```

**メリット:** 実装不要
**デメリット:** S3全体をスキャンするため遅い（数千パーティション以上）

### 方法2: ALTER TABLE ADD PARTITION（推奨）

Lambda + S3イベントで個別追加：

```sql
ALTER TABLE application_logs
ADD IF NOT EXISTS PARTITION (year='2025', month='01', day='20')
LOCATION 's3://my-logs-bucket/application/year=2025/month=01/day=20/';
```

**メリット:**
- リアルタイムでパーティション追加
- 高速（数秒）

**実装:** `output/athena/scripts/partition_auto_add_lambda.py` 参照

### 方法3: Partition Projection（高度）

テーブル定義でパーティション自動生成：

```sql
TBLPROPERTIES (
  "projection.enabled" = "true",
  "projection.year.type" = "integer",
  "projection.year.range" = "2024,2030",
  ...
)
```

**メリット:**
- パーティション管理不要
- 数十万パーティションでも瞬時

**デメリット:**
- 設定がやや複雑

---

## ファイルサイズの最適化

### 小さすぎるファイルの問題

```
s3://bucket/logs/year=2025/month=01/day=20/
  app-001.log (10KB)
  app-002.log (8KB)
  ...
  app-999.log (12KB)  # 999個のファイル
```

**問題:**
- S3 リクエスト数が増えてコスト増
- Athenaのオーバーヘッド（ファイルごとにオープン/クローズ）

### 推奨ファイルサイズ

| 形式 | 推奨サイズ | 理由 |
|------|----------|------|
| CSV/テキスト | 128MB〜1GB | S3転送効率とAthena並列処理のバランス |
| Parquet | 128MB〜256MB | カラムナー形式の最適化 |

### バッファリング実装例

```python
class LogBuffer:
    def __init__(self, bucket, max_size_mb=128):
        self.bucket = bucket
        self.buffer = []
        self.max_size = max_size_mb * 1024 * 1024
        self.current_size = 0

    def add_log(self, log_line):
        self.buffer.append(log_line)
        self.current_size += len(log_line)

        if self.current_size >= self.max_size:
            self.flush()

    def flush(self):
        if not self.buffer:
            return

        # Hive形式でアップロード
        now = datetime.utcnow()
        s3_key = f"logs/year={now.year}/month={now.month:02d}/day={now.day:02d}/batch-{now.timestamp()}.log"

        s3 = boto3.client('s3')
        s3.put_object(
            Bucket=self.bucket,
            Key=s3_key,
            Body='\n'.join(self.buffer)
        )

        self.buffer = []
        self.current_size = 0
```

---

## まとめ

| 項目 | 推奨プラクティス |
|------|---------------|
| **パス形式** | `year=YYYY/month=MM/day=DD/` |
| **パーティション粒度** | 日次（〜100GB/日）、時間次（1TB/日以上） |
| **ファイルサイズ** | 128MB〜1GB（テキスト）、128MB〜256MB（Parquet） |
| **パーティション更新** | Lambda + S3イベントで`ALTER TABLE ADD PARTITION` |
| **高度な最適化** | Partition Projection（数十万パーティション時） |

**実装スクリプト:** `output/athena/scripts/upload_log_hive_format.py` 参照

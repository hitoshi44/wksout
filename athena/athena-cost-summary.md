## Athena コスト

**基本原則**: スキャンしたデータ量で課金

```
料金 = スキャンしたデータ量 × $5/TB
最小課金: 10MB/クエリ
```

### コスト削減のポイント

#### 1. パーティション分割 + WHERE句

**Hive形式でパーティション分割が必須な理由**

| 方式 | スキャン量 | 料金 | 備考 |
|------|-----------|------|------|
| パーティションなし | 全データ(10TB) | $50 | クエリごとに全スキャン |
| Hive形式パーティション + WHERE | 1日分(33GB) | $0.16 | **99.7%削減** |

**Hive形式の構造:**
```
s3://my-logs-bucket/
  year=2025/
    month=01/
      day=20/
        app.log
```

**WHEREで指定:**
```sql
SELECT * FROM logs
WHERE year='2025' AND month='01' AND day='20'  -- パーティション指定（必須）
  AND status_code = 500;
```

#### 2. SELECT * を避ける

```sql
-- 悪い例: 全列スキャン
SELECT * FROM logs WHERE year='2025';

-- 良い例: 必要な列のみ
SELECT timestamp, user_id, message FROM logs WHERE year='2025';
```

#### 3. Parquet形式への変換

必須ではない


## 参考リンク

### AWS公式ドキュメント

- [Top 10 Performance Tuning Tips for Amazon Athena](https://aws.amazon.com/blogs/big-data/top-10-performance-tuning-tips-for-amazon-athena/) - AWS Big Data Blog
- [Partition your data - Amazon Athena](https://docs.aws.amazon.com/athena/latest/ug/partitions.html) - パーティショニング公式ガイド
- [Optimize data - Amazon Athena](https://docs.aws.amazon.com/athena/latest/ug/performance-tuning-data-optimization-techniques.html) - データ最適化テクニック

### Tech Blog

- [Using Parquet On Amazon Athena](https://www.cloudforecast.io/blog/using-parquet-on-athena-to-save-money-on-aws/) - CloudForecast (具体的な削減事例: 98%コスト削減)
- [Optimize AWS Athena Costs](https://www.astuto.ai/blogs/optimize-aws-athena-costs) - Astuto.ai (パーティショニング、Parquet、クエリ最適化)

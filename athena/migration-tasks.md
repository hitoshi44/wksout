# Athena 導入タスク一覧

## 概要

Athena 導入に必要なタスクを4つのテーマに分けて整理。各タスクに作業内容とざっくりの工数を記載。

---

## タスク1: S3セキュリティ設定

**目的**: ログデータの安全な保管と適切なアクセス制御

### 作業内容

| 項目 | 作業 | 工数 |
|------|------|------|
| バケット作成 | ログ保管用S3バケット作成、リージョン選定 | 0.5h |
| 暗号化設定 | SSE-S3 または SSE-KMS を有効化 | 0.5h |
| バケットポリシー | 最小権限の原則でアクセス制御設定 | 1h |
| バージョニング | 誤削除対策でバージョニング有効化（任意） | 0.5h |
| ライフサイクル | 古いログの自動削除/アーカイブ設定 | 1h |

**成果物**:
- S3バケット設定済み
- IAMポリシー定義済み

**参考リンク**:
- [Security best practices for Amazon S3](https://docs.aws.amazon.com/AmazonS3/latest/userguide/security-best-practices.html) - AWS公式
- [Security Pillar - AWS Well-Architected](https://docs.aws.amazon.com/wellarchitected/latest/security-pillar/welcome.html) - AWS公式

**合計工数**: 3.5時間

---

## タスク2: Hive形式パス構成の設計

**目的**: パーティション分割によるコスト削減と検索効率化

### 作業内容

| 項目 | 作業 | 工数 |
|------|------|------|
| パス設計 | Hive形式のディレクトリ構造設計（year/month/day推奨） | 1h |
| 既存ログ分析 | ログ量、検索パターンの調査 | 2h |
| パーティション粒度決定 | 1パーティション128MB以上を目安に粒度決定 | 1h |
| 検証環境構築 | テスト用S3バケットとサンプルデータ配置 | 1h |
| 動作検証 | Hive形式でのAthenaクエリ動作確認 | 1h |

**推奨構造**:
```
s3://my-logs-bucket/
  application/
    year=2025/
      month=01/
        day=20/
          app-001.log
```

**参考リンク**:
- [Partition your data - Amazon Athena](https://docs.aws.amazon.com/athena/latest/ug/partitions.html) - AWS公式パーティショニングガイド
- [Create and use partitioned tables](https://repost.aws/knowledge-center/athena-create-use-partitioned-tables) - AWS re:Post
- [What is Hive style partitioning?](https://athena.guide/articles/hive-style-partitioning) - The Athena Guide

**関連資料**: `hive-verification-2min.md`

**合計工数**: 6時間

---

## タスク3: ログ配置の自動化（Lambda）

**目的**: 継続的にログをHive形式で配置し、パーティション自動追加

### 作業内容

| 項目 | 作業 | 工数 |
|------|------|------|
| Lambda関数作成 | パーティション自動追加Lambda実装 | 2h |
| IAMロール設定 | Lambda実行ロールと必要な権限設定 | 1h |
| S3イベントトリガー | ログ配置時に自動実行されるよう設定 | 1h |
| テスト実行 | サンプルログでエンドツーエンド動作確認 | 1h |
| エラーハンドリング | CloudWatch Logs監視、リトライ設定 | 1h |

**自動化フロー**:
```
アプリ → S3 (Hive形式) → S3イベント → Lambda → Athena パーティション追加
```

**参考リンク**:
- [ALTER TABLE ADD PARTITION](https://docs.aws.amazon.com/athena/latest/ug/alter-table-add-partition.html) - AWS公式DDLリファレンス
- [Five ways to add partitions](https://athena.guide/articles/five-ways-to-add-partitions) - The Athena Guide

**参考スクリプト**: `scripts/partition_auto_add_lambda.py`

**合計工数**: 6時間

---

## タスク4: CloudTrail 設定

**目的**: API実行記録による監査とセキュリティ強化

### 作業内容

| 項目 | 作業 | 工数 |
|------|------|------|
| CloudTrail有効化 | 管理イベント記録を有効化 | 0.5h |
| S3バケット指定 | CloudTrailログ保存先バケット設定 | 0.5h |
| データイベント設定 | Athena関連のS3バケットアクセスを記録（任意） | 1h |
| ログ検証 | CloudTrailログが記録されていることを確認 | 0.5h |

**記録されるAPI例**:
- `s3:PutObject` (ログアップロード)
- `athena:StartQueryExecution` (クエリ実行)
- `glue:CreatePartition` (パーティション追加)

**参考リンク**:
- [Security best practices in AWS CloudTrail](https://docs.aws.amazon.com/awscloudtrail/latest/userguide/best-practices-security.html) - AWS公式
- [AWS CloudTrail Best Practices](https://aws.amazon.com/blogs/mt/aws-cloudtrail-best-practices/) - AWS Cloud Operations Blog

**合計工数**: 2.5時間

---

## 全体スケジュール

### フェーズ1: 設計・検証（2日）

- タスク1: S3セキュリティ設定
- タスク2: Hive形式パス構成の設計

### フェーズ2: 実装・自動化（2日）

- タスク3: ログ配置の自動化（Lambda）

### フェーズ3: 監査・運用準備（0.5日）

- タスク4: CloudTrail 設定

**総工数**: 約18時間（2.5日）

---

## 前提条件

- AWS CLIまたはマネジメントコンソールへのアクセス権限
- Python 3.11+ 実行環境（Lambda開発用）
- Athena実行権限（検証用）

## 関連資料

### プロジェクト内資料
- `athena-cost-summary.md` - コスト基礎知識
- `hive-verification-2min.md` - Hive形式動作検証
- `scripts/README.md` - 実装スクリプト集

### AWS公式ドキュメント
- [Amazon Athena User Guide](https://docs.aws.amazon.com/athena/latest/ug/what-is.html) - Athena公式ドキュメント
- [AWS Big Data Blog - Athena](https://aws.amazon.com/blogs/big-data/tag/amazon-athena/) - 最新のベストプラクティス

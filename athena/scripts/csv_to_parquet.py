#!/usr/bin/env python3
"""
CSV/テキストログをParquet形式に変換するスクリプト

【目的】
コスト最適化のため、CSV形式のログをParquet形式に変換。
Parquet形式により、スキャン量を90%以上削減可能。

【実行方法】
1. Athenaから直接変換（CTAS - CREATE TABLE AS SELECT）
2. このスクリプトでローカル変換してS3にアップロード

【使用例】
    # Athenaで変換（推奨）
    python csv_to_parquet.py --method athena \
        --database logs_db \
        --source-table application_logs \
        --dest-table application_logs_parquet \
        --partition "year='2025',month='01',day='15'"

    # ローカルファイルを変換
    python csv_to_parquet.py --method local \
        --input logs.csv \
        --output logs.parquet
"""

import argparse
import sys
import boto3
import time
from typing import Optional


class AthenaParquetConverter:
    """AthenaのCTASでParquet変換"""

    def __init__(self, output_location: str = 's3://athena-results/'):
        self.athena_client = boto3.client('athena')
        self.output_location = output_location

    def convert_with_ctas(
        self,
        database: str,
        source_table: str,
        dest_table: str,
        dest_location: str,
        partition_filter: Optional[str] = None,
        select_columns: str = '*'
    ) -> str:
        """
        CTAS（CREATE TABLE AS SELECT）でParquet変換

        Args:
            database: データベース名
            source_table: 変換元テーブル名
            dest_table: 変換先テーブル名
            dest_location: 変換後のS3ロケーション
            partition_filter: パーティションフィルタ（例: "year='2025' AND month='01'"）
            select_columns: 選択する列（デフォルト: '*'）

        Returns:
            QueryExecutionId
        """
        # WHERE句の構築
        where_clause = f"WHERE {partition_filter}" if partition_filter else ""

        # CTAS クエリ
        query = f"""
        CREATE TABLE {database}.{dest_table}
        WITH (
          format = 'PARQUET',
          parquet_compression = 'SNAPPY',
          external_location = '{dest_location}'
        )
        AS
        SELECT {select_columns}
        FROM {database}.{source_table}
        {where_clause};
        """

        print(f"🔧 Executing CTAS query:\n{query}\n")

        response = self.athena_client.start_query_execution(
            QueryString=query,
            QueryExecutionContext={'Database': database},
            ResultConfiguration={'OutputLocation': self.output_location}
        )

        query_execution_id = response['QueryExecutionId']
        print(f"✅ Query submitted: {query_execution_id}")

        return query_execution_id

    def wait_for_query(self, query_execution_id: str, timeout: int = 300) -> str:
        """
        クエリの完了を待つ

        Args:
            query_execution_id: クエリ実行ID
            timeout: タイムアウト秒数

        Returns:
            最終ステータス（SUCCEEDED, FAILED, CANCELLED）
        """
        print(f"⏳ Waiting for query completion...")

        start_time = time.time()
        while time.time() - start_time < timeout:
            response = self.athena_client.get_query_execution(
                QueryExecutionId=query_execution_id
            )

            status = response['QueryExecution']['Status']['State']
            print(f"   Status: {status}", end='\r')

            if status == 'SUCCEEDED':
                print(f"\n✅ Query completed successfully")

                # スキャン量とデータ量を表示
                stats = response['QueryExecution']['Statistics']
                data_scanned = stats.get('DataScannedInBytes', 0) / (1024**3)
                exec_time = stats.get('EngineExecutionTimeInMillis', 0) / 1000

                print(f"   📊 Data scanned: {data_scanned:.2f} GB")
                print(f"   ⏱️  Execution time: {exec_time:.2f} seconds")

                return 'SUCCEEDED'

            elif status in ['FAILED', 'CANCELLED']:
                error_msg = response['QueryExecution']['Status'].get(
                    'StateChangeReason', 'Unknown error'
                )
                print(f"\n❌ Query {status.lower()}: {error_msg}")
                return status

            time.sleep(2)

        print(f"\n❌ Query timeout after {timeout} seconds")
        return 'TIMEOUT'

    def convert_partition(
        self,
        database: str,
        source_table: str,
        dest_table_prefix: str,
        year: str,
        month: str,
        day: str,
        base_location: str
    ) -> bool:
        """
        単一パーティションを変換

        Args:
            database: データベース名
            source_table: 変換元テーブル名
            dest_table_prefix: 変換先テーブル名のプレフィックス
            year: 年
            month: 月
            day: 日
            base_location: ベースS3ロケーション

        Returns:
            成功時True、失敗時False
        """
        dest_table = f"{dest_table_prefix}_{year}_{month}_{day}"
        dest_location = f"{base_location}/year={year}/month={month}/day={day}/"
        partition_filter = f"year='{year}' AND month='{month}' AND day='{day}'"

        print(f"\n📅 Converting partition: {year}-{month}-{day}")

        query_id = self.convert_with_ctas(
            database=database,
            source_table=source_table,
            dest_table=dest_table,
            dest_location=dest_location,
            partition_filter=partition_filter
        )

        status = self.wait_for_query(query_id)
        return status == 'SUCCEEDED'


class LocalParquetConverter:
    """ローカルファイルをPyArrowでParquet変換"""

    def __init__(self):
        try:
            import pyarrow.csv as csv
            import pyarrow.parquet as pq
            self.csv = csv
            self.pq = pq
        except ImportError:
            print("❌ Error: pyarrow not installed")
            print("   Install with: pip install pyarrow")
            sys.exit(1)

    def convert_file(self, input_path: str, output_path: str, compression: str = 'SNAPPY'):
        """
        CSVファイルをParquetに変換

        Args:
            input_path: 入力CSVファイルパス
            output_path: 出力Parquetファイルパス
            compression: 圧縮形式（SNAPPY, GZIP, ZSTD）
        """
        print(f"📥 Reading CSV: {input_path}")

        # CSV読み込み
        table = self.csv.read_csv(input_path)

        print(f"📊 Rows: {table.num_rows}, Columns: {table.num_columns}")

        # Parquet書き込み
        print(f"📤 Writing Parquet: {output_path}")
        self.pq.write_table(table, output_path, compression=compression)

        print(f"✅ Conversion completed")


def main():
    parser = argparse.ArgumentParser(
        description='CSV/TextログをParquet形式に変換'
    )
    parser.add_argument(
        '--method',
        choices=['athena', 'local'],
        required=True,
        help='変換方法: athena（推奨）またはlocal'
    )

    # Athenaモード用引数
    parser.add_argument('--database', help='データベース名（athenaモード）')
    parser.add_argument('--source-table', help='変換元テーブル名（athenaモード）')
    parser.add_argument('--dest-table', help='変換先テーブル名（athenaモード）')
    parser.add_argument('--dest-location', help='変換後S3ロケーション（athenaモード）')
    parser.add_argument('--partition', help='パーティションフィルタ（例: year=\'2025\',month=\'01\'）')
    parser.add_argument('--output-location', default='s3://athena-results/',
                        help='Athenaクエリ結果保存先')

    # Localモード用引数
    parser.add_argument('--input', help='入力ファイルパス（localモード）')
    parser.add_argument('--output', help='出力ファイルパス（localモード）')
    parser.add_argument('--compression', default='SNAPPY',
                        choices=['SNAPPY', 'GZIP', 'ZSTD'],
                        help='圧縮形式（localモード）')

    args = parser.parse_args()

    if args.method == 'athena':
        # Athenaモード
        if not all([args.database, args.source_table, args.dest_table, args.dest_location]):
            print("❌ --database, --source-table, --dest-table, --dest-location が必要です")
            return 1

        converter = AthenaParquetConverter(args.output_location)
        query_id = converter.convert_with_ctas(
            database=args.database,
            source_table=args.source_table,
            dest_table=args.dest_table,
            dest_location=args.dest_location,
            partition_filter=args.partition
        )

        status = converter.wait_for_query(query_id)
        return 0 if status == 'SUCCEEDED' else 1

    elif args.method == 'local':
        # Localモード
        if not all([args.input, args.output]):
            print("❌ --input, --output が必要です")
            return 1

        converter = LocalParquetConverter()
        converter.convert_file(args.input, args.output, args.compression)
        return 0


if __name__ == '__main__':
    exit(main())

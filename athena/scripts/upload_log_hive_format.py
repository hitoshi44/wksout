#!/usr/bin/env python3
"""
Hive形式でログをS3にアップロードするスクリプト

使用例:
    # 単一ファイルのアップロード
    python upload_log_hive_format.py --file app.log --bucket my-logs-bucket

    # ディレクトリ内の全ファイルをアップロード
    python upload_log_hive_format.py --dir /var/log/app/ --bucket my-logs-bucket
"""

import argparse
import datetime
import os
from pathlib import Path
import boto3
from botocore.exceptions import ClientError


class HiveLogUploader:
    """Hive形式でS3にログをアップロード"""

    def __init__(self, bucket: str, base_prefix: str = 'application'):
        """
        Args:
            bucket: S3バケット名
            base_prefix: ログのベースプレフィックス（デフォルト: 'application'）
        """
        self.bucket = bucket
        self.base_prefix = base_prefix
        self.s3_client = boto3.client('s3')

    def generate_hive_path(self, timestamp: datetime.datetime = None) -> str:
        """
        Hive形式のS3パスを生成

        Args:
            timestamp: タイムスタンプ（Noneの場合は現在時刻）

        Returns:
            Hive形式のパス（例: 'application/year=2025/month=01/day=15/'）
        """
        if timestamp is None:
            timestamp = datetime.datetime.now()

        return (
            f"{self.base_prefix}/"
            f"year={timestamp.year}/"
            f"month={timestamp.month:02d}/"
            f"day={timestamp.day:02d}/"
        )

    def upload_file(
        self,
        file_path: str,
        timestamp: datetime.datetime = None,
        custom_filename: str = None
    ) -> str:
        """
        単一ファイルをHive形式でアップロード

        Args:
            file_path: アップロードするファイルのパス
            timestamp: タイムスタンプ（Noneの場合は現在時刻）
            custom_filename: カスタムファイル名（Noneの場合は元のファイル名）

        Returns:
            アップロード先のS3 URI

        Raises:
            FileNotFoundError: ファイルが存在しない
            ClientError: S3アップロード失敗
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        # Hive形式のディレクトリパス生成
        hive_dir = self.generate_hive_path(timestamp)

        # ファイル名決定
        if custom_filename:
            filename = custom_filename
        else:
            filename = os.path.basename(file_path)

        s3_key = f"{hive_dir}{filename}"

        # アップロード
        try:
            self.s3_client.upload_file(file_path, self.bucket, s3_key)
            s3_uri = f"s3://{self.bucket}/{s3_key}"
            print(f"✅ Uploaded: {file_path} -> {s3_uri}")
            return s3_uri

        except ClientError as e:
            print(f"❌ Upload failed: {file_path}")
            raise e

    def upload_directory(
        self,
        directory: str,
        pattern: str = "*.log",
        timestamp: datetime.datetime = None
    ) -> list:
        """
        ディレクトリ内のファイルを一括アップロード

        Args:
            directory: ディレクトリパス
            pattern: ファイルパターン（デフォルト: '*.log'）
            timestamp: タイムスタンプ（Noneの場合は現在時刻）

        Returns:
            アップロードしたファイルのS3 URIリスト
        """
        dir_path = Path(directory)
        if not dir_path.is_dir():
            raise NotADirectoryError(f"Not a directory: {directory}")

        uploaded_files = []
        files = list(dir_path.glob(pattern))

        print(f"📂 Found {len(files)} files matching '{pattern}' in {directory}")

        for file_path in files:
            try:
                s3_uri = self.upload_file(str(file_path), timestamp)
                uploaded_files.append(s3_uri)
            except Exception as e:
                print(f"⚠️  Skipped {file_path}: {e}")

        print(f"\n📊 Summary: {len(uploaded_files)}/{len(files)} files uploaded")
        return uploaded_files

    def upload_content(
        self,
        content: str,
        filename: str,
        timestamp: datetime.datetime = None
    ) -> str:
        """
        コンテンツを直接アップロード（ファイルを経由しない）

        Args:
            content: アップロードする内容
            filename: ファイル名
            timestamp: タイムスタンプ（Noneの場合は現在時刻）

        Returns:
            アップロード先のS3 URI
        """
        hive_dir = self.generate_hive_path(timestamp)
        s3_key = f"{hive_dir}{filename}"

        try:
            self.s3_client.put_object(
                Bucket=self.bucket,
                Key=s3_key,
                Body=content.encode('utf-8')
            )
            s3_uri = f"s3://{self.bucket}/{s3_key}"
            print(f"✅ Uploaded content -> {s3_uri}")
            return s3_uri

        except ClientError as e:
            print(f"❌ Upload failed")
            raise e


def main():
    parser = argparse.ArgumentParser(
        description='Hive形式でログをS3にアップロード'
    )
    parser.add_argument(
        '--bucket',
        required=True,
        help='S3バケット名'
    )
    parser.add_argument(
        '--prefix',
        default='application',
        help='ベースプレフィックス（デフォルト: application）'
    )
    parser.add_argument(
        '--file',
        help='アップロードするファイルパス'
    )
    parser.add_argument(
        '--dir',
        help='アップロードするディレクトリパス'
    )
    parser.add_argument(
        '--pattern',
        default='*.log',
        help='ディレクトリモード時のファイルパターン（デフォルト: *.log）'
    )
    parser.add_argument(
        '--date',
        help='パーティション日付（YYYY-MM-DD形式、省略時は今日）'
    )

    args = parser.parse_args()

    # タイムスタンプ解析
    timestamp = None
    if args.date:
        try:
            timestamp = datetime.datetime.strptime(args.date, '%Y-%m-%d')
        except ValueError:
            print(f"❌ Invalid date format: {args.date}. Use YYYY-MM-DD")
            return 1

    # アップローダー初期化
    uploader = HiveLogUploader(args.bucket, args.prefix)

    # ファイルまたはディレクトリのアップロード
    try:
        if args.file:
            uploader.upload_file(args.file, timestamp)
        elif args.dir:
            uploader.upload_directory(args.dir, args.pattern, timestamp)
        else:
            print("❌ --file または --dir のいずれかを指定してください")
            return 1

        return 0

    except Exception as e:
        print(f"❌ Error: {e}")
        return 1


if __name__ == '__main__':
    exit(main())

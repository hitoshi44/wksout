#!/usr/bin/env bash
# パブリック IP（IPv4 / IPv6）を持つ ENI を一覧し、付いている SG が
# 0.0.0.0/0・::/0・プレフィックスリストに開いているかを判定する。
#
# 使い方:
#   ./list-public-enis.sh                 # デフォルトリージョン
#   ./list-public-enis.sh ap-northeast-1 us-east-1
#   ./list-public-enis.sh all             # 有効な全リージョン
#   AWS_CMD=vltp ./list-public-enis.sh    # aws の代わりに vltp などのラッパーを使う
#
# 出力 (TSV):
#   verdict  region  eni  type  instance  description  public_ipv4  ipv6  sgs  open_rules
#
# verdict:
#   OPEN       … SG に 0.0.0.0/0 / ::/0 / プレフィックスリスト(pl-) の許可あり → 要確認
#   NO-SG      … SG なし（SG なし NLB など）→ 配下ターゲット側の SG を確認
#   restricted … ワールド開放なし
#
# 必要: aws cli v2, jq / 必要な権限: ec2:DescribeNetworkInterfaces,
#       ec2:DescribeSecurityGroups, ec2:DescribeRegions
# 注意: NACL・ルートテーブルは見ていない。pl- は中身を手動確認すること。

set -euo pipefail

# aws の代わりに使うコマンド（例: AWS_CMD=vltp ./list-public-enis.sh）
read -ra AWS <<< "${AWS_CMD:-aws}"

if [ $# -eq 0 ]; then
  REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-$("${AWS[@]}" configure get region 2>/dev/null | tr -d '\r' || true)}}"
  if [ -z "$REGION" ]; then
    echo "リージョンが決まらないので引数で指定してください" >&2
    exit 1
  fi
  REGIONS=("$REGION")
elif [ "$1" = "all" ]; then
  mapfile -t REGIONS < <("${AWS[@]}" ec2 describe-regions \
    --query 'Regions[].RegionName' --output text | tr -d '\r' | tr '\t' '\n')
else
  REGIONS=("$@")
fi

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

printf 'verdict\tregion\teni\ttype\tinstance\tdescription\tpublic_ipv4\tipv6\tsgs\topen_rules\n'

for r in "${REGIONS[@]}"; do
  "${AWS[@]}" ec2 describe-security-groups  --region "$r" --output json > "$TMP/sg.json"
  "${AWS[@]}" ec2 describe-network-interfaces --region "$r" --output json > "$TMP/eni.json"

  jq -r --arg region "$r" --slurpfile sg "$TMP/sg.json" '
    # SG ID -> ワールド開放ルールの一覧
    ( $sg[0].SecurityGroups
      | map({
          key: .GroupId,
          value: [ .IpPermissions[] as $p
            | ( ($p.IpRanges      // [] | map(.CidrIp))
              + ($p.Ipv6Ranges    // [] | map(.CidrIpv6))
              + ($p.PrefixListIds // [] | map(.PrefixListId)) )[]
            | select(. == "0.0.0.0/0" or . == "::/0" or startswith("pl-"))
            | "\(.)>"
              + (if $p.IpProtocol == "-1" then "all"
                 else "\($p.IpProtocol):\($p.FromPort)-\($p.ToPort)" end)
          ]
        })
      | from_entries
    ) as $open

    | .NetworkInterfaces[]
    | select(.Association.PublicIp != null
             or ((.Ipv6Addresses // []) | length > 0))
    | (.Groups // []) as $g
    | ($g | map($open[.GroupId] // []) | add // [] | unique) as $rules
    | [ (if ($rules | length) > 0 then "OPEN"
         elif ($g | length) == 0 then "NO-SG"
         else "restricted" end),
        $region,
        .NetworkInterfaceId,
        .InterfaceType,
        (.Attachment.InstanceId // "-"),
        ((.Description // "") | if . == "" then "-" else . end),
        (.Association.PublicIp // "-"),
        ((.Ipv6Addresses // []) | map(.Ipv6Address) | join(",")
          | if . == "" then "-" else . end),
        ($g | map(.GroupId) | join(",") | if . == "" then "-" else . end),
        ($rules | join(" ") | if . == "" then "-" else . end)
      ]
    | @tsv
  ' "$TMP/eni.json"
done

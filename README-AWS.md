# Tusk-Ubuntu24：AWS EC2 10 / 20 / 50 节点完整部署

本文对应当前仓库的 `run-multi-servers.sh`。每台 EC2 运行一个 Primary、一个 Worker 和一个 benchmark client；node-0 同时是控制机和第 0 个协议节点。所有 committee 地址使用 Private IPv4。

## 1. 资源规划

| 项目 | 值 |
|---|---|
| AMI | Ubuntu Server 24.04 LTS，x86_64 |
| 节点数 | 10、20 或 50 |
| 登录用户 | `ubuntu` |
| 项目目录 | `/home/ubuntu/Tusk-Ubuntu24` |
| 仓库 | `https://github.com/DrDaydream/Tusk-Ubuntu24.git` |
| 推荐实例 | 至少 4 vCPU / 16 GiB，50 节点建议 8 vCPU |
| 磁盘 | 至少 30 GiB gp3 |
| 控制机 | node-0，同时参与协议 |
| 网络 | 同一 Region、同一 VPC，建议同一 AZ |

协议要求 `n >= 3f+1`。推荐最大敌手数：10 节点 f=3、20 节点 f=6、50 节点 f=16。先以 10 节点、20 秒、10,000 总 TPS 验证。创建 50 台前检查 AWS Service Quotas 中的 On-Demand vCPU 配额。

## 2. AWS 控制台与安全组

1. AWS Console -> EC2 -> Security Groups -> Create security group。
2. 名称填写 `tusk-sg`，选择集群 VPC。
3. EC2 -> Key Pairs 创建 ED25519 密钥 `tusk-aws.pem`。
4. Launch instances，选择 Ubuntu 24.04 x86_64、相同 VPC/子网/安全组。
5. Number of instances 填 10、20 或 50；建议同一实例类型、同一 AZ、30 GiB gp3。
6. 实例 2/2 status checks 通过后命名为 `tusk-node-0` 至 `tusk-node-N-1`。

安全组入站：

| 协议/端口 | Source | 用途 |
|---|---|---|
| TCP 22 | 你的公网 IP /32 | 本地登录 |
| TCP 22 | `tusk-sg` 自身 | node-0 私网 SSH |
| TCP 3000-3004 | `tusk-sg` 自身 | Tusk 集群内部通信 |

不要向 `0.0.0.0/0` 开放协议端口。

| 端口 | 用途 |
|---:|---|
| 3000 | Primary <-> Primary |
| 3001 | Worker -> Primary |
| 3002 | Primary -> Worker |
| 3003 | Client -> Worker |
| 3004 | Worker <-> Worker |

## 2.1 五大洲跨 Region 部署

上面的安全组自身引用针对单 Region/VPC。五大洲测试可在 5 个 Region 各放 2/4/10 台，对应 10/20/50 节点，例如 `us-east-1`、`sa-east-1`、`eu-west-2`、`ap-southeast-1`、`ap-southeast-2`。

为五个 VPC 分配非重叠 CIDR，例如 `10.10.0.0/16` 到 `10.50.0.0/16`。使用 AWS Cloud WAN 或 Transit Gateway inter-Region peering 建立私网连接，并为每个 VPC route table 加入其他四个 CIDR 的双向路由。每个 Region 都创建安全组：TCP 3000-3004 来源为全部五个集群 CIDR，TCP 22 来源为你的公网 IP /32 和 node-0 VPC CIDR。

hosts 与 committee 统一写可路由的 Private IPv4，node-0 必须能私网 SSH 到全部节点。固定公网/Elastic IP 加逐个 /32 allowlist 只能作为没有私网互联时的备选，不应开放 `0.0.0.0/0`。跨 Region 流量收费，实验结果应记录节点分布、RTT 和实例类型。


## 3. 配置 node-0 SSH

在本地电脑执行：

~~~bash
chmod 400 ~/Downloads/tusk-aws.pem
scp -i ~/Downloads/tusk-aws.pem ~/Downloads/tusk-aws.pem \
  ubuntu@NODE0_PUBLIC_IP:/home/ubuntu/.ssh/tusk-aws.pem
ssh -i ~/Downloads/tusk-aws.pem ubuntu@NODE0_PUBLIC_IP
~~~

进入 node-0 后：

~~~bash
chmod 400 ~/.ssh/tusk-aws.pem
nano ~/.ssh/config
~~~

写入：

~~~sshconfig
Host 10.*
    User ubuntu
    IdentityFile /home/ubuntu/.ssh/tusk-aws.pem
    StrictHostKeyChecking accept-new
    ConnectTimeout 8
    ServerAliveInterval 5
    ServerAliveCountMax 2
~~~

若私网不是 `10.*`，改成实际网段或 `Host *`。然后：

~~~bash
chmod 600 ~/.ssh/config
git clone https://github.com/DrDaydream/Tusk-Ubuntu24.git ~/Tusk-Ubuntu24
cd ~/Tusk-Ubuntu24
cp deploy/hosts-10.txt.example deploy/hosts-10.txt
nano deploy/hosts-10.txt
~~~

hosts 每行只能有一个 Private IPv4，node-0 放第一行。20/50 节点使用 `deploy/hosts-20.txt`、`deploy/hosts-50.txt`。

~~~bash
wc -l deploy/hosts-10.txt
sort deploy/hosts-10.txt | uniq -d
while read -r ip; do ssh "$ip" hostname; done < deploy/hosts-10.txt
~~~

分别应得到 10 行、无重复项、全部登录成功。

## 4. 安装并编译所有节点

在 node-0 的仓库目录执行；20/50 节点替换 hosts 文件：

~~~bash
while read -r ip; do
  ssh "$ip" 'bash -s' <<'REMOTE' &
set -Eeuo pipefail
sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
  build-essential cmake clang-14 libclang-14-dev git curl tmux jq \
  python3 python3-pip netcat-openbsd chrony
sudo systemctl enable --now chrony
if [[ ! -x "$HOME/.cargo/bin/cargo" ]]; then
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
fi
source "$HOME/.cargo/env"
rustup default stable
if [[ -d "$HOME/Tusk-Ubuntu24/.git" ]]; then
  git -C "$HOME/Tusk-Ubuntu24" pull --ff-only
else
  git clone https://github.com/DrDaydream/Tusk-Ubuntu24.git \
    "$HOME/Tusk-Ubuntu24"
fi
cd "$HOME/Tusk-Ubuntu24"
LIBCLANG_PATH=/usr/lib/llvm-14/lib \
CLANG_PATH=/usr/bin/clang-14 \
CC=/usr/bin/clang-14 \
CXX=/usr/bin/clang++-14 \
CXXFLAGS='-include cstdint' \
cargo build --release --features benchmark
test -x target/release/node
test -x target/release/benchmark_client
REMOTE
done < deploy/hosts-10.txt
wait
~~~

检查所有 commit 一致：

~~~bash
while read -r ip; do
  ssh "$ip" 'git -C ~/Tusk-Ubuntu24 rev-parse HEAD'
done < deploy/hosts-10.txt
~~~

## 5. 生成密钥和公共配置

切换节点规模时必须重新生成和分发：

~~~bash
cd ~/Tusk-Ubuntu24
NODES=10
HOSTS_FILE=deploy/hosts-10.txt
rm -f deploy/node-*.json deploy/committee.json deploy/parameters.json
for ((i=0; i<NODES; i++)); do
  ./target/release/node generate_keys --filename "deploy/node-$i.json"
done
chmod 600 deploy/node-*.json

python3 - "$HOSTS_FILE" "$NODES" <<'PY'
import json
import sys
from pathlib import Path

nodes = int(sys.argv[2])
ips = [x.split("#", 1)[0].strip()
       for x in Path(sys.argv[1]).read_text().splitlines()]
ips = [x for x in ips if x]
assert len(ips) == nodes, (len(ips), nodes)
assert len(set(ips)) == nodes, "duplicate private IP"

authorities = {}
for i, ip in enumerate(ips):
    key = json.loads(Path(f"deploy/node-{i}.json").read_text())
    authorities[key["name"]] = {
        "primary": {
            "primary_to_primary": f"{ip}:3000",
            "worker_to_primary": f"{ip}:3001",
        },
        "stake": 1,
        "workers": {"0": {
            "primary_to_worker": f"{ip}:3002",
            "transactions": f"{ip}:3003",
            "worker_to_worker": f"{ip}:3004",
        }},
    }

Path("deploy/committee.json").write_text(
    json.dumps({"authorities": authorities}, indent=4)
)
Path("deploy/parameters.json").write_text(json.dumps({
    "header_size": 1000,
    "max_header_delay": 200,
    "gc_depth": 50,
    "sync_retry_delay": 10000,
    "sync_retry_nodes": 3,
    "batch_size": 500000,
    "max_batch_delay": 200,
}, indent=4))
PY

mapfile -t IPS < <(awk 'NF && $1 !~ /^#/ {print $1}' "$HOSTS_FILE")
for ((i=0; i<NODES; i++)); do
  ssh "${IPS[$i]}" 'mkdir -p ~/Tusk-Ubuntu24/deploy'
  scp "deploy/node-$i.json" deploy/committee.json deploy/parameters.json \
    "${IPS[$i]}:Tusk-Ubuntu24/deploy/"
done
~~~

确认公共配置哈希相同：

~~~bash
while read -r ip; do
  ssh "$ip" 'sha256sum ~/Tusk-Ubuntu24/deploy/committee.json'
done < "$HOSTS_FILE"
~~~

公平对比协议时，固定节点规模、实例类型、`header_size`、`max_header_delay`、batch 参数、总 TPS、运行时间和 seed。

## 6. Tusk 敌手与调度选项

| 环境变量 | 默认值 | 含义 |
|---|---|---|
| `TUSK_FAULTS` | `0` | 每轮敌手数；0 表示无敌手 |
| `TUSK_ADVERSARY_SEED` | `0` | 确定性随机种子 |
| `TUSK_CLIENT_DURING_SILENCE` | `pause` | `pause` 或 `send` |
| `TUSK_CLIENT_SILENCE_SLOT_MS` | `max_header_delay` | 预生成 Client 时序表槽宽，毫秒 |

`TUSK_FAULTS>0` 时，所有节点仍在线，每轮按 seed、轮次和 authority 身份确定性选择 f 个静默敌手。静默 Primary 不生成 Header，但继续接收消息。

调度以 `3f+1` 个 leader 轮为一个窗口：其中恰好 f 个 leader 静默；在其余 `2f+1` 个可用 leader 中，确定性随机选出最多 `f+2` 个允许 direct commit。因此可用 leader 足够时，全部 leader 中 direct commit 的目标比例为：

| f | 目标比例 |
|---:|---:|
| 3 | 5/10 = 50% |
| 6 | 8/19 = 42.11% |
| 16 | 18/49 = 36.73% |

其余路径归入 fallback 统计。结果会输出 direct-commit 和 fallback leader 比例。

`pause` 是默认模式：运行前生成单向墙钟静默时间表，静默槽内 Client 不发交易且 Worker 暂停 batch。`send` 保持 Client 与 batch 输入，但不会恢复 Primary 的 Header，适合作为工作负载对照。

## 7. 运行 10 / 20 / 50 节点

参数为节点数、正式运行秒数、集群总 TPS，脚本自动均摊 TPS。

无敌手基线：

~~~bash
cd ~/Tusk-Ubuntu24
chmod +x run-multi-servers.sh
./run-multi-servers.sh 10 20 10000
./run-multi-servers.sh 20 60 10000
./run-multi-servers.sh 50 60 10000
~~~

最大建议敌手数：

~~~bash
TUSK_FAULTS=3 TUSK_ADVERSARY_SEED=42 \
TUSK_CLIENT_DURING_SILENCE=pause \
./run-multi-servers.sh 10 20 10000

TUSK_FAULTS=6 TUSK_ADVERSARY_SEED=42 \
TUSK_CLIENT_DURING_SILENCE=pause \
./run-multi-servers.sh 20 60 10000

TUSK_FAULTS=16 TUSK_ADVERSARY_SEED=42 \
TUSK_CLIENT_DURING_SILENCE=pause \
./run-multi-servers.sh 50 60 10000
~~~

保持输入流量的对照：

~~~bash
TUSK_FAULTS=3 TUSK_ADVERSARY_SEED=42 \
TUSK_CLIENT_DURING_SILENCE=send \
./run-multi-servers.sh 10 20 10000
~~~

默认路径：

- `SSH_KEY=~/.ssh/tusk-aws.pem`
- `REMOTE_USER=ubuntu`
- `REMOTE_DIR=/home/ubuntu/Tusk-Ubuntu24`
- `HOSTS_FILE=deploy/hosts-N.txt`

覆盖示例：

~~~bash
SSH_KEY=/home/ubuntu/.ssh/tusk-aws.pem \
HOSTS_FILE=/home/ubuntu/Tusk-Ubuntu24/deploy/hosts-10.txt \
./run-multi-servers.sh 10 20 10000
~~~

脚本等待全部 Worker 的 3003 和全部 Client 就绪后开始计时，最后下载日志到 `benchmark/logs/` 并输出 TPS、延迟及 direct/fallback 统计。

## 8. 检查与排障

~~~bash
# commit 一致
while read -r ip; do
  ssh "$ip" 'git -C ~/Tusk-Ubuntu24 rev-parse HEAD'
done < deploy/hosts-10.txt

# 测试运行期间检查 Worker
while read -r ip; do nc -vz -w 2 "$ip" 3003; done < deploy/hosts-10.txt

# 时间和资源
while read -r ip; do
  ssh "$ip" 'chronyc tracking | head -5; nproc; free -h; df -h /'
done < deploy/hosts-10.txt
~~~

常见问题：

- `hostname contains invalid characters`：hosts 只能写纯私网 IPv4。
- `ready=0/N`：至少一个 Worker 未监听 3003。
- `NoneType object has no attribute group`：至少一个 Client 未打印 `Start sending transactions`。
- direct 比例异常：确认 `TUSK_FAULTS>0`、测试足够长、所有节点使用相同 commit/committee/seed。
- 全 0：检查测试是否真正开始、Primary 是否产生提交、运行时间是否过短。
- `librocksdb-sys` / bindgen：确认 clang-14 的五个环境变量完整。
- `Malformed` / `Serialization`：版本或公共配置不一致。
- `Connection refused` 表示进程没监听；`timed out` 通常是安全组、NACL、UFW 或 IP 错误。

~~~bash
ssh NODE_PRIVATE_IP 'tail -100 ~/Tusk-Ubuntu24/run/logs/primary-INDEX.log'
ssh NODE_PRIVATE_IP 'tail -100 ~/Tusk-Ubuntu24/run/logs/worker-INDEX-0.log'
ssh NODE_PRIVATE_IP 'tail -100 ~/Tusk-Ubuntu24/run/logs/client-INDEX-0.log'
~~~

不要上传 pem 或 `deploy/node-*.json`。测试后停止或终止 EC2，并检查 EBS、Elastic IP、公网 IPv4 和跨 AZ 流量费用。

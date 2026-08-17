# Tusk-Ubuntu24 AWS 10/20/50 节点部署

## AWS 网络

1. 创建安全组 `tusk-sg`。TCP 22 允许你的公网 IP；TCP 22 及 `3000-3004` 仅允许 `tusk-sg` 自身。
2. 创建 ED25519 密钥 `tusk-aws.pem`。
3. 在同一 Region、VPC 和可用区创建 10/20/50 台 Ubuntu 24.04 x86_64 EC2，建议 4–8 vCPU、16 GiB、30 GiB gp3。
4. node-0 兼任控制机，所有协议配置使用 Private IPv4。

端口：3000 Primary↔Primary，3001 Worker→Primary，3002 Primary→Worker，3003 Client→Worker，3004 Worker↔Worker。

## node-0 准备

```bash
chmod 400 ~/.ssh/tusk-aws.pem
git clone https://github.com/DrDaydream/Tusk-Ubuntu24.git
cd Tusk-Ubuntu24
cp deploy/hosts-10.txt.example deploy/hosts-10.txt
nano deploy/hosts-10.txt
```

hosts 每行一个私网 IP，node-0 放第一行。20/50 节点创建 `hosts-20.txt` / `hosts-50.txt`。

## 所有节点安装编译

```bash
while read -r ip; do ssh -i ~/.ssh/tusk-aws.pem ubuntu@$ip 'bash -s' <<'REMOTE' &
set -Eeuo pipefail
sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y build-essential cmake clang-14 libclang-14-dev git curl tmux python3 python3-pip chrony
sudo systemctl enable --now chrony
test -x "$HOME/.cargo/bin/cargo" || curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
source "$HOME/.cargo/env"
test -d ~/Tusk-Ubuntu24/.git && git -C ~/Tusk-Ubuntu24 pull --ff-only || git clone https://github.com/DrDaydream/Tusk-Ubuntu24.git ~/Tusk-Ubuntu24
cd ~/Tusk-Ubuntu24
LIBCLANG_PATH=/usr/lib/llvm-14/lib CLANG_PATH=/usr/bin/clang-14 CC=/usr/bin/clang-14 CXX=/usr/bin/clang++-14 CXXFLAGS='-include cstdint' cargo build --release --features benchmark
REMOTE
done < deploy/hosts-10.txt
wait
```

## 生成密钥和 committee

```bash
NODES=10; HOSTS=deploy/hosts-10.txt
for ((i=0;i<NODES;i++)); do ./target/release/node generate_keys --filename deploy/node-$i.json; done
python3 - "$HOSTS" "$NODES" <<'PY'
import json,sys
from pathlib import Path
ips=[x.strip() for x in Path(sys.argv[1]).read_text().splitlines() if x.strip() and not x.startswith('#')]
n=int(sys.argv[2]); assert len(ips)==n and len(set(ips))==n
a={}
for i,ip in enumerate(ips):
 k=json.loads(Path(f'deploy/node-{i}.json').read_text())
 a[k['name']]={'primary':{'primary_to_primary':f'{ip}:3000','worker_to_primary':f'{ip}:3001'},'stake':1,'workers':{'0':{'primary_to_worker':f'{ip}:3002','transactions':f'{ip}:3003','worker_to_worker':f'{ip}:3004'}}}
Path('deploy/committee.json').write_text(json.dumps({'authorities':a},indent=4))
p={'header_size':1000,'max_header_delay':200,'gc_depth':50,'sync_retry_delay':10000,'sync_retry_nodes':3,'batch_size':500000,'max_batch_delay':200}
Path('deploy/parameters.json').write_text(json.dumps(p,indent=4))
PY
mapfile -t IPS < <(awk 'NF && $1 !~ /^#/ {print $1}' "$HOSTS")
for ((i=0;i<NODES;i++)); do ssh -i ~/.ssh/tusk-aws.pem ubuntu@${IPS[$i]} 'mkdir -p ~/Tusk-Ubuntu24/deploy'; scp -i ~/.ssh/tusk-aws.pem deploy/node-$i.json deploy/committee.json deploy/parameters.json ubuntu@${IPS[$i]}:Tusk-Ubuntu24/deploy/; done
```

## 运行

```bash
chmod +x run-multi-servers.sh
./run-multi-servers.sh 10 20 10000
./run-multi-servers.sh 20 60 10000
./run-multi-servers.sh 50 60 10000
```

日志与结果位于 `benchmark/logs`。`NoneType` 日志解析错误通常表示 client 没有 `Start sending transactions`；检查所有 Worker 的 3003 端口。测试后及时 Stop/Terminate EC2。

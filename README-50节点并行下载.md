# Tusk 50 节点并行下载、依赖安装与编译

在 node0 上执行。SSH 使用 `/home/ubuntu/.ssh/config`，五个 PEM 按 Region 选择；`deploy/hosts-50.txt` 每行填写一个私网 IPv4，第一行是 node0。脚本使用 `SSH_KEY=` 留空的方式读取 SSH config。

~~~bash
cd /home/ubuntu/Tusk-Ubuntu24
HOSTS=deploy/hosts-50.txt
sed -e 's/#.*//' -e '/^[[:space:]]*$/d' "$HOSTS" | xargs -P 50 -I {} ssh {} '
  if [ -d /home/ubuntu/Tusk-Ubuntu24/.git ]; then git -C /home/ubuntu/Tusk-Ubuntu24 pull --ff-only;
  elif [ ! -e /home/ubuntu/Tusk-Ubuntu24 ]; then git clone https://github.com/DrDaydream/Tusk-Ubuntu24.git /home/ubuntu/Tusk-Ubuntu24;
  else echo "existing non-git directory" >&2; exit 1; fi'
~~~

依赖安装和编译：

~~~bash
sed -e 's/#.*//' -e '/^[[:space:]]*$/d' "$HOSTS" | xargs -P 10 -I {} ssh {} '
  set -e
  sudo apt-get update
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y build-essential clang libclang-dev cmake pkg-config libssl-dev librocksdb-dev git curl
  if ! command -v cargo >/dev/null 2>&1; then curl --proto "=https" --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y; fi
  cd /home/ubuntu/Tusk-Ubuntu24
  . "$HOME/.cargo/env" 2>/dev/null || true
  cargo fetch
  CARGO_BUILD_JOBS=2 cargo build --quiet --release --features benchmark
'
~~~

检查编译结果：

~~~bash
sed -e 's/#.*//' -e '/^[[:space:]]*$/d' "$HOSTS" | xargs -P 50 -I {} ssh {} '
  printf "%s: " "$(hostname)"; test -x /home/ubuntu/Tusk-Ubuntu24/target/release/node && echo "build ok" || echo "build failed"'
~~~

# Tusk on Ubuntu 24.04

本目录来自 Narwhal/Tusk 作者维护的公开仓库 `asonnino/narwhal` 的 `master` 分支，并已适配 Ubuntu 24.04、Python 3.12 和本地 `fab local`。

## 安装

```bash
sudo apt update
sudo apt install -y build-essential cmake clang-14 libclang-14-dev git curl tmux python3 python3-pip
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
source "$HOME/.cargo/env"
cd benchmark
python3 -m pip install --user --break-system-packages -r requirements.txt
```

## 本地测试

必须从 `benchmark` 目录运行：

```bash
cd /path/to/Tusk-Ubuntu24/benchmark
fab local
```

默认参数位于 `benchmark/fabfile.py`：4节点、1 Worker/节点、50,000 tx/s、512 B、20秒。

本适配会自动寻找 Ubuntu 已安装的 clang/libclang 14–18，为 RocksDB 添加所需的 C++ `cstdint` 兼容选项；显式使用 `RUST_LOG=info` 保留性能解析标记；每次测试使用独立 tmux socket，避免和其他项目冲突。

本机验证结果（不同硬件不可直接横向比较）：End-to-end TPS 47,250，End-to-end latency 791 ms。


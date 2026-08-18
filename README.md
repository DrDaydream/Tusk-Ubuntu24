# Tusk-Ubuntu24

[![Rust](https://github.com/DrDaydream/Tusk-Ubuntu24/actions/workflows/rust.yml/badge.svg)](https://github.com/DrDaydream/Tusk-Ubuntu24/actions/workflows/rust.yml)
[![Ubuntu](https://img.shields.io/badge/Ubuntu-24.04-E95420?style=flat-square&logo=ubuntu)](https://ubuntu.com/)
[![license](https://img.shields.io/badge/license-Apache--2.0-blue.svg?style=flat-square)](LICENSE)

This repository provides an Ubuntu 24.04-compatible implementation of [Narwhal and Tusk](https://arxiv.org/pdf/2105.11827.pdf). This version also includes deterministic dynamic-adversary scheduling, controlled direct-commit selection, additional leader/non-leader latency statistics, and local/AWS benchmark tooling.

The code is designed for research, benchmarking, and protocol modification rather than production use. It uses real cryptography ([dalek](https://doc.dalek.rs/ed25519_dalek)), asynchronous networking ([Tokio](https://docs.rs/tokio)), and persistent storage ([RocksDB](https://rocksdb.org/)).

## Quick Start

The core protocol is written in Rust. Python scripts use [Fabric](https://www.fabfile.org/) to compile, run, and parse benchmarks.

Install the Ubuntu 24.04 dependencies directly into the current user environment:

~~~bash
git clone https://github.com/DrDaydream/Tusk-Ubuntu24.git
cd Tusk-Ubuntu24

sudo apt-get update
sudo apt-get install -y \
  build-essential cmake clang-14 libclang-14-dev curl git tmux \
  python3 python3-pip

curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
source "$HOME/.cargo/env"

python3 -m pip install --user --break-system-packages \
  -r benchmark/requirements.txt
export PATH="$HOME/.local/bin:$PATH"
~~~

The local benchmark detects installed LLVM versions automatically. These explicit variables can be used if RocksDB bindgen fails:

~~~bash
export LIBCLANG_PATH=/usr/lib/llvm-14/lib
export CLANG_PATH=/usr/bin/clang-14
export CC=/usr/bin/clang-14
export CXX=/usr/bin/clang++-14
export CXXFLAGS='-include cstdint'
~~~

Configure the experiment in `benchmark/fabfile.py`:

~~~python
bench_params = {
    'faults': 0,
    'nodes': 4,
    'workers': 1,
    'rate': 50_000,
    'tx_size': 512,
    'duration': 20,
}
~~~

The `faults` field enables the dynamic adversary while all processes stay online. Use `nodes >= 3 * faults + 1`.

Run:

~~~bash
cd benchmark
fab local
~~~

The first run builds the workspace in release mode with the `benchmark` feature and can take several minutes.

### Local adversary options

Set `'faults': 0` in `benchmark/fabfile.py` for the no-adversary baseline. With `faults > 0`:

~~~bash
# Default: pause client input during deterministically selected silent slots.
TUSK_ADVERSARY_SEED=42 \
TUSK_CLIENT_DURING_SILENCE=pause \
fab local

# Keep client and batch input while the selected Primary remains silent.
TUSK_ADVERSARY_SEED=42 \
TUSK_CLIENT_DURING_SILENCE=send \
fab local

# Override the wall-clock schedule slot.
TUSK_ADVERSARY_SEED=42 \
TUSK_CLIENT_DURING_SILENCE=pause \
TUSK_CLIENT_SILENCE_SLOT_MS=200 \
fab local
~~~

| Variable | Default | Meaning |
|---|---|---|
| `TUSK_ADVERSARY_SEED` | `0` | Deterministic per-round schedule seed |
| `TUSK_CLIENT_DURING_SILENCE` | `pause` | Pause or preserve client input |
| `TUSK_CLIENT_SILENCE_SLOT_MS` | `max_header_delay` | Client schedule slot in milliseconds |

Every round selects exactly f adversarial authorities. A silent Primary suppresses its Header while still receiving messages. Client silence is a pre-generated one-way wall-clock schedule and does not query live protocol rounds.

Leader scheduling uses windows of `3f+1` leader rounds. Exactly f leader slots are silent, and up to `f+2` available slots are deterministically selected for direct commit. When enough leaders are available, the target direct-commit ratio over all leader slots is `(f+2)/(3f+1)`. Other committed leaders are reported as fallback leaders.

### No-adversary baseline (`faults = 0`)

Set `'faults': 0` in `benchmark/fabfile.py` and run:

~~~bash
RUST_LOG=info fab local
~~~

The following output was produced by a 4-node, 50,000 tx/s, 20-second local run:

~~~text
-----------------------------------------
 SUMMARY:
-----------------------------------------
 + CONFIG:
 Faults: 0 node(s)
 Committee size: 4 node(s)
 Worker(s) per node: 1 worker(s)
 Collocate primary and workers: True
 Input rate: 50,000 tx/s
 Transaction size: 512 B
 Execution time: 20 s

 Header size: 1,000 B
 Max header delay: 200 ms
 GC depth: 50 round(s)
 Sync retry delay: 10,000 ms
 Sync retry nodes: 3 node(s)
 batch size: 500,000 B
 Max batch delay: 200 ms

 + RESULTS:
 Consensus TPS: 44,182 tx/s
 Consensus BPS: 22,621,418 B/s
 Consensus latency: 847 ms

 End-to-end TPS: 43,859 tx/s
 End-to-end BPS: 22,455,760 B/s
 End-to-end latency: 985 ms
 Leader commit latency: 527 ms
 Non-leader commit latency: 893 ms
 All committed headers latency: 846 ms
 Leader commit interval: 436 ms
 Non-leader rule-order latency: 893 ms
 Direct-commit leader ratio: 97.73%
 Fallback leader ratio: 2.27%
-----------------------------------------
~~~

### Preserved adversarial result (`faults = 1`)

For comparison, this is the previously recorded 4-node, 1-fault, 20-second local result using the adversary commands above:

~~~text
-----------------------------------------
 SUMMARY:
-----------------------------------------
 + CONFIG:
 Faults: 1 node(s)
 Committee size: 4 node(s)
 Worker(s) per node: 1 worker(s)
 Collocate primary and workers: True
 Input rate: 50,000 tx/s
 Transaction size: 512 B
 Execution time: 20 s

 Header size: 1,000 B
 Max header delay: 200 ms
 GC depth: 50 round(s)
 Sync retry delay: 10,000 ms
 Sync retry nodes: 3 node(s)
 batch size: 500,000 B
 Max batch delay: 200 ms

 + RESULTS:
 Consensus TPS: 36,247 tx/s
 Consensus BPS: 18,558,490 B/s
 Consensus latency: 829 ms

 End-to-end TPS: 35,999 tx/s
 End-to-end BPS: 18,431,588 B/s
 End-to-end latency: 1,036 ms
 Leader commit latency: 510 ms
 Non-leader commit latency: 877 ms
 All committed headers latency: 831 ms
 Leader commit interval: 512 ms
 Non-leader rule-order latency: 877 ms
 Direct-commit leader ratio: 100.00%
 Fallback leader ratio: 0.00%
-----------------------------------------
~~~

Results depend on hardware and load. `Consensus latency` measures header creation to consensus commit; `End-to-end latency` starts when the benchmark client submits a sampled transaction. Short executions may not converge to the configured long-run direct/fallback ratio.

## Next Steps

- Read [Narwhal and Tusk: A DAG-based Mempool and Efficient BFT Consensus](https://arxiv.org/pdf/2105.11827.pdf).
- Read [All You Need is DAG](https://arxiv.org/abs/2102.08325) for related asynchronous DAG consensus.
- See [benchmark/README.md](benchmark/README.md) for complete benchmark parameters and result semantics.
- See [README-AWS.md](README-AWS.md) for complete AWS 10/20/50-node, cross-Region, and adversary deployment instructions.
- Inspect the [primary](primary), [worker](worker), and [consensus](consensus) crates.

## License

This software is licensed under [Apache License 2.0](LICENSE).

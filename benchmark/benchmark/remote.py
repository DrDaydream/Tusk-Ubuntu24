# Copyright(C) Facebook, Inc. and its affiliates.
from collections import OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from fabric import Connection, ThreadingGroup as Group
from fabric.exceptions import GroupException
from paramiko import RSAKey
from paramiko.ssh_exception import PasswordRequiredException, SSHException
from os.path import basename, splitext
from shlex import quote
from threading import Event, Lock, Thread
from time import monotonic, sleep
from math import ceil
from copy import deepcopy
import subprocess

from benchmark.config import Committee, Key, NodeParameters, BenchParameters, ConfigError
from benchmark.utils import BenchError, Print, PathMaker, progress_bar
from benchmark.commands import CommandMaker
from benchmark.adversary_schedule import build_client_schedules, client_silence_slot_ms
from benchmark.logs import LogParser, ParseError
from benchmark.instance import InstanceManager


class FabricError(Exception):
    ''' Wrapper for Fabric exception with a meaningfull error message. '''

    def __init__(self, error):
        assert isinstance(error, GroupException)
        message = list(error.result.values())[-1]
        super().__init__(message)


class ExecutionError(Exception):
    pass


class Bench:
    def __init__(self, ctx):
        self.manager = InstanceManager.make()
        self.settings = self.manager.settings
        try:
            ctx.connect_kwargs.pkey = RSAKey.from_private_key_file(
                self.manager.settings.key_path
            )
            self.connect = ctx.connect_kwargs
        except (IOError, PasswordRequiredException, SSHException) as e:
            raise BenchError('Failed to load SSH key', e)

    def _check_stderr(self, output):
        if isinstance(output, dict):
            for x in output.values():
                if x.stderr:
                    raise ExecutionError(x.stderr)
        else:
            if output.stderr:
                raise ExecutionError(output.stderr)

    def install(self):
        Print.info('Installing dependencies and cloning the repo...')
        hosts = self.manager.hosts(flat=True)
        if not hosts:
            raise BenchError(
                'Failed to install repo on testbed',
                RuntimeError('No running testbed nodes found'),
            )

        apt = (
            'sudo timeout --signal=TERM --kill-after=30s 1800 '
            'env DEBIAN_FRONTEND=noninteractive '
            'APT_LISTCHANGES_FRONTEND=none NEEDRESTART_MODE=a apt-get '
            '-o DPkg::Lock::Timeout=900 '
            '-o Acquire::Retries=3 '
            '-o Acquire::http::Timeout=30 '
            '-o Acquire::https::Timeout=30 '
            '-o Acquire::ForceIPv4=true '
            '-o Dpkg::Use-Pty=0'
        )
        repo_name = quote(self.settings.repo_name)
        repo_url = quote(self.settings.repo_url)
        branch = quote(self.settings.branch)
        steps = [
            (
                'cloud-init',
                'if command -v cloud-init >/dev/null 2>&1; then '
                'sudo timeout 900 cloud-init status --wait; fi',
            ),
            (
                'apt-locks',
                'waited=0; while sudo fuser '
                '/var/lib/dpkg/lock-frontend /var/lib/dpkg/lock '
                '/var/cache/apt/archives/lock /var/lib/apt/lists/lock '
                '>/dev/null 2>&1; do '
                'echo "apt/dpkg lock busy; waited ${waited}s"; '
                'sudo fuser -v /var/lib/dpkg/lock-frontend '
                '/var/lib/dpkg/lock /var/cache/apt/archives/lock '
                '/var/lib/apt/lists/lock 2>&1 || true; '
                'if [ "$waited" -ge 900 ]; then exit 124; fi; '
                'sleep 10; waited=$((waited + 10)); done',
            ),
            (
                'dpkg-configure',
                'sudo env DEBIAN_FRONTEND=noninteractive '
                'APT_LISTCHANGES_FRONTEND=none NEEDRESTART_MODE=a '
                'timeout 900 dpkg --configure -a',
            ),
            ('apt-update', f'{apt} update'),
            (
                'base-packages',
                f'{apt} install -y build-essential cmake curl git '
                'software-properties-common',
            ),
            (
                'enable-universe',
                'sudo timeout --signal=TERM --kill-after=30s 300 '
                'env DEBIAN_FRONTEND=noninteractive '
                'add-apt-repository --yes --no-update universe',
            ),
            ('apt-update-universe', f'{apt} update'),
            (
                'clang-14',
                f'{apt} install -y clang-14 llvm-14 llvm-14-dev libclang-14-dev',
            ),
            (
                'rustup',
                'if [ ! -x "$HOME/.cargo/bin/rustup" ]; then '
                'timeout --signal=TERM --kill-after=30s 900 '
                'bash -o pipefail -c \'curl --proto "=https" --tlsv1.2 -sSfL '
                '--retry 3 --retry-all-errors --connect-timeout 15 --max-time 300 '
                'https://sh.rustup.rs | sh -s -- -y\'; fi; '
                'source "$HOME/.cargo/env"; '
                'timeout 600 "$HOME/.cargo/bin/rustup" default stable',
            ),
            (
                'configure-clang',
                'sudo update-alternatives --install /usr/bin/clang clang '
                '/usr/bin/clang-14 140 && '
                'sudo update-alternatives --install /usr/bin/clang++ clang++ '
                '/usr/bin/clang++-14 140 && '
                'sudo update-alternatives --set clang /usr/bin/clang-14 && '
                'sudo update-alternatives --set clang++ /usr/bin/clang++-14 && '
                'if ! grep -q "LIBCLANG_PATH=/usr/lib/llvm-14/lib" '
                '"$HOME/.cargo/env"; then '
                'printf "\\nexport PATH=/usr/lib/llvm-14/bin:\\$PATH'
                '\\nexport CC=/usr/bin/clang-14'
                '\\nexport CXX=/usr/bin/clang++-14'
                '\\nexport CLANG_PATH=/usr/bin/clang-14'
                '\\nexport LIBCLANG_PATH=/usr/lib/llvm-14/lib'
                '\\nexport CXXFLAGS=\\\"-include cstdint\\\"\\n" '
                '>> "$HOME/.cargo/env"; fi',
            ),
            (
                'verify-toolchain',
                'source "$HOME/.cargo/env"; '
                'rustc --version && cargo --version && '
                'clang-14 --version | head -n 1 && '
                'test "$CC" = /usr/bin/clang-14 && '
                'test "$LIBCLANG_PATH" = /usr/lib/llvm-14/lib',
            ),
            (
                'repository',
                'retry() { attempt=1; while ! "$@"; do '
                'if [ "$attempt" -ge 3 ]; then return 1; fi; '
                'sleep $((attempt * 5)); attempt=$((attempt + 1)); done; }; '
                'export GIT_TERMINAL_PROMPT=0; '
                f'if [ -d {repo_name}/.git ]; then '
                f'retry timeout 600 git -C {repo_name} fetch --prune origin && '
                f'git -C {repo_name} checkout {branch} && '
                f'git -C {repo_name} merge --ff-only origin/{branch}; '
                f'elif [ -e {repo_name} ]; then '
                f'echo "Repository path exists but is not a git checkout: {repo_name}" >&2; '
                'exit 1; else '
                f'retry timeout 600 git clone --branch {branch} --single-branch '
                f'{repo_url} {repo_name}; fi && '
                f'git -C {repo_name} rev-parse --short HEAD',
            ),
        ]

        output_lock = Lock()

        def status(message):
            with output_lock:
                print(message, flush=True)

        def install_host(host):
            connection = None
            output_tail = deque(maxlen=12)

            def remember_output(result):
                if result is None:
                    return
                for stream in ('stdout', 'stderr'):
                    text = getattr(result, stream, '') or ''
                    for line in text.splitlines():
                        if line.strip():
                            output_tail.append(line)

            def error_with_output(message):
                if not output_tail:
                    return message
                lines = '\n'.join(f'      {line}' for line in output_tail)
                return f'{message}\n    Last output (up to 12 lines):\n{lines}'

            try:
                for attempt in range(1, 6):
                    status(
                        f'[INSTALL][{host}][connect] START '
                        f'attempt={attempt}/5'
                    )
                    connection = Connection(
                        host,
                        user='ubuntu',
                        connect_kwargs=self.connect,
                        connect_timeout=30,
                    )
                    try:
                        connection.open()
                        status(f'[INSTALL][{host}][connect] OK')
                        break
                    except Exception as error:
                        connection.close()
                        if attempt == 5:
                            raise
                        message = str(error).replace('\n', ' | ')
                        status(
                            f'[INSTALL][{host}][connect] RETRY '
                            f'in={attempt * 5}s error={message}'
                        )
                        sleep(attempt * 5)
                try:
                    for step, command in steps:
                        status(f'[INSTALL][{host}][{step}] START')
                        prefix = f'[INSTALL][{host}][{step}][output] '
                        wrapped = (
                            'set -e -o pipefail; '
                            f'{{ {command}; }} 2>&1 | '
                            f'sed -u {quote(f"s/^/{prefix}/")}'
                        )
                        heartbeat_stop = Event()
                        started_at = monotonic()

                        def report_heartbeat():
                            while not heartbeat_stop.wait(30):
                                elapsed = int(monotonic() - started_at)
                                status(
                                    f'[INSTALL][{host}][{step}] RUNNING '
                                    f'elapsed={elapsed}s'
                                )

                        heartbeat = Thread(
                            target=report_heartbeat,
                            name=f'install-heartbeat-{host}-{step}',
                            daemon=True,
                        )
                        heartbeat.start()
                        try:
                            result = connection.run(
                                f'bash -lc {quote(wrapped)}',
                                hide=False,
                                in_stream=False,
                                pty=False,
                                warn=True,
                            )
                        except Exception as error:
                            remember_output(getattr(error, 'result', None))
                            message = str(error).replace('\n', ' | ')
                            detail = error_with_output(
                                f'{step} raised {type(error).__name__}: '
                                f'{message}'
                            )
                            status(
                                f'[INSTALL][{host}][{step}] ERROR '
                                f'{type(error).__name__}: {message}'
                            )
                            return host, detail
                        finally:
                            heartbeat_stop.set()
                            heartbeat.join()

                        remember_output(result)
                        if result.failed:
                            error = error_with_output(
                                f'{step} exited with status {result.exited}'
                            )
                            status(
                                f'[INSTALL][{host}][{step}] ERROR '
                                f'exit={result.exited}'
                            )
                            return host, error
                        status(f'[INSTALL][{host}][{step}] OK')
                finally:
                    if connection is not None:
                        connection.close()
                status(f'[INSTALL][{host}] COMPLETE')
                return host, None
            except Exception as error:
                message = str(error).replace('\n', ' | ')
                detail = error_with_output(
                    f'{type(error).__name__}: {message}'
                )
                status(f'[INSTALL][{host}] ERROR {detail}')
                return host, detail

        results = {}
        with ThreadPoolExecutor(max_workers=len(hosts)) as executor:
            futures = {
                executor.submit(install_host, host): host for host in hosts
            }
            for future in as_completed(futures):
                host = futures[future]
                try:
                    _, error = future.result()
                except Exception as failure:
                    message = str(failure).replace('\n', ' | ')
                    error = f'{type(failure).__name__}: {message}'
                    status(f'[INSTALL][{host}][internal] ERROR {error}')
                results[host] = error
                failures = sum(value is not None for value in results.values())
                status(
                    f'[INSTALL][PROGRESS] completed={len(results)}/{len(hosts)} '
                    f'ok={len(results) - failures} failed={failures}'
                )

        failed = {host: error for host, error in results.items() if error}
        Print.info(
            f'Install summary: {len(hosts) - len(failed)}/{len(hosts)} '
            'nodes completed successfully'
        )
        for host in sorted(hosts):
            result = failed.get(host)
            if result:
                indented = result.replace('\n', '\n    ')
                Print.info(f'  {host}: FAILED\n    {indented}')
            else:
                Print.info(f'  {host}: OK')

        if failed:
            details = '\n\n'.join(
                f'{host}: {error}' for host, error in sorted(failed.items())
            )
            raise BenchError(
                'Failed to install repo on testbed', RuntimeError(details)
            )
        Print.heading(f'Initialized testbed of {len(hosts)} nodes')

    def kill(self, hosts=[], delete_logs=False):
        assert isinstance(hosts, list)
        assert isinstance(delete_logs, bool)
        hosts = hosts if hosts else self.manager.hosts(flat=True)
        delete_logs = CommandMaker.clean_logs() if delete_logs else 'true'
        cmd = [delete_logs, f'({CommandMaker.kill()} || true)']
        try:
            g = Group(*hosts, user='ubuntu', connect_kwargs=self.connect)
            g.run(' && '.join(cmd), hide=True)
        except GroupException as e:
            raise BenchError('Failed to kill nodes', FabricError(e))

    def _select_hosts(self, bench_parameters):
        # Collocate the primary and its workers on the same machine.
        if bench_parameters.collocate:
            nodes = max(bench_parameters.nodes)

            # Ensure there are enough hosts.
            hosts = self.manager.hosts()
            if sum(len(x) for x in hosts.values()) < nodes:
                return []

            # Select the hosts in different data centers.
            ordered = zip(*hosts.values())
            ordered = [x for y in ordered for x in y]
            return ordered[:nodes]

        # Spawn the primary and each worker on a different machine. Each
        # authority runs in a single data center.
        else:
            primaries = max(bench_parameters.nodes)

            # Ensure there are enough hosts.
            hosts = self.manager.hosts()
            if len(hosts.keys()) < primaries:
                return []
            for ips in hosts.values():
                if len(ips) < bench_parameters.workers + 1:
                    return []

            # Ensure the primary and its workers are in the same region.
            selected = []
            for region in list(hosts.keys())[:primaries]:
                ips = list(hosts[region])[:bench_parameters.workers + 1]
                selected.append(ips)
            return selected

    def _background_run(self, host, command, log_file):
        name = splitext(basename(log_file))[0]
        cmd = f'tmux new -d -s "{name}" "{command} |& tee {log_file}"'
        c = Connection(host, user='ubuntu', connect_kwargs=self.connect)
        output = c.run(cmd, hide=True)
        self._check_stderr(output)

    def _update(self, hosts, collocate):
        if collocate:
            ips = list(set(hosts))
        else:
            ips = list(set([x for y in hosts for x in y]))

        Print.info(
            f'Updating {len(ips)} machines (branch "{self.settings.branch}")...'
        )
        cmd = [
            f'(cd {self.settings.repo_name} && git fetch -f)',
            f'(cd {self.settings.repo_name} && git checkout -f {self.settings.branch})',
            f'(cd {self.settings.repo_name} && git pull -f)',
            'source $HOME/.cargo/env',
            f'(cd {self.settings.repo_name}/node && {CommandMaker.compile()})',
            CommandMaker.alias_binaries(
                f'./{self.settings.repo_name}/target/release/'
            )
        ]
        g = Group(*ips, user='ubuntu', connect_kwargs=self.connect)
        g.run(' && '.join(cmd), hide=True)

    def _config(self, hosts, node_parameters, bench_parameters):
        Print.info('Generating configuration files...')

        # Cleanup all local configuration files.
        cmd = CommandMaker.cleanup()
        subprocess.run([cmd], shell=True, stderr=subprocess.DEVNULL)

        # Recompile the latest code.
        cmd = CommandMaker.compile().split()
        subprocess.run(cmd, check=True, cwd=PathMaker.node_crate_path())

        # Create alias for the client and nodes binary.
        cmd = CommandMaker.alias_binaries(PathMaker.binary_path())
        subprocess.run([cmd], shell=True)

        # Generate configuration files.
        keys = []
        key_files = [PathMaker.key_file(i) for i in range(len(hosts))]
        for filename in key_files:
            cmd = CommandMaker.generate_key(filename).split()
            subprocess.run(cmd, check=True)
            keys += [Key.from_file(filename)]

        names = [x.name for x in keys]

        if bench_parameters.collocate:
            workers = bench_parameters.workers
            addresses = OrderedDict(
                (x, [y] * (workers + 1)) for x, y in zip(names, hosts)
            )
        else:
            addresses = OrderedDict(
                (x, y) for x, y in zip(names, hosts)
            )
        committee = Committee(addresses, self.settings.base_port)
        committee.print(PathMaker.committee_file())

        node_parameters.print(PathMaker.parameters_file())

        # Cleanup all nodes and upload configuration files.
        progress = progress_bar(names, prefix='Uploading config files:')
        for i, name in enumerate(progress):
            for ip in committee.ips(name):
                c = Connection(ip, user='ubuntu', connect_kwargs=self.connect)
                c.run(f'{CommandMaker.cleanup()} || true', hide=True)
                c.put(PathMaker.committee_file(), '.')
                c.put(PathMaker.key_file(i), '.')
                c.put(PathMaker.parameters_file(), '.')

        return committee

    def _run_single(self, rate, committee, bench_parameters, node_parameters, debug=False):
        faults = bench_parameters.faults

        # Kill any potentially unfinished run and delete logs.
        hosts = committee.ips()
        self.kill(hosts=hosts, delete_logs=True)

        # Run every client; its pre-generated schedule controls silent slots.
        Print.info('Booting clients...')
        workers_addresses = committee.workers_addresses(0)
        rate_share = ceil(rate / committee.workers())
        names = list(committee.json['authorities'])
        silence_slot_ms = client_silence_slot_ms(
            node_parameters.json['max_header_delay']
        )
        silence_schedules = build_client_schedules(
            names, faults, bench_parameters.duration, silence_slot_ms
        )
        for i, addresses in enumerate(workers_addresses):
            for (id, address) in addresses:
                host = Committee.ip(address)
                cmd = CommandMaker.run_client(
                    address,
                    bench_parameters.tx_size,
                    rate_share,
                    [x for y in workers_addresses for _, x in y],
                    silence_schedules[names[i]],
                    silence_slot_ms,
                )
                log_file = PathMaker.client_log_file(i, id)
                self._background_run(host, cmd, log_file)

        # Run every primary; adversaries are selected independently each round.
        Print.info('Booting primaries...')
        for i, address in enumerate(committee.primary_addresses(0)):
            host = Committee.ip(address)
            cmd = CommandMaker.run_primary(
                PathMaker.key_file(i),
                PathMaker.committee_file(),
                PathMaker.db_path(i),
                PathMaker.parameters_file(),
                debug=debug,
                faults=faults,
            )
            log_file = PathMaker.primary_log_file(i)
            self._background_run(host, cmd, log_file)

        # Run every worker; batch production follows the primary's round state.
        Print.info('Booting workers...')
        for i, addresses in enumerate(workers_addresses):
            for (id, address) in addresses:
                host = Committee.ip(address)
                cmd = CommandMaker.run_worker(
                    PathMaker.key_file(i),
                    PathMaker.committee_file(),
                    PathMaker.db_path(i, id),
                    PathMaker.parameters_file(),
                    id,  # The worker's id.
                    debug=debug
                )
                log_file = PathMaker.worker_log_file(i, id)
                self._background_run(host, cmd, log_file)

        # Wait for all transactions to be processed.
        duration = bench_parameters.duration
        for _ in progress_bar(range(20), prefix=f'Running benchmark ({duration} sec):'):
            sleep(ceil(duration / 20))
        self.kill(hosts=hosts, delete_logs=False)

    def _logs(self, committee, faults):
        # Delete local logs (if any).
        cmd = CommandMaker.clean_logs()
        subprocess.run([cmd], shell=True, stderr=subprocess.DEVNULL)

        # Download log files.
        workers_addresses = committee.workers_addresses(0)
        progress = progress_bar(workers_addresses, prefix='Downloading workers logs:')
        for i, addresses in enumerate(progress):
            for id, address in addresses:
                host = Committee.ip(address)
                c = Connection(host, user='ubuntu', connect_kwargs=self.connect)
                c.get(
                    PathMaker.client_log_file(i, id), 
                    local=PathMaker.client_log_file(i, id)
                )
                c.get(
                    PathMaker.worker_log_file(i, id), 
                    local=PathMaker.worker_log_file(i, id)
                )

        primary_addresses = committee.primary_addresses(0)
        progress = progress_bar(primary_addresses, prefix='Downloading primaries logs:')
        for i, address in enumerate(progress):
            host = Committee.ip(address)
            c = Connection(host, user='ubuntu', connect_kwargs=self.connect)
            c.get(
                PathMaker.primary_log_file(i), 
                local=PathMaker.primary_log_file(i)
            )

        # Parse logs and return the parser.
        Print.info('Parsing logs and computing performance...')
        return LogParser.process(PathMaker.logs_path(), faults=faults)

    def run(self, bench_parameters_dict, node_parameters_dict, debug=False):
        assert isinstance(debug, bool)
        Print.heading('Starting remote benchmark')
        try:
            bench_parameters = BenchParameters(bench_parameters_dict)
            node_parameters = NodeParameters(node_parameters_dict)
        except ConfigError as e:
            raise BenchError('Invalid nodes or bench parameters', e)

        # Select which hosts to use.
        selected_hosts = self._select_hosts(bench_parameters)
        if not selected_hosts:
            Print.warn('There are not enough instances available')
            return

        # Update nodes.
        try:
            self._update(selected_hosts, bench_parameters.collocate)
        except (GroupException, ExecutionError) as e:
            e = FabricError(e) if isinstance(e, GroupException) else e
            raise BenchError('Failed to update nodes', e)

        # Upload all configuration files.
        try:
            committee = self._config(
                selected_hosts, node_parameters, bench_parameters
            )
        except (subprocess.SubprocessError, GroupException) as e:
            e = FabricError(e) if isinstance(e, GroupException) else e
            raise BenchError('Failed to configure nodes', e)

        # Run benchmarks.
        for n in bench_parameters.nodes:
            committee_copy = deepcopy(committee)
            committee_copy.remove_nodes(committee.size() - n)

            for r in bench_parameters.rate:
                Print.heading(f'\nRunning {n} nodes (input rate: {r:,} tx/s)')

                # Run the benchmark.
                for i in range(bench_parameters.runs):
                    Print.heading(f'Run {i+1}/{bench_parameters.runs}')
                    try:
                        self._run_single(
                            r, committee_copy, bench_parameters, node_parameters, debug
                        )

                        faults = bench_parameters.faults
                        logger = self._logs(committee_copy, faults)
                        logger.print(PathMaker.result_file(
                            faults,
                            n, 
                            bench_parameters.workers,
                            bench_parameters.collocate,
                            r, 
                            bench_parameters.tx_size, 
                        ))
                    except (subprocess.SubprocessError, GroupException, ParseError) as e:
                        self.kill(hosts=selected_hosts)
                        if isinstance(e, GroupException):
                            e = FabricError(e)
                        Print.error(BenchError('Benchmark failed', e))
                        continue

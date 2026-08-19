# Windows 使用五个 Region PEM 控制 Tusk

本文适用于：本地控制电脑是 Windows；Linux 服务器用户是 `ubuntu`；节点分布在五个 AWS Region；每个 Region 使用不同 PEM；Linux `node-0` 同时是控制机和协议节点 0。

五个 PEM 对应五个 Region，而不是五个协议。Orca-A、Orca-B、Bullshark 和 Tusk 可以共用同一套区域密钥配置。

## 1. Windows 登录 node-0

在 PowerShell 中执行：

~~~powershell
ssh -V
scp -V
$Node0Pem = "C:\Users\YOUR_NAME\Downloads\eu-west-2.pem"
icacls $Node0Pem /inheritance:r
icacls $Node0Pem /grant:r "$($env:USERNAME):(R)"
ssh -i $Node0Pem ubuntu@NODE0_PUBLIC_IP
~~~

## 2. 上传五个区域 PEM

~~~powershell
$PemDir = "C:\Users\YOUR_NAME\Downloads"
ssh -i $Node0Pem ubuntu@NODE0_PUBLIC_IP "mkdir -p /home/ubuntu/.ssh && chmod 700 /home/ubuntu/.ssh"
scp -i $Node0Pem "$PemDir\us-east-1.pem" ubuntu@NODE0_PUBLIC_IP:/home/ubuntu/.ssh/
scp -i $Node0Pem "$PemDir\sa-east-1.pem" ubuntu@NODE0_PUBLIC_IP:/home/ubuntu/.ssh/
scp -i $Node0Pem "$PemDir\eu-west-2.pem" ubuntu@NODE0_PUBLIC_IP:/home/ubuntu/.ssh/
scp -i $Node0Pem "$PemDir\ap-southeast-1.pem" ubuntu@NODE0_PUBLIC_IP:/home/ubuntu/.ssh/
scp -i $Node0Pem "$PemDir\ap-southeast-2.pem" ubuntu@NODE0_PUBLIC_IP:/home/ubuntu/.ssh/
ssh -i $Node0Pem ubuntu@NODE0_PUBLIC_IP
~~~

PEM 是敏感文件，禁止提交到 GitHub。

## 3. node-0 按区域选择密钥

以下 CIDR 仅为示例，请替换成实际 VPC CIDR：

~~~bash
chmod 400 /home/ubuntu/.ssh/*.pem
nano /home/ubuntu/.ssh/config
~~~

~~~sshconfig
Host 10.10.*
    User ubuntu
    IdentityFile /home/ubuntu/.ssh/us-east-1.pem

Host 10.20.*
    User ubuntu
    IdentityFile /home/ubuntu/.ssh/sa-east-1.pem

Host 10.30.*
    User ubuntu
    IdentityFile /home/ubuntu/.ssh/eu-west-2.pem

Host 10.40.*
    User ubuntu
    IdentityFile /home/ubuntu/.ssh/ap-southeast-1.pem

Host 10.50.*
    User ubuntu
    IdentityFile /home/ubuntu/.ssh/ap-southeast-2.pem

Host 10.*
    StrictHostKeyChecking accept-new
    ConnectTimeout 8
    ServerAliveInterval 5
    ServerAliveCountMax 2
~~~

~~~bash
chmod 600 /home/ubuntu/.ssh/config
ssh -G 10.10.1.10 | grep -E '^(user|identityfile) '
ssh ubuntu@NODE_IN_EACH_REGION_PRIVATE_IP hostname
~~~

## 4. hosts 文件与运行

~~~bash
git clone https://github.com/DrDaydream/Tusk-Ubuntu24.git /home/ubuntu/Tusk-Ubuntu24
cd /home/ubuntu/Tusk-Ubuntu24
cp deploy/hosts-10.txt.example deploy/hosts-10.txt
nano deploy/hosts-10.txt
~~~

每行只写一个跨 Region 私网互联后可达的 Private IPv4，第一行必须是 node-0。不能写 `ubuntu@`、端口、逗号或主机名。

先按 [AWS 完整部署文档](README-AWS.md) 安装、编译和分发配置。`SSH_KEY` 必须留空，让脚本读取 `~/.ssh/config`：

~~~bash
cd /home/ubuntu/Tusk-Ubuntu24
REMOTE_USER=ubuntu \
REMOTE_DIR=/home/ubuntu/Tusk-Ubuntu24 \
HOSTS_FILE=/home/ubuntu/Tusk-Ubuntu24/deploy/hosts-10.txt \
SSH_KEY= \
./run-multi-servers.sh 10 20 10000
~~~

三个参数依次为节点数、运行秒数和集群总输入 TPS。安全组、跨 Region 路由及 10/20/50 节点配置以 AWS 完整部署文档为准。

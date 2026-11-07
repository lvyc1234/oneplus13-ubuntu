# 安装、重装与恢复

## 适用范围与备份

本流程针对已解锁的一加 13 PJZ110、24 GB RAM / 1 TB 存储参考配置。Android 位于 A 槽位并可正常启动，KernelSU 已可工作，Boot Control HAL 与 ColorOS C.93 相容。安装器校验 4096 字节逻辑扇区、磁盘容量和分区名。其余配置应先完成独立适配。

首次分区把 userdata 调整为 448 GiB、linux_rootfs 设为 64 GiB、剩余空间设为 shared_media。fresh 模式清除 Android 用户数据。replace-rootfs 模式清除 Ubuntu 根分区中的账号、软件和文件，并保留 shared_media。先备份两套系统中所需的个人数据。

保存当前原厂 ROM、启动链恢复镜像、完整 GPT 备份及 boot_a / boot_b 镜像。安装器的预检会导出两个 UFS LUN 的 GPT 和两个 boot 分区，存入指定的 RAM 或主机网络目录。RAM 里的备份必须先复制到电脑，再开始实际安装。

## 准备构建产物

按 [BUILD](BUILD.md) 下载 full 的 oneplus13-persistent artifact，解压为电脑上的 oneplus13-persistent 目录，在目录内执行校验。

~~~sh
sha256sum --check SHA256SUMS
~~~

boot.img 是正式 B 槽位启动镜像，boot-ram.img 用于临时安装环境，rootfs.squashfs 与 persistent-overlay.tar 共同组成干净的持久化根文件系统。将整个解压目录打包以传入临时系统。

~~~sh
tar -C oneplus13-persistent -cf op13-install.tar .
python3 -m http.server 8002 --bind 0.0.0.0
~~~

安装材料 HTTP 服务运行在电脑的 8002 端口，目录应只包含本次安装材料。下面 HOST_ADDRESS 替换为手机通过 USB 网络可访问的电脑地址。

临时 RAM 系统还需要从电脑获取根文件系统分块。在第二个终端进入解压后的 oneplus13-persistent 目录，先计算 rootfs.squashfs 的 SHA-256，再把输出的哈希填入 ROOTFS_SHA256，启动以下服务并保持运行。

~~~sh
sha256sum rootfs.squashfs
python3 tools/serve-rootfs.py --image rootfs.squashfs --sha256 ROOTFS_SHA256 --bind 0.0.0.0
~~~

该服务在 8000 端口提供 RAM 镜像命令行绑定的分块内容。手机 USB 网络地址为 172.16.13.1，电脑必须使用 172.16.13.2/24。临时镜像的 DHCP 会提供该地址，可在 USB 网卡连接中启用 DHCP，也可手动配置。检查本机防火墙允许这两个服务通过 USB 网卡访问。

## 启动临时 RAM 系统

在 bootloader Fastboot 中确认以下状态。

~~~sh
fastboot getvar product
fastboot getvar unlocked
fastboot getvar current-slot
fastboot getvar partition-size:boot_b
fastboot getvar slot-unbootable:a
~~~

核对为目标设备、解锁状态、A 槽位、boot_b 为 0x6000000、A 可启动后临时启动。

~~~sh
fastboot boot oneplus13-persistent/boot-ram.img
~~~

使用 USB 网络上的 RAM 诊断控制台进入临时系统。该接口提供 root 权限，限安装期间通过本机 USB 使用，正式 boot.img 默认关闭该接口。电脑可使用 telnet 连接 172.16.13.1 的 23 端口。确认已进入完整 RAM 系统、Python 和 systemd 可用后执行以下命令，避免诊断计时器在安装期间自动结束会话。若日志显示根文件系统分块下载失败，检查电脑地址和 8000 服务，重新从 Fastboot 临时启动。

~~~sh
systemctl stop oneplus13-ram-deadline.timer
mkdir -p /run/op13-install
curl --fail http://HOST_ADDRESS:8002/op13-install.tar | tar -xf - -C /run/op13-install
cd /run/op13-install
sha256sum --check SHA256SUMS
~~~

## 只读预检

选择模式。已存在本项目分区时使用 replace-rootfs，原厂布局使用 fresh。

~~~sh
mkdir /run/op13-preflight
python3 tools/install-persistent.py --bundle /run/op13-install --backup /run/op13-preflight --mode replace-rootfs
~~~

首次安装时将 mode 改为 fresh。检查生成的 plan.json。用临时 HTTP 服务或可信网络传输将整个 /run/op13-preflight 保存到电脑。例如在 RAM 系统运行以下服务，随后在电脑下载四份备份和 plan.json。

~~~sh
python3 -m http.server 8001 --bind 172.16.13.1 --directory /run/op13-preflight
~~~

备份保存在电脑且核对哈希后，结束该服务，回到诊断控制台继续。下一个步骤会修改分区或文件系统。

## 写入文件系统

现有双系统重装 Ubuntu 时执行以下命令。

~~~sh
mkdir /run/op13-install-backup
python3 tools/install-persistent.py --bundle /run/op13-install --backup /run/op13-install-backup --mode replace-rootfs --apply --erase-ubuntu
~~~

首次调整原厂布局时使用以下命令。

~~~sh
mkdir /run/op13-install-backup
python3 tools/install-persistent.py --bundle /run/op13-install --backup /run/op13-install-backup --mode fresh --apply --erase-android
~~~

安装器在预检和备份成功后停止 RAM 写保护服务及硬件初始化，临时屏蔽两条写保护 udev 规则，再格式化根分区、展开全新 rootfs 和 A/B overlay，配置共享盘并生成绑定本次 B 镜像哈希及共享盘 UUID 的 OnePlus13-DualBoot.zip。保存完整 /run/op13-install-backup 到电脑，特别是模块 ZIP 和分区备份。若安装报错，保持临时系统运行并核对错误及备份，按当前分区状态重新预检后恢复。

首次分区后还需在 A 槽位的原厂 recovery 执行清除用户数据，以新分区尺寸重新建立 Android userdata。选择 A 并进入原厂恢复环境，完成 Android 首次设置，再按当前系统的方式启用 KernelSU。现有分区的 replace-rootfs 流程可继续下一步。

## 安装正式启动镜像

从临时系统请求进入 bootloader。

~~~sh
systemctl --reboot-argument=bootloader reboot
~~~

电脑再次核对设备及 boot_b 大小。使用同一次构建的正式镜像写 B，并开启接电关机所需的 bootloader 设置。

~~~sh
fastboot flash boot_b oneplus13-persistent/boot.img
fastboot oem off-mode-charge 1
fastboot getvar off-mode-charge
fastboot getvar charger-screen-enabled
fastboot set_active b
fastboot reboot
~~~

两个充电设置应均为 1。Ubuntu 首次启动进入 GNOME Initial Setup，在手机自行创建账号和密码。完成后连接 Wi-Fi，公共共享目录 ACL 会在第一账号创建后约三十秒内配置。

## 完成 Android 切换模块

在 Ubuntu 应用菜单点击“重启到 Android”并认证。把刚才保存到电脑的 OnePlus13-DualBoot.zip 传到 Android，使用 KernelSU 安装并重启 Android。等待系统启动和首次解锁完成，在 KernelSU 模块页点击 OnePlus 13 Dual Boot 的“操作”进入 Ubuntu。

共享盘在 Android 首次解锁后挂载。模块会把公共媒体接入 shared_media，同时保留加密 userdata 中的 Android 与 OBB 目录。模块与 B 镜像的哈希必须匹配，重新构建并更新 B 后应一并更新模块及 Ubuntu 中的校验文件。

## 安装后验收

- 两次 Android → Ubuntu → Android 往返，检查每次均可进入桌面。
- Wi-Fi 自动连接，横竖屏旋转，触控和 GPU 动画。
- 开机前连接电脑 USB / 原装充电器，开机后识别充电，拔插图标方向正确。
- 短按电源锁屏，再按唤醒，认证后进入桌面。
- 断线关机保持关闭，接电关机保持黑屏充电，拔线后保持关闭，长按电源能开机。
- 两系统公共媒体创建、改名、读取，Android 应用私有目录仍正常。

MAC 稳定后，DHCP 地址通常可沿用租约。需要长期固定地址时，在路由器对该连接的 MAC 设置 DHCP 保留。

## 恢复

启动异常时先进入 bootloader，将 A 设为活动槽位并重启。

~~~sh
fastboot set_active a
fastboot reboot
~~~

B 镜像被 OTA 或实验构建覆盖时，先启动临时 RAM 系统，备份当前数据并按 replace-rootfs 重装，再安装匹配的正式 B 镜像和模块。该模式会重置 Ubuntu 账号及根分区文件。

恢复完整 Android 原厂布局需要设备本身的 GPT、原厂 ROM 和完整 userdata 恢复流程。GPT 恢复涉及磁盘几何，必须核对备份对应当前手机、LUN 与扇区大小。恢复原厂分区后 userdata 需要重新格式化。请以保留的设备备份和官方恢复工具为依据。

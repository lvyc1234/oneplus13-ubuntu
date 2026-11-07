# 当前状态

更新于 2026-11-07。参考设备为 OnePlus 13 PJZ110、24 GB RAM、1 TB 存储，原系统为 ColorOS C.93。当前稳定基线为 r14 持久化启动方案。

## 实机通过的范围

用户最终验收通过原生屏幕和触控、Adreno 830v1 GPU 桌面、Wi-Fi 自动连接、横竖屏旋转、开机前接电识别、原装充电器和电脑 USB 的充电及拔插显示、Android A 与 Ubuntu B 的设备内双向切换，以及短按电源锁屏和认证后唤醒。

关机分别在断线、电脑 USB 和原装充电器条件下验收。接电时保持黑屏充电模式，拔线后保持关机，长按电源能正常启动。多次重启后 Wi-Fi 的稳定网络身份和路由器分配地址保持一致。DHCP 地址仍由路由器管理。

GPU 使用 Mesa 26.2.3，实际渲染器报告 Adreno 830v1，GNOME 的 GPU 引擎计数随动画增加。旋转曾触发无效局部重绘区域，当前通过 CLUTTER_PAINT=disable-clipped-redraws 与 GSK_RENDERER=gl 修复。用户确认桌面恢复流畅，全量重绘的额外耗电尚未测量。

共享盘通过 Android 文件管理器改名并在 Linux ext4 底层核对。Android 加密应用目录和 OBB 由独立 bind 保留，公共媒体使用 shared_media。Linux 第一个账号创建后补齐公共目录 ACL。

## 固化到源码的内容

- 0026 内核补丁修正电池电流方向、供电来源及初次通知。
- persistent-hardware 初始化 CPU 前置、ADSP、原生显示和触控。
- persistent-peripherals 初始化 Wi-Fi、SSC 加速度计和 SensorProxy。
- persistent-launch 在移动 proc 后从新根读取命令行，选择桌面或充电 target。
- 网络使用稳定 cloned MAC 和基于 MAC 的 DHCP client ID。
- qbootctl 管理 Linux 到 Android 的槽位选择，Android 使用 Boot Control HAL。
- B 启动镜像和 qbootctl 的 SHA-256 在切换及启动成功标记前核对。

## 构建验证与实机验收

新构建使用同一源码提交的内核、模块、Mesa、Ubuntu 根文件系统和 SSC 组件。打包器独立读取 SquashFS，检查普通账号数量为零、machine-id 为空、GNOME 首次设置启用及 GPU 修复配置存在。每次构建生成新的 AVB 签名密钥、会话随机值和镜像哈希，因此功能复现与旧镜像字节完全一致属于不同验证目标。

当前稳定手机的功能已有实机验收。公开构建与新的安装器仍需针对具体产物执行安装和设备验收。新安装不包含现有手机的用户数据或网络配置。

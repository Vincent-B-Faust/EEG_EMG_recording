# EEG-EMG Recording

> 设备型号说明：需求中写的是 `NI USB-9001`，但未找到这个型号的 NI 官方资料；可核实的相近型号是 `NI USB-6001`，因此默认配置按 USB-6001 构建。采集代码本身会通过 NI-DAQmx 枚举实际设备和通道；正式接线前请核对采集卡铭牌，并以 `list_ni_devices.cmd` 的输出为准。

独立的 NI-DAQmx EEG/EMG 睡眠记录系统。它使用 WPI DAM50 的模拟输出，经 NI USB-6001 连续采集，支持自由配置通道、实时预览、增量 HDF5 保存、外部事件/停止控制，以及与 Thermocamera 实验 session 对齐。

## 快速开始

本项目与 Thermocamera 项目共用 conda 环境 `thermocamera_public`，不另建环境。

1. 双击 `install_shared_env.cmd`，将本项目及 NI/test 依赖安装到共享环境。
2. 连接 USB-6001，在 NI MAX 中确认设备可见。
3. 双击 `list_ni_devices.cmd`，确认输出中包含 USB-6001 和 `Dev*/ai0..ai7`。
4. 按真实接线修改 `configs/ni_usb6001_dam50.yaml`。
5. 双击 `start_gui.cmd`，在 GUI 中检查通道、增益、采样率和预览后开始记录。

没有采集卡时，可双击 `run_fake_demo.cmd`。它会生成 20 秒、5 路合成 EEG/EMG/SYNC 数据并显示实时预览。也可指定时长，例如：

```cmd
run_fake_demo.cmd 60
```

## 硬件关系

记录链路是：

```text
EEG/EMG electrodes -> DAM50 differential input -> DAM50 BNC output -> USB-6001 AI -> PC
```

关键限制：

- 一台 DAM50 只有一个 BNC 输出，因此只能提供一个独立记录通道。
- 一只鼠同时记录 EEG 和 EMG，至少需要两台 DAM50。
- 两只鼠各记录 EEG 和 EMG，需要四台 DAM50，并占满 USB-6001 的四个 DIFF 逻辑通道。
- USB-6001 在 DIFF 模式下只能配置 `ai0`、`ai1`、`ai2`、`ai3`。NI-DAQmx 自动使用 `ai4..ai7` 作为负端配对。
- USB-6001 的模拟输入总吞吐量是 20 kS/s，必须满足 `启用通道数 * 每通道采样率 <= 20000`。
- `configs/fake_demo.yaml` 中的 `ai4` 只是 fake 通道名，不代表真实 USB-6001 的第五个 DIFF 通道。

USB-6001 DIFF 配对如下：

| 配置中的逻辑通道 | 正端 | 负端 |
| --- | --- | --- |
| `ai0` | AI0 | AI4 |
| `ai1` | AI1 | AI5 |
| `ai2` | AI2 | AI6 |
| `ai3` | AI3 | AI7 |

以第一台 DAM50 为例，可将 BNC 中心信号接 AI0，BNC 屏蔽层/返回接 AI4，然后在配置中写 `physical_channel: ai0`。DAM50 由电池供电时，输出可能相对采集卡浮地；是否需要按 NI 手册增加 AI GND 偏置路径，应根据实际接地和 NI 官方差分接线图确认。不要把动物电极直接接到 USB-6001。

DAM50 的生物信号输入本身是差分输入。USB-6001 的 DIFF 是对 DAM50 BNC 输出的采集方式，两者不是同一层连接。动物 preparation ground、电极和 DAM50 输入应严格按 WPI 手册连接。

官方资料：

- [NI USB-6001 产品页](https://www.ni.com/en/shop/hardware/voltage/model-usb-6001)
- [NI USB-6001 Specifications](https://download.ni.com/support/manuals/374369a.pdf)
- [WPI DAM50 Instruction Manual](https://www.wpiinc.com/media/wysiwyg/pdf/dam50-im-082208.pdf)

## DAM50 设置

默认配置假定两台 DAM50 都使用 AC `1000x` 增益。必须让每个通道的
`amplifier_gain`、`amplifier_mode`、`hardware_highpass_hz` 和
`hardware_lowpass_hz` 与对应 DAM50 面板完全一致，并填写
`amplifier_serial` 以便追溯。

- HDF5 永远保存 DAM50 的原始输出电压，单位 V。
- 预览选择 `input_uv` 时，显示值为 `output_voltage_v * 1e6 / amplifier_gain`。
- DAM50 官方输出摆幅约为 ±8 V，因此默认 NI 输入范围使用 `[-10, 10] V`。
- EEG 常需保留慢波，WPI 手册建议慢信号使用较低的 LOW FILTER；EMG 可使用另一台 DAM50 独立设置硬件滤波。
- 软件中的 notch/bandpass 默认只影响预览，不会改变保存的原始数据。

第一次接线后，建议先用 DAM50 的 BAT/CAL 10 Hz 校准信号短时测试，确认极性、幅度、增益换算和通道名称，再连接正式实验。

## 通道配置

编辑 `acquisition.channels` 即可自由增删通道：

```yaml
acquisition:
  sample_rate_hz: 1000
  terminal_config: DIFF
  channels:
    - name: Mouse01_EEG
      mouse_id: Mouse01
      role: EEG
      physical_channel: ai0
      enabled: true
      amplifier_gain: 1000
    - name: Mouse01_EMG
      mouse_id: Mouse01
      role: EMG
      physical_channel: ai1
      enabled: true
      amplifier_gain: 1000
```

支持的常用 `role` 包括 `EEG`、`EMG`、`EOG`、`ECG`、`SYNC` 和 `AUX`。名称和物理通道在启用通道中必须唯一。GUI 可直接添加 EEG/EMG 行；其他角色可在 YAML 中配置。

重要字段：

| 字段 | 含义 |
| --- | --- |
| `backend` | `nidaqmx` 使用真机，`fake` 使用合成数据 |
| `device` | `auto` 自动选择第一个带 AI 的 NI 设备，也可指定 `Dev1` |
| `sample_rate_hz` | 每个通道的采样率，不是所有通道之和 |
| `block_duration_s` | 每次读取和写入的数据块时长 |
| `duration_s` | 固定记录时长；`null` 表示持续到用户或外部停止 |
| `terminal_config` | USB-6001 默认使用 `DIFF` |
| `input_range_v` | NI 端电压量程，默认覆盖 DAM50 的 ±8 V 输出 |
| `amplifier_gain` | 对应 DAM50 的真实硬件增益 |
| `amplifier_mode` | DAM50 的 AC/DC 模式 |
| `hardware_highpass_hz` / `hardware_lowpass_hz` | DAM50 面板滤波设置 |
| `storage.csv_enabled` | 记录时同步输出降采样 CSV |
| `storage.csv_decimation` | CSV 每隔多少个原始样本写一行 |

在线 CSV 使用整段记录的全局样本索引执行 decimation，因此不会在采集块边界重置。

## 独立 CLI

安装后可在 `thermocamera_public` 环境中运行：

```cmd
conda run --no-capture-output -n thermocamera_public eeg-emg-recording list-devices
conda run --no-capture-output -n thermocamera_public eeg-emg-recording validate --config configs\ni_usb6001_dam50.yaml
conda run --no-capture-output -n thermocamera_public eeg-emg-recording record --config configs\ni_usb6001_dam50.yaml
conda run --no-capture-output -n thermocamera_public eeg-emg-recording inspect --session D:\path\to\session
conda run --no-capture-output -n thermocamera_public eeg-emg-recording export-csv --session D:\path\to\session --decimation 10
```

`record` 可用 `--duration-s`、`--until-stop`、`--out-dir`、`--session-dir`、`--preview/--no-preview`、`--fake`、`--control-dir` 和 `--parent-session` 覆盖 YAML。`--until-stop` 会忽略 YAML 中的固定时长，适合由热像或总控决定结束时刻；`--session-dir` 指定的目录必须为空。

## 实时预览

GUI 将记录放在线程中执行，主窗口持续更新所有启用通道。预览滤波、缩放和单位显示不修改 HDF5 原始电压。

CLI 预览窗口中按 `Q` 或 `Esc` 可正常停止。停止时会完成 HDF5 flush、metadata 和 `complete.json`，不会把正常停止当作崩溃。

## 与 Thermocamera 同步

推荐使用同级 `Multi_recording_system` 作为唯一总控；它先启动 EEG/EMG，
等待 `ready.json`，再启动热像，并在主 session 中保存两套数据和统一 manifest。
Thermocamera 也保留直接托管 EEG 子进程的兼容模式。

手工调用共享 control 协议时，可把 EEG/EMG 保存到热像 session 内的独立子目录：

```cmd
eeg-emg-recording record ^
  --config configs\ni_usb6001_dam50.yaml ^
  --session-dir D:\thermal_session\eeg_emg ^
  --control-dir D:\thermal_session\sync\eeg_emg ^
  --parent-session D:\thermal_session ^
  --until-stop ^
  --no-preview
```

EEG/EMG 进程准备好后写入：

```text
control/ready.json
control/status.json
```

Thermocamera 在正式实验起点发布同机 monotonic 时间事件：

```cmd
eeg-emg-recording event --control-dir D:\thermal_session\sync\eeg_emg --name thermal_experiment_start --source thermocamera
```

实验结束时请求正常停止：

```cmd
eeg-emg-recording stop --control-dir D:\thermal_session\sync\eeg_emg --reason thermal_session_complete
```

结束后会生成 `control/complete.json`。执行对齐：

```cmd
eeg-emg-recording align --session D:\thermal_session\eeg_emg --thermal-session D:\thermal_session
```

同机控制事件可将事件的 monotonic 时间映射到 NI 样本索引。需要亚毫秒级物理同步时，应从 Thermocamera 控制硬件向 USB-6001 `PFI0` 或 `PFI1` 接入 TTL 启动触发，并在 `start_trigger` 中启用。USB-6001 支持 AI 数字启动触发，但不支持把该信号作为外部 sample clock。

也可占用一个可用 AI 通道并设置 `role: SYNC`，记录完整 TTL 波形和阈值边沿。
仅当该输入确实连接专用的 thermal-start 脉冲时，才启用
`sync.analog_start_match_enabled`，并在 `sync.analog_start_match_channel` 中填写对应
`role: SYNC` 的通道名称；对齐只会使用这个通道的边沿，不会混入其他 SYNC、激光或测试脉冲。
四个 DIFF 生物通道已占满时，应优先使用 PFI 启动触发，或增加独立采集硬件。

启用 PFI 启动触发后，记录器会先写 `ready.json` 并等待 TTL。等待期间仍可通过 GUI、`Ctrl+C`
或 control stop 正常取消，产生零样本但结构完整的 session，不需要强杀进程。

## 输出结构

```text
session_YYYYMMDD_HHMMSS_<mouse>_<id>/
  signals.h5
  metadata.json
  config_used.yaml
  block_index.csv
  events.csv
  signals_decimated.csv       # 仅 csv_enabled=true
  recording.log
  thermal_alignment.json      # 执行 align 后
  control/
    ready.json
    status.json
    complete.json
    events/
```

`signals.h5` 数据集为 `/signals/output_voltage_v`，形状是 `[sample, channel]`，
类型为 little-endian float32。通道顺序与 `metadata.json` 和
`config_used.yaml` 一致。NI 读取运行在独立采集线程，并配置约 60 秒主机缓冲；
HDF5 用 `committed_samples` 标记已提交边界，检查和导出会忽略异常中断留下的未提交尾部。
CSV 仅用于交换或快速检查，不建议代替 HDF5 作为主数据。

## 测试

```cmd
conda run --no-capture-output -n thermocamera_public python -m pytest -q
```

测试覆盖：

- 任意通道数、禁用通道和顺序保持
- USB-6001 DIFF 配对及 20 kS/s 聚合限制
- fake 数据的增量 HDF5 记录
- 外部 control event 与正常 stop
- 在线降采样 CSV 和离线 HDF5 导出
- control event 优先及 wall-clock fallback 对齐

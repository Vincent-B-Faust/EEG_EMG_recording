# EEG-EMG Recording

[GUI operation workflow / 界面操作说明](docs/UI_WORKFLOW.md) — setup locks and stop/finalization behavior / 设置锁定与停止收尾。

[中文](#中文说明) · [English](#english-guide)

<a id="中文说明"></a>

> 设备型号说明：需求中写的是 `NI USB-9001`，但未找到这个型号的 NI 官方资料；可核实的相近型号是 `NI USB-6001`，因此默认配置按 USB-6001 构建。采集代码本身会通过 NI-DAQmx 枚举实际设备和通道；正式接线前请核对采集卡铭牌，并以 `list_ni_devices.cmd` 的输出为准。

独立的 NI-DAQmx EEG/EMG 睡眠记录系统。它使用 WPI DAM50 的模拟输出，经 NI USB-6001 连续采集，支持自由配置通道、实时预览、增量 HDF5 保存、外部事件/停止控制，以及与 Thermocamera 实验 session 对齐。

## 快速开始

本项目默认优先复用 Thermocamera 的 conda 环境 `thermocamera_public`。该环境可以位于任意盘；若未找到，安装器会在本项目所在盘创建独立环境，不再假定 Conda 或环境位于 `C:`。

1. 双击 `install_environment.cmd`（旧名称 `install_shared_env.cmd` 仍可用），自动寻找或创建环境并安装 NI/test 依赖。
2. 连接 USB-6001，在 NI MAX 中确认设备可见。
3. 双击 `list_ni_devices.cmd`，确认输出中包含 USB-6001 和 `Dev*/ai0..ai7`。
4. 按真实接线修改 `configs/ni_usb6001_dam50.yaml`。
5. 双击 `start_gui.cmd`，在 GUI 中检查通道、逐通道放大器参数、采样率和预览后开始记录。

没有采集卡时，可双击 `run_fake_demo.cmd`。它会生成 20 秒、5 路合成 EEG/EMG/SYNC 数据并显示实时预览。也可指定时长，例如：

```cmd
run_fake_demo.cmd 60
```

## 环境发现与独立安装

`start_gui.cmd`、`run_fake_demo.cmd` 和 `list_ni_devices.cmd` 使用同一个 `environment_bootstrap.cmd`。启动器按以下顺序查找：

1. `environment.local.cmd` 或同名环境变量中指定的绝对路径；
2. 当前激活环境、`PATH`、Conda 注册环境列表及 `%USERPROFILE%\.conda\environments.txt`；
3. 所有已挂载盘符下常见的 Miniconda/Anaconda 安装位置；
4. 名为 `thermocamera_public` 的已注册环境；
5. 项目内 `.runtime\envs\eeg_emg_recording` 独立环境。

默认 `auto` 模式优先共享环境。共享环境不存在时，如果发现 Conda，就按绝对 prefix 创建独立 Conda 环境；只有普通 Python 3.10+ 时则创建 `venv`。首次启动若依赖缺失会自动执行项目安装。`.runtime` 和本机配置均已被 Git 忽略。

双击 `check_environment.cmd` 可查看发现的 Conda、最终环境、Python 路径及依赖状态；机器可读诊断为：

```cmd
environment_bootstrap.cmd doctor --json
```

需要覆盖默认行为时，将 `environment.local.cmd.example` 另存为 `environment.local.cmd` 并设置所需变量。路径都接受任意盘绝对路径：

```cmd
set "EEG_EMG_ENV_MODE=auto"
set "EEG_EMG_CONDA_EXE=<conda-installation>\Scripts\conda.exe"
set "EEG_EMG_ENV_PREFIX=<existing-environment>"
set "EEG_EMG_STANDALONE_PREFIX=<environment-parent>\eeg_emg_recording"
set "EEG_EMG_ENV_SEARCH_ROOTS=<first-root>;<second-root>"
```

`EEG_EMG_ENV_MODE` 可为：

- `auto`：优先共享环境，缺失或 Python 版本过旧时使用独立环境；
- `shared`：必须找到共享环境或 `EEG_EMG_ENV_PREFIX`，否则报错；
- `standalone`：始终使用项目独立环境。

若 Python 和 Conda 都没有加入 `PATH`，可额外设置 `EEG_EMG_BOOTSTRAP_PYTHON`。完整变量及注释见 `environment.local.cmd.example`。启动器不会递归扫描整块数据盘；非标准目录应使用显式路径或 `EEG_EMG_ENV_SEARCH_ROOTS`，避免启动时遍历大量实验数据。

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

- GUI 每个通道右侧的 `Amplifier...` 可录入型号、序列号、真实增益、耦合模式、
  高/低通、硬件滤波/陷波状态、该通道的 DAQ 量程覆盖、确认状态及备注。
- 这些项目只记录用户已经在硬件上完成的设置；程序不会控制或改变 DAM50 面板。
- 修改增益或硬件滤波参数会自动清除“已对照硬件确认”状态，需重新核对后勾选。
- HDF5 永远只保存 NI 在 DAM50 输出端测得的原始电压，数据集为
  `/signals/output_voltage_v`，单位 V。
- 预览选择 `input_uv` 时，显示的是明确标记为 derived 的输入端估算值：
  `derived_input_uv = output_voltage_v * 1e6 / amplifier_gain`。该值不会替换原始电压，
  也不会作为第二份信号数据写入 HDF5。
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
      amplifier_model: WPI DAM50
      amplifier_serial: DAM50-001
      amplifier_mode: AC
      hardware_filter_enabled: true
      hardware_highpass_hz: 0.1
      hardware_lowpass_hz: 1000
      hardware_notch_enabled: false
      hardware_notch_hz: null
      amplifier_settings_verified: true
      amplifier_notes: Checked against the front panel before recording
    - name: Mouse01_EMG
      mouse_id: Mouse01
      role: EMG
      physical_channel: ai1
      enabled: true
      amplifier_gain: 1000
      amplifier_model: WPI DAM50
      amplifier_serial: DAM50-002
      amplifier_mode: AC
      hardware_filter_enabled: true
      hardware_highpass_hz: 10
      hardware_lowpass_hz: 1000
      hardware_notch_enabled: false
      hardware_notch_hz: null
      amplifier_settings_verified: true
      amplifier_notes: Checked against the front panel before recording
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
| `amplifier_model` / `amplifier_serial` | 放大器型号和可追溯序列号/资产编号 |
| `amplifier_mode` | 放大器的耦合/模式，可为 `AC`、`DC`、`UNKNOWN` 或 `OTHER` |
| `hardware_filter_enabled` | 硬件滤波是否启用；不代表预览软件滤波 |
| `hardware_highpass_hz` / `hardware_lowpass_hz` | DAM50 面板滤波设置 |
| `hardware_notch_enabled` / `hardware_notch_hz` | 外置或其他型号放大器的硬件陷波状态；DAM50 未使用时保持 false/null |
| `amplifier_settings_verified` | 是否已由用户对照真实硬件确认 |
| `amplifier_notes` | 接线、旋钮位置、外置滤波器等自由备注 |
| 通道级 `terminal_config` / `input_min_v` / `input_max_v` | 可选的 NI 通道覆盖；留空继承 acquisition 全局设置 |
| `storage.csv_enabled` | 记录时同步输出降采样 CSV |
| `storage.csv_decimation` | CSV 每隔多少个原始样本写一行 |

在线 CSV 使用整段记录的全局样本索引执行 decimation，因此不会在采集块边界重置。

## 独立 CLI

推荐通过统一引导器运行 CLI，这样环境位于其他盘或使用独立环境时命令不变：

```cmd
environment_bootstrap.cmd run -- -m eeg_emg_recording list-devices
environment_bootstrap.cmd run -- -m eeg_emg_recording validate --config configs\ni_usb6001_dam50.yaml
environment_bootstrap.cmd run -- -m eeg_emg_recording record --config configs\ni_usb6001_dam50.yaml
environment_bootstrap.cmd run -- -m eeg_emg_recording inspect --session D:\path\to\session
environment_bootstrap.cmd run -- -m eeg_emg_recording export-csv --session D:\path\to\session --decimation 10
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
类型为 little-endian float32。它只包含 DAQ 原始输出端电压；数据集属性和
`/metadata/amplifiers` 保存单位、换算公式及逐通道人工录入的放大器参数，但不保存派生
输入端信号。相同参数也会写入 `metadata.json` 和完整展开的 `config_used.yaml`。
通道顺序在三者中一致。NI 读取运行在独立采集线程，并配置约 60 秒主机缓冲；
HDF5 用 `committed_samples` 标记已提交边界，检查和导出会忽略异常中断留下的未提交尾部。
CSV 仅用于交换或快速检查，不建议代替 HDF5 作为主数据。

## RSE 首次标定与可追溯诊断

`configs/ni_usb6001_dam50_rse.yaml` 用于当前两台 DAM50 的首次 RSE 标定：EEG BNC
中心接 AI0、EMG BNC 中心接 AI1，两条 BNC 屏蔽/返回线接 AI GND。它不是对所有实验都
最优的永久接法；只有完成 RSE 噪声基线后，才应在相同条件下与 DIFF 进行定量比较。

每次短时记录后运行：

```cmd
environment_bootstrap.cmd run -- -m eeg_emg_recording analyze --session D:\path\to\session --line-hz 50 --max-frequency-hz 100
```

默认创建新的时间戳 `analysis_*` 文件夹，保存 `signal_quality.json/.csv`、完整波形与
Welch PSD、首秒放大波形、幅度分布和跨通道相干性图。不会修改原始 HDF5。诊断阈值均可
配置：`--line-bandwidth-hz`、`--amplifier-output-limit-v`、
`--headroom-warning-fraction`、`--line-power-warning-fraction`。两路在 50/100 Hz 高相干通常提示共同电源、接地或动物参考
电极问题；DAM50 输出逼近约 ±8 V 时，应先排除放大器输入端饱和，不能用软件陷波掩盖后
继续正式实验。
使用 `--start-s` 和 `--end-s` 可将滤波器恢复段、稳定段和运动伪迹分别分析，原始数据仍保持不变。

## 测试

```cmd
environment_bootstrap.cmd run -- -m pytest -q
```

测试覆盖：

- 任意通道数、禁用通道和顺序保持
- USB-6001 DIFF 配对及 20 kS/s 聚合限制
- fake 数据的增量 HDF5 记录
- 外部 control event 与正常 stop
- 在线降采样 CSV 和离线 HDF5 导出
- control event 优先及 wall-clock fallback 对齐
- 任意路径共享环境发现、显式路径覆盖及项目独立环境回退
- 所有 Windows 入口统一使用环境引导器且不含固定盘符/用户名

---

<a id="english-guide"></a>

## English Guide

This repository is a standalone NI-DAQmx EEG/EMG recorder for WPI DAM50
amplifier outputs and NI USB-6001 acquisition. It supports configurable
channels, live preview, incremental HDF5 storage, external events and graceful
stop control, and timestamp alignment with Thermocamera sessions.

The original requirement named an `NI USB-9001`, for which no matching NI
documentation was found. The verified nearby device is USB-6001, so the
checked-in hardware defaults target USB-6001. Always confirm the physical
device label and use `list_ni_devices.cmd` before wiring or recording.

### Quick start

1. Run `install_environment.cmd` (`install_shared_env.cmd` remains an alias).
2. Connect the USB-6001 and verify it in NI MAX.
3. Run `list_ni_devices.cmd` and confirm the actual `Dev*/ai*` names.
4. Copy or edit `configs/ni_usb6001_dam50.yaml` to match the real wiring.
5. Run `start_gui.cmd`, review every channel and amplifier field, preview the
   signals, then start recording.

Without hardware, run `run_fake_demo.cmd 20` or replace `20` with another
duration. The demo records five synthetic EEG/EMG/SYNC channels and exercises
the same storage and preview path.

### Portable environment discovery

All Windows launchers use `environment_bootstrap.cmd`. It searches explicit
values in `environment.local.cmd`, active/PATH/registered Conda environments,
common Conda installations on mounted drives, `thermocamera_public`, then the
project-local `.runtime/envs/eeg_emg_recording` fallback. It never assumes a
specific drive letter or user directory.

```cmd
check_environment.cmd
environment_bootstrap.cmd doctor --json
```

Copy `environment.local.cmd.example` to the Git-ignored
`environment.local.cmd` when explicit control is needed. Set
`EEG_EMG_ENV_MODE` to `auto`, `shared`, or `standalone`; optional absolute
overrides include `EEG_EMG_CONDA_EXE`, `EEG_EMG_ENV_PREFIX`,
`EEG_EMG_STANDALONE_PREFIX`, `EEG_EMG_ENV_SEARCH_ROOTS`, and
`EEG_EMG_BOOTSTRAP_PYTHON`. Avoid broad data-drive searches; declare unusual
installation roots explicitly.

### Hardware and wiring constraints

```text
animal electrodes -> DAM50 differential input -> DAM50 BNC output
                  -> USB-6001 analog input -> computer
```

- One DAM50 has one BNC output and therefore supplies one independent channel.
- EEG plus EMG for one mouse requires at least two DAM50 amplifiers.
- USB-6001 differential mode exposes four logical channels: `ai0` pairs with
  AI4, `ai1` with AI5, `ai2` with AI6, and `ai3` with AI7.
- Total analog throughput is 20 kS/s, so
  `enabled channels * per-channel sample rate <= 20000`.
- Never connect animal electrodes directly to USB-6001. Follow the DAM50 and
  NI differential-wiring manuals, including any required bias path.

The software records settings that were physically made on each DAM50; it
does not control the amplifier. Enter the real gain, AC/DC mode, filters,
serial/asset ID, NI range, verification state, and notes. Any gain/filter
change clears the verified flag until the hardware is checked again.

`/signals/output_voltage_v` always stores the raw NI measurement in volts. The
optional preview value is derived only for display:

```text
derived_input_uv = output_voltage_v * 1e6 / amplifier_gain
```

Software notch/band-pass settings affect preview only. Before using animal
signals, test polarity and scale briefly with the DAM50 BAT/CAL 10 Hz signal.

### Channel configuration

Each enabled channel needs a unique name and physical channel. Common roles
are `EEG`, `EMG`, `EOG`, `ECG`, `SYNC`, and `AUX`. Important acquisition fields
include backend (`nidaqmx` or `fake`), device, rate, block duration, total
duration, terminal configuration, NI range, and per-channel amplifier
metadata. `storage.csv_enabled` creates an optional decimated exchange CSV;
HDF5 remains the authoritative data.

```yaml
acquisition:
  sample_rate_hz: 1000
  terminal_config: DIFF
  channels:
    - name: Mouse01_EEG
      mouse_id: Mouse01
      role: EEG
      physical_channel: ai0
      amplifier_gain: 1000
      amplifier_model: WPI DAM50
      amplifier_serial: DAM50-001
      amplifier_mode: AC
      hardware_highpass_hz: 0.1
      hardware_lowpass_hz: 1000
      amplifier_settings_verified: true
```

### CLI and preview

```cmd
environment_bootstrap.cmd run -- -m eeg_emg_recording list-devices
environment_bootstrap.cmd run -- -m eeg_emg_recording validate --config configs\ni_usb6001_dam50.yaml
environment_bootstrap.cmd run -- -m eeg_emg_recording record --config configs\ni_usb6001_dam50.yaml
environment_bootstrap.cmd run -- -m eeg_emg_recording inspect --session D:\path\to\session
environment_bootstrap.cmd run -- -m eeg_emg_recording export-csv --session D:\path\to\session --decimation 10
environment_bootstrap.cmd run -- -m eeg_emg_recording analyze --session D:\path\to\session --line-hz 50
```

`record` accepts safe overrides for duration, output/session directory,
preview, fake mode, control directory, and parent session. A requested session
directory must be empty. Press `Q`/`Esc`, use `Ctrl+C`, or send a control stop
for graceful completion; the recorder flushes HDF5 and writes metadata and
`complete.json`.

### Traceable RSE bring-up and diagnostics

`configs/ni_usb6001_dam50_rse.yaml` is the initial two-DAM50 RSE baseline:
EEG BNC center to AI0, EMG BNC center to AI1, and both BNC returns/shields to
AI GND. It is a bring-up configuration, not a claim that RSE is always optimal.
Compare it with DIFF only after a reproducible RSE baseline exists.

The `analyze` command creates a new timestamped `analysis_*` directory without
altering raw HDF5. It writes JSON/CSV metrics plus full waveforms, Welch PSD,
a first-second zoom, amplitude distributions, and cross-channel coherence.
Line frequency, analysis bandwidth, nominal DAM50 output limit, and headroom
warning fraction are CLI parameters. Strong shared 50/100 Hz coherence points
to a common power/ground/reference path. If a DAM50 output approaches its
approximately +/-8 V swing, resolve input-stage saturation before considering
software notch filtering or a production recording.
Use `--start-s` and `--end-s` to analyze recovery, stable, and artifact windows
separately without altering the source recording.

### Synchronization and Multi Recording System

Use the sibling `Multi_recording_system` as the preferred single supervisor.
It starts EEG/EMG first, waits for `ready.json`, starts thermal recording, then
stores one cross-system manifest. Manual integration uses the same control
protocol: `record --until-stop`, `event` for thermal start/end, `stop`, then
`align`.

Same-computer monotonic events support ordinary alignment. For validated
sub-millisecond synchronization, wire a TTL start pulse to USB-6001 PFI0/PFI1
or to a dedicated analog `SYNC` channel. Enable analog start matching only
when that exact channel carries the dedicated thermal-start pulse. When all
four differential analog channels are occupied, prefer PFI or additional
hardware.

### Session contents and recovery

Each session contains `signals.h5`, `metadata.json`, expanded
`config_used.yaml`, block/event tables, logs, optional decimated CSV,
alignment, and control state. HDF5 data are little-endian float32 with shape
`[sample, channel]`; `committed_samples` marks the valid boundary after an
interruption. Inspection/export ignores an uncommitted tail. Preserve partial
sessions for diagnosis rather than deleting them.

Run the full regression suite with:

```cmd
environment_bootstrap.cmd run -- -m pytest -q
```


### Live preview: fixed Y range and AC/DC

Above the live plot, select AC or DC, optionally enable **Fixed Y** and enter
**Min** / **Max**, then click **Apply**. These controls remain available during
recording. Limits use the selected Preview units (input uV or output V), apply
to all channels, and clip traces outside the display range. Disable Fixed Y
and Apply to return to automatic scaling. Save config while idle to persist.

AC removes the current preview window mean and uses the existing display-only
notch/bandpass filters when enabled. DC bypasses those filters and preserves
the measured offset. This is a software display choice; it does not change
physical amplifier coupling or stored raw DAQ voltages.

YAML preview options: `coupling: AC` (or `DC`) and `y_range: [-500, 500]`
(`null` for automatic scaling). Existing configs default to AC and auto range.

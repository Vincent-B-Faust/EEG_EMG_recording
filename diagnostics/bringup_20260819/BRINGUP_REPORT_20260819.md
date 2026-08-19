# USB-6001 + DAM50 bring-up / 首次联调报告

## 中文

- 配置：两台 WPI DAM50，AC、A-B、增益 1000；EEG 0.1--100 Hz，EMG 10--100 Hz；
  USB-6001 RSE，EEG AI0、EMG AI1、两路 BNC 返回接 AI GND；每路 1000 Hz。
- NI-DAQmx 26.5 自检通过，设备为 `Dev1 / USB-6001`。
- 普通权限下创建 AI 任务阻塞；管理员权限下 10 秒真实记录完整完成，共 10,000
  sample/channel。LED/AO 始终未启用。
- EEG 输出范围 -8.174--8.780 V，81.13% 样本超过 DAM50 名义 ±8 V 摆幅，不能作为有效
  EEG；EMG 输出 -2.453--1.704 V。
- 50 Hz 频带占 0.5--100 Hz 功率：EEG 14.68%，EMG 25.93%。两路 50 Hz 相干性
  0.99618，100 Hz 相干性 0.999984，提示共同电源/接地/动物参考路径干扰。
- 判定：DAQ 未达到 ±10 V 削顶线，但 EEG 已在 DAM50 输出级饱和。正式记录前必须先做
  输入短接噪声测试并检查 preparation ground/reference；不能依靠软件 notch 掩盖。

成功原始 session：`session_20260819_175754_Mouse01_f8615768/`

最终诊断：`session_20260819_175754_Mouse01_f8615768/analysis_20260819_1802_rse_v3/`

## English

- Configuration: two WPI DAM50 units, AC A-B, gain 1000; EEG 0.1--100 Hz,
  EMG 10--100 Hz; USB-6001 RSE on AI0/AI1 with both BNC returns at AI GND;
  1000 Hz/channel.
- NI-DAQmx 26.5 self-test passed on `Dev1 / USB-6001`.
- AI task creation blocked without elevation. The elevated 10-second hardware
  run completed with 10,000 samples/channel. LED/AO remained disabled.
- EEG was -8.174 to 8.780 V and spent 81.13% beyond the nominal DAM50 +/-8 V
  swing; it is not a usable EEG trace. EMG was -2.453 to 1.704 V.
- The 50 Hz band represented 14.68% (EEG) and 25.93% (EMG) of 0.5--100 Hz
  power. Cross-channel coherence was 0.99618 at 50 Hz and 0.999984 at 100 Hz,
  indicating a shared power, grounding, or animal-reference interference path.
- Conclusion: the NI +/-10 V range did not clip, but EEG saturated at the
  amplifier output. Perform an input-short noise test and correct the
  preparation ground/reference before any production recording; software
  notch filtering is not an acceptable substitute.

## 接入动物 preparation GND 后 / After connecting animal preparation ground

- 第二轮 20 秒记录：`session_20260819_181323_Mouse01_52de90cc/`。
- EEG 不再饱和。稳定的 2--15 秒窗口为 -373.7--205.0 µV、峰峰值 578.7 µV、
  去均值 RMS 102.6 µV，50 Hz 功率占比 5.11%。
- 16--19 秒存在一次约 2.12 mV 的慢伪迹，需继续观察电极/线缆运动，不与稳定窗混合解释。
- EMG 幅度降至 -52.7--184.4 µV，但 50 Hz 占 0.5--100 Hz 功率 99.97%，且与 EEG
  在 50 Hz 的相干性为 0.9993；EMG 仍是电源干扰，不能用于正式记录。
- 下一隔离步骤：仅将 EMG DAM50 的 INPUT SELECT 切至 GND，EEG 保持 A-B，记录 10 秒。
  若 50 Hz 消失则定位到 EMG 电极/接触/输入线；若保留则定位到 EMG DAM50、输出 BNC
  或 DAQ 连接链路。

- The preparation ground eliminated EEG amplifier saturation. In the stable
  2--15 s window EEG was -373.7 to 205.0 uV, 578.7 uV peak-to-peak, 102.6 uV
  demeaned RMS, with 5.11% line-band power.
- A slow approximately 2.12 mV artifact occurred in the 16--19 s window and
  should be treated separately as a movement/contact artifact.
- EMG fell to -52.7 to 184.4 uV, but 50 Hz still accounted for 99.97% of its
  0.5--100 Hz power. It is not yet usable.
- Next isolate the EMG chain by switching only the EMG DAM50 INPUT SELECT to
  GND for a 10-second run while EEG remains A-B.

## 第三轮重新接线 / Third rewiring run

- 10 秒记录：`session_20260819_182841_Mouse01_2ebb5e68/analysis_rewired_v1/`。
- EEG 未饱和，范围 -422.6--260.5 µV，但 50 Hz 已占 0.5--100 Hz 功率 53.34%。
- EMG 范围 -51.4--176.7 µV，50 Hz 占比仍为 99.94%。
- 两路 50 Hz 相干性 0.99990，说明重新接线后共同干扰仍存在，且比上一轮 EEG 稳定窗更强。
- 本轮无法单独判定 EMG 电极端或输出端，因为采集元数据不能获知人工 INPUT SELECT 档位；
  必须确认 EMG 本轮究竟是 `GND` 还是 `A-B`。

- Ten-second run: `session_20260819_182841_Mouse01_2ebb5e68/analysis_rewired_v1/`.
- EEG remained within range (-422.6 to 260.5 uV), but 50 Hz accounted for
  53.34% of 0.5--100 Hz power. EMG remained 99.94% line-frequency power.
- Coherence at 50 Hz was 0.99990. The manually selected EMG input mode must be
  confirmed before deciding whether the interference is upstream or downstream
  of the EMG DAM50 input selector.

## 第四轮复测与实时分型质量回放 / Fourth retest and realtime-staging QC replay

- 10 秒真实记录：`session_20260819_190645_Mouse01_e7e270ad/`；采集完整，
  10,000 samples/channel，LED/AO 始终关闭。
- EEG 未削顶，范围 -472.9--309.4 µV、峰峰值 782.3 µV、去均值 RMS
  149.1 µV，但 50 Hz 占 0.5--100 Hz 功率 70.94%。
- EMG 未削顶，范围 -100.4--273.3 µV、峰峰值 373.8 µV、RMS
  118.1 µV，50 Hz 占比 99.95%。两路 50 Hz 相干性为 0.999946。
- 使用实时单 EEG 模型的两个完整 4 秒 epoch 回放时，49--51 Hz / 1--100 Hz
  功率比分别为 63.47% 和 71.56%，均超过预设 20% 门限。
- 两个 epoch 的流形坐标仍保存并以灰色 `QCRejected` 点显示，但 HMM 前向状态没有推进、
  后验没有发布、光输出保持关闭。结果位于
  `session_20260819_190645_Mouse01_e7e270ad/analysis_realtime_qc_v1/`。
- 集成硬件启动测试随后被 NI-DAQmx `-50103 resource reserved` 拒绝。只读进程检查显示
  `EEG-EMG Sleep Recording` GUI（PID 15300）仍在运行并占用 USB-6001；未自动终止该程序。

- The completed 10-second retest contained no DAQ clipping, but 50 Hz power
  remained 70.94% in EEG and 99.95% in EMG; 50 Hz coherence was 0.999946.
- The two complete 4-second model epochs had line-power fractions of 63.47%
  and 71.56%. Both exceeded the predeclared 20% gate.
- Their manifold coordinates were archived as gray `QCRejected` points for
  diagnosis. The HMM was not advanced, no posterior was emitted, and optical
  output remained off. See `analysis_realtime_qc_v1/` under the session.
- A subsequent integrated hardware start was blocked by NI-DAQmx error
  `-50103` because the existing `EEG-EMG Sleep Recording` GUI (PID 15300) was
  still holding the USB-6001. That user-facing process was not terminated.

## 最终集成冒烟测试 / Final integrated smoke test

- 旧 GUI 释放设备后，`Sleep_Closeloop` 于 19:38 完成 12 秒真实集成记录：
  `Sleep_Closeloop/runs/realtime_unsupervised_mouse01/session_20260819_193804_c7da8a1a/`。
- 三个 4 秒 epoch 的 49--51 Hz / 1--100 Hz 占比分别为 27.06%、25.56%、
  19.79%。前两个被质量门控拒绝，第三个仅略低于 20% 门限并作为第 1/900 个
  baseline 窗口保存。
- 三个 epoch 的流形坐标、质量状态与冻结 HMM 矩阵均成功实时发布并落盘；因一小时
  baseline 未完成，HMM 后验与睡眠状态按设计不启动。LED/激光全程禁用、0% 功率。
- 跨设备精选归档位于
  `Sleep_Closeloop/docs/hardware_smoke_20260819/`。当前信号仍处于门限边缘，
  不应开始正式一小时 baseline；应先让连续窗口稳定且显著低于 20%。

- After the old GUI released the device, the final 12-second integrated run
  completed successfully. Its three line-power fractions were 27.06%, 25.56%,
  and 19.79%. Two epochs were rejected and one contributed the first of 900
  required baseline windows.
- Manifold coordinates, quality state, and the frozen HMM matrix were published
  and archived. The HMM correctly remained inactive because the one-hour
  baseline was incomplete. Optical output stayed disabled at 0% power.

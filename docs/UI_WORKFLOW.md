# Recording GUI workflow / 记录界面操作说明

Updated 2026-09-06. Open `start_gui.cmd` only after safely closing any older
recording instance. No default wiring/amplifier configuration was changed.

## 中文

先打开配置，核对 backend、设备、采样率、通道、终端模式及放大器参数，再开始记录。
启动后配置保存/切换、通道编辑和设置区锁定；本轮预览参数也固定，不会显示一个实际上
未生效的新 notch 或增益。波形和停止按钮保留可用。

点击停止后，等待当前采集块结束及文件收尾；状态不会被迟到的“已启动”消息覆盖。
只有后台线程真正退出后才恢复设置和开始按钮。结束前不要启动新的设备控制窗口。
新记录会清除上一轮待显示的预览队列，避免旧数据混入新会话的显示缓冲。

打开损坏配置失败时，继续保留原配置及其路径。运行中保存操作被拒绝，不会让实际
采集配置与界面内容不一致。显示设置锁不等于硬件质量达标；仍需检查削顶、工频等。

## English

Open and review configuration before Start recording. Acquisition, channels,
amplifier and preview settings are fixed for that run. Save/Open and setup
controls are locked while preview and Stop remain available. Stop waits for
block completion/finalization; a delayed startup message cannot replace the
stopping status. Unlock occurs after the worker exits. Old queued preview
samples are discarded at the next start. A failed configuration load leaves
the previous path and configuration intact.

This lock is local to this GUI, not a global NI reservation manager. Do not run
another controller against the same hardware. Existing user configs, raw-data
recording, timing and safety checks are preserved.

无新数据时不重绘/重复滤波同一预览缓冲；调整窗口大小仍会重绘。历史预览使用产生该
记录时的显示配置，不受之后保存的新参数影响。

Preview redraw/filtering occurs only on new data or resize, with run-frozen
display settings rather than subsequently saved parameters.

Validation: 42 tests passed, including hidden Tk and mocked lifecycle tests;
hardware enumeration was disabled in GUI tests. No real acquisition was started.

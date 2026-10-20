# 复杂矢量预览性能验收

日期：2026-10-20。基于含其他任务未提交修改的工作区；本次修改 `vector_preview.py`、`image_preview.py`，新增离线回归与基准脚本。保留其他任务修改、FS 原件、导出成果和发布包，未提交或打包。

## 真实样本测量

使用已有本地车型 SVG，视口 1000×650，依次测量适应比例的 1、1.25、1.5、2、4、8、32、1、1.25、1.5、2 倍。下表连续帧中位数排除首次渲染，计时包括矢量光栅化与像素转换。基线为修改前保存的 `vector_preview.py`，不是官方软件。

| 样本 | path 数 | 旧连续帧中位 | 新连续帧中位 | 新初始化 | 新首帧 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 3261588.fs.svg | 4,162 | 114.830 ms | 4.584 ms | 382.775 ms | 125.158 ms |
| 7260798.fs.svg | 51,794 | 989.369 ms | 98.287 ms | 2,629.232 ms | 226.457 ms |

实际映射的 Tk 窗口完成各 30 次缩放；交互期间先变换缓存帧，停顿后生成精确帧。两份样本均完成全部操作并显示最终精确帧，没有 Tk 回调异常。10 ms 心跳的交互中位间隔分别为 11.503 / 11.334 ms，最大间隔为 35.739 / 34.902 ms。51k 图首次精确呈现阶段的最大心跳间隔为 82.119 ms。心跳不等于显示器 FPS，也不包含初始化 `VectorImage` 阶段；初始化由现有主界面加载线程执行。

可复查的脱敏结果见 [benchmarks.json](benchmarks.json)。完整 SVG、旧基线模块、REA 原始证据及测试日志留在被忽略的 `.local/tasks/20261020_vector_performance/`；已有 SVG 输入保持原位。

复现命令：

```powershell
python scripts/benchmark_vector_preview.py .local/tasks/20261019_missing_windows/3261588.fs.svg --baseline .local/tasks/20261020_vector_performance/baseline_vector_preview.py --output .local/tasks/20261020_vector_performance/benchmark_3261588.json
python scripts/benchmark_vector_preview.py .local/tasks/20261019_missing_windows/7260798.fs.svg --baseline .local/tasks/20261020_vector_performance/baseline_vector_preview.py --output .local/tasks/20261020_vector_performance/benchmark_7260798.json
```

## 回归覆盖与边界

新增回归覆盖：缩放不重解析、viewBox 非零原点、嵌套变换、渐变、源绘制顺序、复合路径填充与零宽细线、闭合后继续绘制、未知 SVG 回退；可见窗口慢绘制时 Tk 回调继续、只保留最新缩放请求、选择框不被覆盖、清空或销毁时拒绝晚帧并结束轮询。

最终执行 `python -X utf8 -m unittest discover -s . -p "test_*.py" -v`：**173 项通过，30.704 秒**。最终日志没有 `Variable.__del__`、Tk 回调错误或 Tcl 崩溃。差异空白检查通过。未调用真实 AI 收费接口。

完整回归曾暴露测试生命周期问题：旧 Tk 测试只销毁窗口，仍由测试实例持有窗口、Variable 与应用引用；这些循环可能在下一个测试的后台线程被垃圾回收，引发 `Variable.__del__` 跨线程错误甚至 `Tcl_AsyncDelete`。新增 `tests/tk_lifecycle.py`，让使用 Tk 的测试在原有清理后等待自身线程退出，释放公开 fixture 引用，并在主线程回收循环。测试保留原业务断言，不全局禁用垃圾回收或忽略异常。

未知 SVG 语法仍使用兼容导入器，不能保证同样提速。首次编译和原生复杂路径绘制仍有成本；本次没有官方动态 FPS 对照，不能声称所有车型已与官方完全一致。官方静态证据、推断边界及实现说明见 [性能调查](../../docs/vector-preview-performance.md)。

清理仅删除本次 4 个诊断脚本/PNG，共 3,255 字节；保留基准、旧基线、证据与测试日志。新增文件分别用于长期性能说明、静态反编译依据、可重复基准和离线回归。

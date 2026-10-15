# 独立 FS 几何调查

全部脚本只读原始 FS，不依赖 Flexi 进程、旧工具实现或附件 SVG。

## 已验证

- 13 份 FS 的 OLE Preview 全部解码；256 像素宽、8bit RGB 索引图。
- 13 份对象流全部读取，覆盖未压缩 32bit TLV、分块 zlib 32bit TLV、分块 zlib 64bit TLV。
- 样本直接还原 4541 条原始曲线：566 APath、3975 AEditPath，4541 个对象 ID 无重复。
- 附件三条路径通过遍历独立解码曲线寻找最佳匹配，对应原生对象 233/235/237；数值数 22/22/126，最大坐标误差均 0.0，M/L/C/Z 命令序列相同。附件仅用于独立验收脚本。

## 文件

- `inspect_fs.py`：容器目录、对象解压、Preview 解码。
- `fs_geometry.py`：生产集成入口 `extract_geometry(source)`。
- `validate_geometry.py`：13 文件几何调查及附件独立验收。
- `reference_acceptance.json`：三条目标逐数值匹配结果。
- `geometry_corpus_report.json`：13 文件曲线数量和警告。
- `probe_paths.py`：早期 APath 几何调查程序。

## 格式证据

TLV 对象先存版本字节，再存若干 `field:u8,length:u32/u64,payload`；length 包含字段头。
独立对象 wrapper 含对象 ID:u32、ClassTable 索引:u32、payload 长度:u32/u64、payload。

APath field1 存展开顶点数和段数（各 u32），field2 存 `x:f64,y:f64,kind:u32`；kind2 是直线，kind4 后再存两对控制点坐标，最后16字节存终点。所有成功解析记录均校验展开顶点数和数据长度。

AEditPath field3 是混合结构：基类的若干 TLV 之后是节点数:u32，再是固定49字节节点 `flag:u8,x,y,inDx,inDy,outDx,outDy:f64`。控制柄是相对节点位移；下一节点 flag bit0 决定三次曲线，即使控制柄为零也保留 C 命令。顶层空 field1 对应闭合路径标记。

## 仍未解决

对象发现是经过长度/数值验证的扫描，尚未解析完整父子树。`tree_path` 为 null，`source_order_index` 只表示文件顺序。`own_matrix` 是自身 SVG 六元矩阵 `(a,b,c,d,e,f)`，不得当作最终父子累计变换。颜色、隐藏、裁剪、布尔组合等由属性调查单独处理。

`bbox` 是局部控制点包围盒，不是精确贝塞尔极值。APath 闭合标志还未解释，因此不会擅自追加 Z。尚不支持矩形/椭圆/文本/位图作为几何类型；不能将成功解析曲线数量当作文档全对象覆盖率。

扫描标记被拒数量包括数值数据中的同字节组合，不等于真实失败对象数；自动批量业务层应显示上述语义限制并要求复核。

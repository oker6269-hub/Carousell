# Carousell SKU 素材采集工具

批量采集 Carousell（香港站）卖家主页的全部 SKU 素材：图片 + 表格数据。支持断点续跑、增量采集、类目跳过、多链接批量。

## 原理

**单一客户端**：枚举和详情都由同一个浏览器发出。`cf_clearance` 与签发它的客户端的
TLS/UA 指纹绑定，导出给 curl 复用必然被判无效 —— 所以不做跨客户端搬运。

```
① 浏览器会话（全程只开一次，结束时关闭）
   Chrome / CloakBrowser 定制内核（复用持久 profile，CF 通常 5-20s 通过）
   ├─ 打开卖家页 /u/{user}/（或 /p/ 商品 → 自动解析卖家 → 转主页）
   ├─ 循环点击「浏览更多」展开全部商品（每次 +20，直到耗尽）
   ├─ 页面内并发 fetch 详情页（同源请求，cookie/UA/TLS 天然一致）
   └─ 从内联状态 JSON 的 listingsMap 抽字段（标题/描述/价格/类目/图片/卖家）
② 图片下载（media.karousell.com 无 CF）
   curl_cffi 直连，线程池并发
③ 落盘
   ├─ {root}/{SKU}/SKU_01.jpg …
   ├─ {root}/素材保存表格.xlsx（模板 12 列，追加去重）
   └─ state.json 断点 / filters.json 类目过滤
```

- 浏览器既做枚举也做取数，全程一个身份，**不存在第二个客户端**
- 只有图片走 curl（CDN 无 CF 防护）
- 默认**直连免代理**；代理可选（GUI/CLI 配置）
- 反爬应对：详情按批并发 + 批次间隔、失败重试（指数退避）、profile 损坏自动隔离重建
- 规模参考（实测）：详情抓取约 4 条/秒；瓶颈是图片（每 SKU 4 并发）。
  3500 个商品、每 SKU 6 张图，整体约 2-3 小时；调大 `--workers` 可线性缩短下图时间

## 使用（GUI）

双击 `CarousellCollector.exe`（打包版）或 `python run.py`：

1. **选择根路径** —— 素材输出目录
2. **粘贴卖家链接**（每行一条，如 `https://www.carousell.com.hk/u/kekeepiera63711/`）
3. **类目过滤管理**（可选）—— 添加要跳过的分类（如「其他」），可逐条开关，持久化在 `filters.json`
4. **开始采集** —— 日志面板实时显示进度；首次运行会弹 Chrome 窗口过验证（约 15-20 秒，自动完成）

## 使用（CLI）

```bash
CarousellCollector.exe --root D:\materials --urls https://www.carousell.com.hk/u/xxx/ [--workers 4] [--detail-concurrency 4] [--max-items 5000] [--proxy socks5://127.0.0.1:1080]
# 定时自动更新（增量拉新品）
CarousellCollector.exe --root D:\materials --urls URL --watch 60          # 每 60 分钟
CarousellCollector.exe --root D:\materials --urls URL --watch-daily 09:00  # 每天 09:00
```

## 链接形态

| 输入 | 行为 |
|---|---|
| `https://www.carousell.com.hk/u/{卖家}/` | 枚举该卖家**全部**在售商品并采集 |
| `https://www.carousell.com.hk/p/{slug}-{id}/` | 自动解析该商品卖家 → 转入卖家主页**全量**枚举采集 |
| 混合多行 | 逐条处理，同一卖家自动去重 |

## 输出结构

```
{root}/
├── 素材保存表格.xlsx     # 12 列：SKU/BrowserID/ProductNameCn/ProductNameEn/
│                        #   GenderEn/HKPrice/SGPrice/MYPrice/Brand/Folder/ShopID/Desc
│                        #   版式对齐「最干净的上品记录/001/测试.xlsx」：
│                        #   等线 12、表头行高 22、A1:L1 自动筛选、不冻结窗格；
│                        #   SKU 与价格写数值；旧版式文件首次打开会自动迁移
├── {listing_id}/        # 每 SKU 一个目录（如 1470110967/）
│   ├── {sku}_01.jpg     # 图片按序号命名
│   └── …
├── state.json           # 断点状态（done/skipped/failed）——删除对应条目可强制重采
├── filters.json         # 类目跳过规则（GUI 可管理）
└── logs/                # 采集日志
```

### Folder 列 = 图片目录（绝对路径，跨平台）

| `--path-style` | Windows 输出 | macOS 输出 |
|---|---|---|
| `native`（默认） | `D:\materials\1470110967` | `/Users/me/materials/1470110967` |
| `posix` | `D:/materials/1470110967` | `/Users/me/materials/1470110967` |
| `relative` | `1470110967` | `1470110967` |

- `native` 用系统原生分隔符，直接复制到资源管理器 / Finder 可用
- `posix` 统一正斜杠，便于把表格导入其他系统（Windows 上 mac 路径风格混用时的推荐）
- Excel 文件被占用（正在用 Excel 打开）时，会自动重试 3 次，仍失败则另存为 `素材保存表格_时间戳.xlsx`

## 标题清洗（正则）

标题在写入表格前会按一组**可配置的正则规则**清洗，例如：

| 原始标题 | 落表标题 |
|---|---|
| `原裝有盒Zippo煤油火機 富貴唐草 純銅盔甲機` | `Zippo煤油火機 富貴唐草 純銅盔甲機` |
| `全新有盒MLB Chunky Liner 厚底防滑耐磨 休閒.` | `MLB Chunky Liner 厚底防滑耐磨 休閒` |

- 配置位置：`~/.carousell-collector/title_rules.json`（**全局**，所有 root 共用）
- GUI 里在「采集」页右下角的 **规则 → 标题清洗** 里增删/开关，带「恢复默认」
- 规则按顺序逐条 `re.sub`，最后合并多余空格、去掉首尾空白
- 正则写错不会中断采集：该条跳过并写日志；「添加」时会先校验并提示
- 内置默认两条：去掉「原裝有盒 / 全新有盒 / 原裝 / 全新 / 二手 / 有盒…」前缀、去掉结尾标点

```json
{
  "enabled": true,
  "rules": [
    {"name": "去掉成色/包裝前綴",
     "pattern": "^\\s*(?:原裝有盒|全新有盒|原裝|全新|二手|近全新|有盒|無盒|冇盒)\\s*",
     "replace": "", "enabled": true},
    {"name": "去掉結尾標點", "pattern": "[\\s.。,，、·;；:：!！?？]+$",
     "replace": "", "enabled": true}
  ]
}
```

清洗只影响 `ProductNameCn`（以及 `state.json` 里的 title），`Desc`、价格、图片目录不变；
品牌/性别推断用的也是清洗后的标题。

## 断点 / 增量 / 定时更新

### 三轮去重（互不依赖，任一满足即跳过）

| 层级 | 依据 | 行为 |
|---|---|---|
| ① SKU 层 | `state.json` 中的 `listing_id` 状态 | `done` 跳过；`failed` 自动重试；`skipped` 保持跳过 |
| ② 文件层 | 目标图片已存在且大小 > 0 | 直接复用，不重复下载（换网络/中断后重跑极快） |
| ③ 表格层 | Excel 中已有该 SKU 行 | 不重复追加，避免重复行 |

### 定时自动更新（只拉新品）

```bash
# 每 60 分钟跑一轮；每轮只采集「枚举到但 state 里没有」的新 SKU
CarousellCollector.exe --root D:\materials --urls URL --watch 60
# 每天 09:00 跑一轮
CarousellCollector.exe --root D:\materials --urls URL --watch-daily 09:00
```

GUI 里勾选「自动更新」+ 选择 `interval`/`daily` 同样生效；「立即更新一次」可手动触发。

**增量语义**：卖家新增商品 → 下一轮枚举会看到新 ID → 只下载这些新 ID 的详情与图片 → 追加到 Excel 末尾。已采过的商品不会被重新请求（除非状态是 `failed`）。

### 强制重采某个 SKU

删 `state.json` 里对应条目，并删除该 SKU 目录（或删目录内图片）即可。

## 依赖要求

- **Google Chrome 或 Microsoft Edge**（CF 突破用，自动检测）
- Windows 10+ / macOS 12+

## 从源码运行

```bash
pip install -r requirements.txt
python run.py            # GUI
python -m app.main --root ... --urls ...   # CLI
```

## 打包

```bash
pyinstaller build.spec --noconfirm
# Windows: dist/CarousellCollector/CarousellCollector.exe
# macOS:   dist/CarousellCollector.app（双击）+ dist/CarousellCollector/（CLI）
# macOS Intel 交叉构建: CC_TARGET_ARCH=x86_64 pyinstaller build.spec --noconfirm
```

GitHub Actions（`.github/workflows/build.yml`）：

| 产物 | 运行器 | 说明 |
|---|---|---|
| `CarousellCollector-windows-x64.zip` | windows-latest | Windows x64 |
| `CarousellCollector-macos-arm64.zip` | macos-14 (arm64) | Apple Silicon，含 `.app` |
| `CarousellCollector-macos-x64.zip` | macos-14 + `target_arch=x86_64` | Intel Mac |
| `*-cli.tar.gz` | macos | 命令行目录版 |

打 tag 即构建并发布 Release：

```bash
git tag v1.0.2 && git push origin v1.0.2
```

### macOS 首次打开说明

构建产物未做代码签名，首次打开会提示「无法验证开发者」：

- **方式一**：右键点 `CarousellCollector.app` → 打开 → 再点「打开」
- **方式二**：终端执行 `xattr -dr com.apple.quarantine CarousellCollector.app`
- 需要系统已安装 **Google Chrome**（用于突破 CF 拿商品清单）

## 注意

- 首次运行 Chrome 窗口会自动通过 CF 验证；若弹人机验证，手动点击一次即可（60 秒宽限）
- 详情默认 4 条一批并发（`--detail-concurrency` 可调）、批间隔 0.6s（`--workers` 控图片并发）；
  大量 SKU 时请勿盲目调高，先小批量观察是否触发风控
- cf_clearance 与客户端指纹绑定：换网络环境后首跑需重新过验证（自动）
- profile 若损坏（内核启动即崩），程序会自动改名隔离并用全新 profile 重建，日志里会写明路径

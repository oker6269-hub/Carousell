# Carousell SKU 素材采集工具

批量采集 Carousell（香港站）卖家主页的全部 SKU 素材：图片 + 表格数据。支持断点续跑、增量采集、类目跳过、多链接批量。

## 原理

```
① 浏览器会话（一次，用完即关）
   Chrome（复用持久 profile，CF 通常 5-20s 通过）
   ├─ 打开卖家页 /u/{user}/（或 /p/ 商品 → 自动解析卖家 → 转主页）
   ├─ 循环点击「浏览更多」展开全部商品（每次 +20，直到耗尽）
   ├─ 提取全部商品 ID + 链接（实测单卖家可达 3500+）
   └─ 导出 cf_clearance 等 cookie
② 纯协议采集（curl_cffi chrome150，直连，无需代理）
   ├─ 详情页 /p/{slug}/   ld+json → 描述/价格/成色/图片数组/分类
   └─ 图片   media.karousell.com  直连下载（无 CF，线程池并发）
③ 落盘
   ├─ {root}/{SKU}/SKU_01.jpg …
   ├─ {root}/素材保存表格.xlsx（模板 12 列，追加去重）
   └─ state.json 断点 / filters.json 类目过滤
```

- **浏览器只负责枚举商品清单**（这是唯一必须 JS 的环节），其余全部纯协议
- 默认**直连免代理**；代理可选（GUI/CLI 配置）
- 反爬应对：随机请求间隔、失败重试（指数退避）、CF 失效自动重过、failed 复跑自动重试
- 规模参考：3500 个商品的卖家，枚举约 15 分钟，之后逐条采集详情与图片（每条约 2-5s）

## 使用（GUI）

双击 `CarousellCollector.exe`（打包版）或 `python run.py`：

1. **选择根路径** —— 素材输出目录
2. **粘贴卖家链接**（每行一条，如 `https://www.carousell.com.hk/u/kekeepiera63711/`）
3. **类目过滤管理**（可选）—— 添加要跳过的分类（如「其他」），可逐条开关，持久化在 `filters.json`
4. **开始采集** —— 日志面板实时显示进度；首次运行会弹 Chrome 窗口过验证（约 15-20 秒，自动完成）

## 使用（CLI）

```bash
CarousellCollector.exe --root D:\materials --urls https://www.carousell.com.hk/u/xxx/ [--workers 4] [--max-items 5000] [--proxy socks5://127.0.0.1:1080]
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
├── 素材保存表格.xlsx     # 12 列模板：SKU/BrowserID/ProductNameCn/ProductNameEn/
│                        #   GenderEn/HKPrice/SGPrice/MYPrice/Brand/Folder/ShopID/Desc
├── {listing_id}/        # 每 SKU 一个目录（如 1470110967/）
│   ├── {sku}_01.jpg     # 图片按序号命名
│   └── …
├── state.json           # 断点状态（done/skipped/failed）——删除对应条目可强制重采
├── filters.json         # 类目跳过规则（GUI 可管理）
└── logs/                # 采集日志
```

## 断点/增量

- 复跑自动跳过 `done` 的 SKU（列表页对比 state.json）
- `failed` 的 SKU 自动重试
- 想强制重采某 SKU：删 `state.json` 里对应条目 + 对应目录

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
# 产物: dist/CarousellCollector/
```

GitHub Actions：打 tag `v*` 自动构建 Windows x64 + macOS arm64 双平台产物并发布 Release（见 `.github/workflows/build.yml`）。

## 注意

- 首次运行 Chrome 窗口会自动通过 CF 验证；若弹人机验证，手动点击一次即可（60 秒宽限）
- 采集节奏默认保守（0.6-1.6s 随机间隔），大量 SKU 时请勿调高并发以免触发风控
- cf_clearance 与 IP/UA 绑定：换网络环境后首跑需重新过验证（自动）

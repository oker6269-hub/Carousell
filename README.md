# Carousell SKU 素材采集工具

批量采集 Carousell（香港站）卖家主页的全部 SKU 素材：图片 + 表格数据。支持断点续跑、增量采集、类目跳过、多链接批量。

## 原理

```
Chrome（真实浏览器，直连）一次性突破 Cloudflare 挑战 (~15-20s，首次)
   ↓ 提取 cf_clearance cookie（缓存复用，失效自动重过）
curl_cffi（chrome150 TLS 指纹）纯协议直连高速采集
   ├─ 列表页 /u/{user}/   SSR HTML → 商品卡（listing ID/标题/价格）
   ├─ 详情页 /p/{slug}/   ld+json → 描述/价格/成色/图片数组/分类
   └─ 图片   media.karousell.com  直连下载（无 CF，线程池并发）
```

- 全程**默认直连，无需代理**；代理可选（GUI/CLI 配置）
- 反爬应对：随机请求间隔、失败重试（指数退避）、CF 失效自动重过、failed 复跑自动重试

## 使用（GUI）

双击 `CarousellCollector.exe`（打包版）或 `python run.py`：

1. **选择根路径** —— 素材输出目录
2. **粘贴卖家链接**（每行一条，如 `https://www.carousell.com.hk/u/kekeepiera63711/`）
3. **类目过滤管理**（可选）—— 添加要跳过的分类（如「其他」），可逐条开关，持久化在 `filters.json`
4. **开始采集** —— 日志面板实时显示进度；首次运行会弹 Chrome 窗口过验证（约 15-20 秒，自动完成）

## 使用（CLI）

```bash
CarousellCollector.exe --root D:\materials --urls https://www.carousell.com.hk/u/xxx/ https://www.carousell.com.hk/u/yyy/ [--workers 4] [--proxy socks5://127.0.0.1:1080]
```

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

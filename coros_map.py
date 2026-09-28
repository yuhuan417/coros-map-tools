#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
coros-map-tools —— 高驰（COROS）手表离线地图下载 / 子区域提取工具

手表离线地图的两种分发方式：
  1. 打包 ZIP：{host}/map/region/v{3,4,5}/map_<Region>_<ALL|OSM>_v{n}.zip
     对应「电脑下载 + USB 拷贝」流程。
  2. 瓦片包：App 里「复制下载链接」得到的是清单 JSON
     {host}/mapid/<map_id>.json#mapv5，里面逐条列出瓦片文件名/大小/quadkey，
     真正的地图数据是每个瓦片一个 PMTiles 文件（.t），手表通过 WiFi 逐个下载。

本工具处理第 1 种（整包下载 / 校验）与第 2 种（按行政边界提取子区域，
把 1.5 GB 的华北包裁成 20 MB 的北京市包）。

依赖：Python 3.8+ 标准库；按区域裁切需要官方 go-pmtiles：
    go install github.com/protomaps/go-pmtiles@latest

地图数据版权归 COROS 及 OpenStreetMap 贡献者所有，本工具仅供个人为自己
的手表管理离线地图，请勿二次分发地图数据。
"""

import argparse
import gzip
import json
import math
import os
import random
import shutil
import struct
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile

__version__ = "1.0.0"

CN_HOST = "http://map-oss-cn.coros.com"
US_HOST = "https://map-oss-us.coros.com"
S3_HOST = "https://osm-map.s3.us-west-1.amazonaws.com"

# 中国区域包（v5，2025-11-26 快照，取自官方下载页 JS 里的 china_region_v5.json）
# size = 解压后各瓦片文件大小之和，count = 瓦片文件数；map_id 用于拼接 App 下载链接
CHINA_V5 = {
    "China": {
        "provinces": [],
        "ALL": {"map_id": "RJNXm8gP", "size": 6391890589, "count": 621, "filename": "map_China_ALL_v5.zip"},
        "OSM": {"map_id": "TFx1K4BV", "size": 1409644777, "count": 316, "filename": "map_China_OSM_v5.zip"},
    },
    "Northeast-China": {
        "provinces": ["辽宁", "吉林", "黑龙江"],
        "ALL": {"map_id": "kf1CVCRX", "size": 729010634, "count": 100, "filename": "map_Northeast-China_ALL_v5.zip"},
        "OSM": {"map_id": "huMlnFcQ", "size": 156987505, "count": 50, "filename": "map_Northeast-China_OSM_v5.zip"},
    },
    "North-China": {
        "provinces": ["北京", "天津", "河北", "山西", "内蒙古"],
        "ALL": {"map_id": "WVUZfiZ9", "size": 1628415078, "count": 206, "filename": "map_North-China_ALL_v5.zip"},
        "OSM": {"map_id": "MIkbtLgS", "size": 276155296, "count": 103, "filename": "map_North-China_OSM_v5.zip"},
    },
    "Central-China": {
        "provinces": ["河南", "湖北", "湖南"],
        "ALL": {"map_id": "kv9sMrlQ", "size": 604748494, "count": 36, "filename": "map_Central-China_ALL_v5.zip"},
        "OSM": {"map_id": "DOtQ2lJr", "size": 166931480, "count": 18, "filename": "map_Central-China_OSM_v5.zip"},
    },
    "East-China": {
        "provinces": ["上海", "江苏", "浙江", "安徽", "福建", "江西", "台湾", "山东"],
        "ALL": {"map_id": "wErXPkna", "size": 659855098, "count": 65, "filename": "map_East-China_ALL_v5.zip"},
        "OSM": {"map_id": "VrqFiI1w", "size": 314026693, "count": 33, "filename": "map_East-China_OSM_v5.zip"},
    },
    "South-China": {
        "provinces": ["广东", "广西", "海南", "香港", "澳门"],
        "ALL": {"map_id": "gJMYJetu", "size": 629924734, "count": 68, "filename": "map_South-China_ALL_v5.zip"},
        "OSM": {"map_id": "yTLekmmm", "size": 204637359, "count": 39, "filename": "map_South-China_OSM_v5.zip"},
    },
    "Southwest-China": {
        "provinces": ["四川", "云南", "贵州", "重庆", "西藏"],
        "ALL": {"map_id": "ZVAjRQWj", "size": 2529041845, "count": 142, "filename": "map_Southwest-China_ALL_v5.zip"},
        "OSM": {"map_id": "TGv8H4A5", "size": 600651428, "count": 71, "filename": "map_Southwest-China_OSM_v5.zip"},
    },
    "Northwest-China": {
        "provinces": ["新疆", "宁夏", "青海", "陕西", "甘肃"],
        "ALL": {"map_id": "4tVNdOTt", "size": 2304691062, "count": 218, "filename": "map_Northwest-China_ALL_v5.zip"},
        "OSM": {"map_id": "yO3pqh4G", "size": 289055623, "count": 109, "filename": "map_Northwest-China_OSM_v5.zip"},
    },
}

# 图层：VCM = 等高线图（contour），VSM = 地貌图（landscape）
LAYERS = {"VCM": "C", "VSM": "S"}
# 全球 v3 清单（v3/v4 机型用，按 zip 分发）
GLOBAL_V3_INDEX = US_HOST + "/regionMap/v3/regions_v3.json"
# 全球（中国之外）v5 区域清单与整包：/regionMap/v5/<区域>_<landscape|topo>_<版本>.zip
# 注意路径是 regionMap 而不是中国区的 map/region，两套并存
WORLD_V5_PATH = "/regionMap/v5"
WORLD_V5_INDEX = "/regionMap/v5/regions_v5.json"
# 行政边界来源（阿里 DataV.GeoAtlas）
DATAV_URL = "https://geo.datav.aliyun.com/areas_v3/bound/{adcode}{suffix}.json"


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024.0


def eprint(*a):
    print(*a, file=sys.stderr)


# ---------------------------------------------------------------- 几何 / 瓦片

def lonlat_to_tile(lon, lat, z):
    """经纬度 → XYZ 瓦片坐标（Web Mercator，与 quadkey 同一套编号）"""
    n = 2 ** z
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.log(math.tan(math.radians(lat)) + 1.0 / math.cos(math.radians(lat))) / math.pi) / 2.0 * n)
    return x, y


def lonlat_to_quadkey(lon, lat, z=7):
    x, y = lonlat_to_tile(lon, lat, z)
    qk = []
    for i in range(z, 0, -1):
        d = 0
        mask = 1 << (i - 1)
        if x & mask:
            d += 1
        if y & mask:
            d += 2
        qk.append(str(d))
    return "".join(qk)


def quadkey_bounds(qk):
    """quadkey → (min_lon, min_lat, max_lon, max_lat)（理论边界）"""
    z = len(qk)
    x = y = 0
    for i, ch in enumerate(qk):
        mask = 1 << (z - i - 1)
        d = int(ch)
        if d & 1:
            x |= mask
        if d & 2:
            y |= mask
    n = 2 ** z
    lon = lambda px: px / n * 360.0 - 180.0
    lat = lambda py: math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * py / n))))
    return (lon(x), lat(y + 1), lon(x + 1), lat(y))


def bbox_intersects(a, b):
    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])


def feature_polygons(feat):
    """GeoJSON Feature → Polygon 列表（每个 Polygon 是环的列表）"""
    g = feat.get("geometry") or {}
    if g.get("type") == "Polygon":
        return [g["coordinates"]]
    if g.get("type") == "MultiPolygon":
        return list(g["coordinates"])
    return []


def features_bbox(feats):
    lons, lats = [], []
    for f in feats:
        for poly in feature_polygons(f):
            for ring in poly:
                for p in ring:
                    lons.append(p[0])
                    lats.append(p[1])
    if not lons:
        raise ValueError("GeoJSON 里没有可用多边形")
    return (min(lons), min(lats), max(lons), max(lats))


def point_in_polygons(polys, lon, lat):
    for poly in polys:
        ring = poly[0]
        inside = False
        for i in range(len(ring) - 1):
            x1, y1 = ring[i][0], ring[i][1]
            x2, y2 = ring[i + 1][0], ring[i + 1][1]
            if (y1 > lat) != (y2 > lat) and lon < (x2 - x1) * (lat - y1) / (y2 - y1) + x1:
                inside = not inside
        if inside:
            return True
    return False


# ------------------------------------------------------------------- PMTiles

PMTILES_HEADER_LEN = 127


def parse_pmtiles_header(buf):
    """解析 PMTiles v3 头部（127 字节）"""
    if len(buf) < PMTILES_HEADER_LEN or buf[:7] != b"PMTiles":
        raise ValueError("不是 PMTiles 文件")
    if buf[7] != 3:
        raise ValueError(f"只支持 PMTiles v3，当前版本 {buf[7]}")
    (root_off, root_len, meta_off, meta_len, leaf_off, leaf_len,
     data_off, data_len) = struct.unpack_from("<8Q", buf, 8)
    clustered, icomp, tcomp, ttype, minz, maxz = struct.unpack_from("<6B", buf, 96)
    minlon, minlat, maxlon, maxlat = struct.unpack_from("<4i", buf, 102)
    return {
        "root_off": root_off, "root_len": root_len,
        "meta_off": meta_off, "meta_len": meta_len,
        "leaf_off": leaf_off, "leaf_len": leaf_len,
        "data_off": data_off, "data_len": data_len,
        "clustered": bool(clustered), "internal_compression": icomp,
        "tile_compression": tcomp, "tile_type": ttype,
        "min_zoom": minz, "max_zoom": maxz,
        "bbox": (minlon / 1e7, minlat / 1e7, maxlon / 1e7, maxlat / 1e7),
    }


def read_pmtiles_header(path_or_obj):
    if hasattr(path_or_obj, "read"):
        return parse_pmtiles_header(path_or_obj.read(PMTILES_HEADER_LEN))
    with open(path_or_obj, "rb") as f:
        return parse_pmtiles_header(f.read(PMTILES_HEADER_LEN))


_zip_cache = {}          # zip 路径 → ZipFile，避免惰性 opener 用到已关闭的归档


def output_tile_path(out_dir, style, qk, name):
    """输出包里的瓦片路径（中国区顶层是 Map，中国之外是 map）"""
    for root in ("Map", "map"):
        p = os.path.join(out_dir, root, style, qk[:3], name)
        if os.path.exists(p):
            return p
    return os.path.join(out_dir, "Map", style, qk[:3], name)


def package_root_name(src):
    """包内顶层目录名：中国区是 Map，中国之外是 map（手表按官方包的原样识别）"""
    if zipfile.is_zipfile(src):
        zf = _zip_cache.setdefault(os.path.abspath(src), zipfile.ZipFile(src))
        for n in zf.namelist():
            parts = n.split("/")
            if parts[-1].endswith(".t") and len(parts) >= 3:
                return parts[0]
        return "Map"
    for style in ("VCM", "VSM"):
        for cand in (os.path.join(src, style), os.path.join(src, "Map", style), os.path.join(src, "map", style)):
            if os.path.isdir(cand):
                return os.path.basename(os.path.dirname(cand)) or "Map"
    return "Map"


def iter_package_tiles(src, styles=("VCM", "VSM")):
    """遍历一个地图包，产出 (style, 文件名, quadkey, 大小, 头部信息, 打开函数)

    src 可以是解压后的目录，也可以是 zip 文件。目录可以是包根、Map/ 或 Map/VCM 等。
    """
    def emit(style, name, size, opener):
        qk = name[1:8]
        try:
            info = read_pmtiles_header(opener())
        except Exception as e:                                  # noqa: BLE001
            eprint(f"跳过 {name}: {e}")
            return None
        return (style, name, qk, size, info, opener)

    if zipfile.is_zipfile(src):
        # zip 句柄要活到 opener 被调用时（调用可能发生在遍历结束之后），所以缓存住不关闭；
        # 进程退出时由 GC 收尾
        zf = _zip_cache.setdefault(os.path.abspath(src), zipfile.ZipFile(src))
        for zi in zf.infolist():
            parts = zi.filename.split("/")
            if len(parts) < 2 or not zi.filename.endswith(".t"):
                continue
            style = parts[-3] if len(parts) >= 3 else parts[0]
            name = parts[-1]
            if style not in styles:
                continue
            item = emit(style, name, zi.file_size, lambda zi=zi, zf=zf: zf.open(zi))
            if item:
                yield item
        return

    for style in styles:
        # <STYLE> 目录可能在 src 下、src/Map 下，或 src 本身就是它
        roots = [c for c in (os.path.join(src, style), os.path.join(src, "Map", style),
                             src if os.path.basename(os.path.normpath(src)) == style else None)
                 if c and os.path.isdir(c)]
        for r in dict.fromkeys(roots):                      # 去重且保序
            for root, _dirs, files in os.walk(r):
                for name in sorted(files):
                    if not name.endswith(".t"):
                        continue
                    p = os.path.join(root, name)
                    item = emit(style, name, os.path.getsize(p), lambda p=p: open(p, "rb"))
                    if item:
                        yield item


# ------------------------------------------------------------------ 网络工具

def http_open(url, headers=None, timeout=30):
    req = urllib.request.Request(url, headers=headers or {})
    return urllib.request.urlopen(req, timeout=timeout)


def download(url, dest, resume=True, quiet=False):
    """断点续传下载，返回最终字节数"""
    pos = os.path.getsize(dest) if (resume and os.path.exists(dest)) else 0
    headers = {"Range": f"bytes={pos}-"} if pos else {}
    try:
        resp = http_open(url, headers, timeout=60)
    except urllib.error.HTTPError as e:
        if e.code == 416 and pos:                    # 已经下完
            return pos
        if e.code in (403, 404):
            raise SystemExit(f"HTTP {e.code}：{url}\n（该路径可能不存在——注意各节点的 v3/v4/v5 分布不同，见 README）")
        raise
    if pos and getattr(resp, "status", 200) != 206:
        # 服务器忽略了 Range 返回整个文件：必须从头写，否则会把全文追加到半截文件后面
        resp.close()
        pos = 0
        resp = http_open(url, {}, timeout=60)
    total = int(resp.headers.get("Content-Length") or 0) + pos
    mode = "ab" if pos else "wb"
    got = pos
    tty = sys.stderr.isatty()
    mark = -1
    with open(dest, mode) as f:
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
            if quiet:
                continue
            if tty:                     # 终端里原地刷新一行
                pct = f"{got * 100 // total}%" if total else "?"
                sys.stderr.write(f"\r  下载中 {human(got)} / {human(total)} ({pct})   ")
                sys.stderr.flush()
            elif total:
                # 重定向到文件时不要每 1 MB 刷一行，只在每 10% 打一次
                m = got * 10 // total
                if m != mark:
                    mark = m
                    eprint(f"  下载中 {m * 10}%（{human(got)} / {human(total)}）")
    if not quiet and tty:
        sys.stderr.write("\r" + " " * 60 + "\r")
    return got


def find_pmtiles_bin(explicit=None):
    if explicit:
        return explicit
    env = os.environ.get("COROS_PMTILES")
    if env:
        return env
    for name in ("go-pmtiles", "pmtiles"):
        p = shutil.which(name)
        if p:
            return p
    raise SystemExit(
        "找不到 go-pmtiles（按区域裁切需要它）：\n"
        "    go install github.com/protomaps/go-pmtiles@latest\n"
        "然后用 --pmtiles-bin 指定，或把 $GOPATH/bin 加进 PATH，或设置 COROS_PMTILES。"
    )


# --------------------------------------------------------------------- 子命令

def cmd_list(args):
    if args.world:
        regions = fetch_world_regions(CN_HOST if args.host == "cn" else US_HOST)
        names = [k for k in regions if not k.startswith("_")]
        if args.region:
            names = [n for n in names if args.region.lower() in n.lower()]
        if not names:
            raise SystemExit("没有匹配的区域")
        print(f"全球区域包（v5，bundle {regions['_bundleVersion']}，合计 {human(regions['_totalSize'])}）")
        print(f"{'区域':<22}{'地貌 landscape':>18}{'等高线 topo':>18}")
        for n in sorted(names):
            cells = []
            for t in ("landscape", "topo"):
                d = regions[n].get(t)
                cells.append(human(d["size"]) if d else "—")
            print(f"{n:<22}{cells[0]:>18}{cells[1]:>18}")
        print(f"\n下载：python3 {os.path.basename(sys.argv[0])} download <区域> --world --layer landscape|topo")
        print("注意：中国之外的区域包 landscape 与 topo 是分开的，没有合并包")
        return
    rows = []
    for name, info in CHINA_V5.items():
        if args.region and args.region.lower() not in name.lower():
            continue
        if args.province and not any(args.province in p for p in info["provinces"]):
            continue
        rows.append((name, info))
    if not rows:
        raise SystemExit("没有匹配的区域")
    print(f"{'区域':<18}{'地貌+等高线 (ALL)':>26}{'仅地貌 (OSM)':>24}   省份")
    for name, info in rows:
        a, o = info["ALL"], info["OSM"]
        left = f"{human(a['size'])} / {a['count']} 文件"
        right = f"{human(o['size'])} / {o['count']} 文件"
        print(f"{name:<18}{left:>26}{right:>24}   {'、'.join(info['provinces']) or '全国'}")
    print(f"\n下载：python3 {os.path.basename(sys.argv[0])} download <区域> [--layer ALL|OSM]")
    print(f"App 链接（手表 WiFi 下载用）：{CN_HOST}/mapid/<map_id>.json#mapv5")
    if args.show_ids:
        print("\nmap_id：")
        for name, info in rows:
            print(f"  {name:<18}ALL={info['ALL']['map_id']:<12} OSM={info['OSM']['map_id']}")
    print(f"\n全球 v3 清单（老机型 zip 分发）：{GLOBAL_V3_INDEX}")


def fetch_world_regions(host=US_HOST):
    """取全球 v5 区域清单 → {区域: {landscape|topo: {size, link}}}，另附 _bundleVersion"""
    with http_open(host + WORLD_V5_INDEX, timeout=30) as r:
        d = json.load(r)
    out = {"_bundleVersion": d.get("bundleVersion", ""), "_totalSize": d.get("totalSize", 0)}
    for e in (d.get("mapData") or d.get("regions") or []):
        out.setdefault(e["region"], {})[e["type"]] = e["data"]
    return out


def download_world(args):
    """中国之外的区域包（landscape = 仅地貌，topo = 仅等高线，没有合并包）"""
    if args.layer == "OSM":
        args.layer = "landscape"
    if args.layer == "ALL":
        raise SystemExit("中国之外的区域包不提供「地貌+等高线」合并包，请分别指定 "
                         "--layer landscape 或 --layer topo")
    if args.layer not in ("landscape", "topo"):
        raise SystemExit("--layer 只能是 landscape 或 topo")
    host = CN_HOST if args.host == "cn" else US_HOST
    regions = fetch_world_regions(host)
    if args.region not in regions:
        raise SystemExit(f"未知区域 {args.region}，可用：{', '.join(k for k in regions if not k.startswith('_'))}")
    if args.layer not in regions[args.region]:
        raise SystemExit(f"{args.region} 没有 {args.layer}，可用：{', '.join(regions[args.region])}")
    spec = regions[args.region][args.layer]
    fn = spec["link"].rsplit("/", 1)[-1]
    url = host + spec["link"]
    dest = os.path.join(args.out, fn)
    os.makedirs(args.out, exist_ok=True)
    eprint(f"  {url}   (版本 {regions['_bundleVersion']})")
    n = download(url, dest, resume=not args.no_resume, quiet=args.quiet)
    if n < 1024:
        os.path.exists(dest) and os.remove(dest)
        raise SystemExit(f"下载结果只有 {n} 字节，几乎肯定是空占位文件")
    print(f"✔ {dest}  {human(n)}")
    if args.check:
        with zipfile.ZipFile(dest) as zf:
            members = [i for i in zf.infolist() if not i.is_dir()]
            total = sum(i.file_size for i in members)
            tops = sorted({i.filename.split("/")[0] for i in members})
            styles = sorted({i.filename.split("/")[1] for i in members if i.filename.count("/") >= 2})
            bad = zf.testzip()
        print(f"  条目 {len(members)} 个，解压后合计 {human(total)}，顶层 {tops}，图层 {styles}")
        print(f"  清单声明 {human(spec['size'])} → {'一致 ✅' if total == spec['size'] else '不一致 ⚠️'}")
        print(f"  CRC 校验：{'通过 ✅' if bad is None else f'失败 ❌ ({bad})'}")


def cmd_download(args):
    if args.world or args.region not in CHINA_V5:
        return download_world(args)
    spec = CHINA_V5[args.region][args.layer]
    version = args.version or 5
    if args.host == "s3":
        # us-west-1 的 S3 桶只放了 v3/v4 的整包（v5 走 CN 的 OSS）
        version = args.version or 4
        fn = spec["filename"].replace("_v5.zip", f"_v{version}.zip")
        url = f"{S3_HOST}/map/region/v{version}/{fn}"
    else:
        fn = spec["filename"]
        url = f"{CN_HOST}/map/region/v{version}/{fn}"
    dest = os.path.join(args.out, fn)
    os.makedirs(args.out, exist_ok=True)
    eprint(f"  {url}")
    n = download(url, dest, resume=not args.no_resume, quiet=args.quiet)
    if n < 1024:
        os.path.exists(dest) and os.remove(dest)
        raise SystemExit(f"下载结果只有 {n} 字节，几乎肯定是空占位文件（CN 节点的 /v4/ 目录就是这样）。\n"
                         f"v5 整包请用默认的 CN 节点；v3/v4 整包请加 --host s3。")
    print(f"✔ {dest}  {human(n)}")
    if args.check:
        with zipfile.ZipFile(dest) as zf:
            members = [i for i in zf.infolist() if not i.is_dir()]
            total = sum(i.file_size for i in members)
            bad = zf.testzip()
        print(f"  条目 {len(members)} 个，解压后合计 {human(total)}")
        print(f"  清单声明 {spec['count']} 个 / {human(spec['size'])} → "
              f"{'一致 ✅' if (len(members) == spec['count'] and total == spec['size']) else '不一致 ⚠️'}")
        print(f"  CRC 校验：{'通过 ✅' if bad is None else f'失败 ❌ ({bad})'}")


def cmd_boundary(args):
    feats = []
    for code in args.adcodes:
        last_err = None
        suffixes = ("",) if (args.no_full or args.url_template) else ("_full", "")
        for suffix in suffixes:
            if args.url_template:
                url = args.url_template.format(adcode=code)
            else:
                url = DATAV_URL.format(adcode=code, suffix=suffix)
            try:
                if "://" in url:                    # 网络源
                    with http_open(url, timeout=30) as r:
                        d = json.load(r)
                else:                               # 本地文件
                    with open(url, encoding="utf-8") as r:
                        d = json.load(r)
                if d.get("features"):
                    feats.extend(d["features"])
                    eprint(f"  {code}{suffix}: {len(d['features'])} 个多边形  ← {url}")
                    break
            except Exception as e:                              # noqa: BLE001
                last_err = e
        else:
            raise SystemExit(f"取不到 {code} 的边界：{last_err}")
    out = {"type": "FeatureCollection", "features": feats}
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    bb = features_bbox(feats)
    print(f"✔ {args.out}  {len(feats)} 个多边形，bbox {bb[0]:.4f},{bb[1]:.4f} → {bb[2]:.4f},{bb[3]:.4f}")


def _load_region(path, bbox=None):
    if bbox:
        w, s, e, n = [float(x) for x in bbox.split(",")]
        feat = {"type": "Feature", "properties": {},
                "geometry": {"type": "Polygon",
                             "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]}}
        return [feat]
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    feats = d["features"] if d.get("type") == "FeatureCollection" else [d]
    if not feats:
        raise SystemExit(f"{path} 里没有要素")
    return feats


def cmd_tiles(args):
    feats = _load_region(args.region, args.bbox)
    # 逐个多边形判断，而不是用整个区域的并集外接框——后者对「多个分散城市」会误命中大量瓦片
    fboxes = [(f, features_bbox([f])) for f in feats]
    rows = []
    seen = set()
    for src in args.src:
        for style, name, qk, size, info, _opener in iter_package_tiles(src, tuple(args.style)):
            if (style, qk) in seen:          # 同一个瓦片在多个区域包里重复存放，内容相同
                continue
            b = info["bbox"]
            if not any(bbox_intersects(fb, b) for _f, fb in fboxes):
                continue
            seen.add((style, qk))
            rows.append((qk, style, name, size, b, info, src))
    rows.sort()
    print(f"{'quadkey':<10}{'图层':<6}{'文件':<20}{'大小':>10}   瓦片覆盖范围 (lon/lat)")
    for qk, style, name, size, b, info, src in rows:
        print(f"{qk:<10}{style:<6}{name:<20}{human(size):>10}   {b[0]:.4f}~{b[2]:.4f}, {b[1]:.4f}~{b[3]:.4f}")
    qks = sorted({r[0] for r in rows})
    print(f"\n命中 {len(qks)} 个瓦片 / {len(rows)} 个文件：{', '.join(qks)}")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump([{"quadkey": r[0], "style": r[1], "file": r[2], "size": r[3],
                        "bbox": r[4], "package": r[6]} for r in rows], f, ensure_ascii=False, indent=2)
        print(f"已写出 {args.json}")


def cmd_info(args):
    for p in args.files:
        info = read_pmtiles_header(p)
        b = info["bbox"]
        print(f"{p}")
        type_name = {1: "mvt", 2: "png", 3: "jpeg", 4: "webp", 5: "avif"}.get(info["tile_type"], info["tile_type"])
        comp_name = {1: "none", 2: "gzip", 3: "brotli", 4: "zstd"}
        print(f"  大小 {human(os.path.getsize(p))}  zoom {info['min_zoom']}-{info['max_zoom']}  "
              f"瓦片 {type_name}({comp_name.get(info['tile_compression'], '?')})  "
              f"索引 {comp_name.get(info['internal_compression'], '?')}")
        print(f"  bbox {b[0]:.6f},{b[1]:.6f} → {b[2]:.6f},{b[3]:.6f}")


def _extract_one(pmt_bin, src_file, dst_file, region_file, quiet=True):
    cmd = [pmt_bin, "extract", f"--region={region_file}", "-q", src_file, dst_file]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"pmtiles extract 失败：{' '.join(cmd)}\n{r.stderr.strip()}")


def cmd_extract(args):
    pmt_bin = find_pmtiles_bin(args.pmtiles_bin)
    feats = _load_region(args.region, args.bbox)
    fboxes = [(f, features_bbox([f])) for f in feats]
    total_out = total_src = 0
    made = []
    roots = set()
    seen = set()
    for src in args.src:
        for style, name, qk, size, info, opener in iter_package_tiles(src, tuple(args.style)):
            if args.tile and qk not in args.tile:
                continue
            if (style, qk) in seen:          # 重复包里的同一瓦片，内容相同，跳过
                continue
            seen.add((style, qk))
            sel = [f for f, fb in fboxes if bbox_intersects(fb, info["bbox"])]
            if not sel:
                continue
            if not args.per_tile_region:
                sel = feats
            with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as tf:
                json.dump({"type": "FeatureCollection", "features": sel}, tf, ensure_ascii=False)
                region_file = tf.name
            # 源可能是 zip 里的成员，统一经 opener 落盘成临时文件
            tmp_src = tempfile.NamedTemporaryFile(suffix=".t", delete=False).name
            with open(tmp_src, "wb") as f, opener() as r:
                shutil.copyfileobj(r, f)
            src_path = tmp_src
            dst_file = os.path.join(args.out, package_root_name(src), style, qk[:3], name)
            os.makedirs(os.path.dirname(dst_file), exist_ok=True)
            if args.dry_run:
                print(f"  [dry-run] {name} ← region {len(sel)} 个多边形")
            else:
                _extract_one(pmt_bin, src_path, dst_file, region_file, quiet=args.quiet)
                out_size = os.path.getsize(dst_file)
                total_out += out_size
                total_src += size
                roots.add(package_root_name(src))
                made.append((name, style, out_size, size))
                print(f"  {name:<20}{style:<5}{human(size):>10} → {human(out_size):>10}  ({out_size * 100 // max(size, 1)}%)")
            os.unlink(region_file)
            if tmp_src:
                os.unlink(tmp_src)
    if made:
        print(f"\n合计 {len(made)} 个文件：{human(total_src)} → {human(total_out)}"
              f"（压到 {total_out * 100 // max(total_src, 1)}%）")
        top = "/".join(sorted(roots)) if roots else "Map"
        print(f"输出目录：{args.out}/{top}  把这里的顶层目录整个拷到手表根目录即可")


def cmd_verify(args):
    pmt_bin = find_pmtiles_bin(args.pmtiles_bin)
    feats = _load_region(args.region, args.bbox)
    groups = {}
    for f in feats:
        adcode = str(f.get("properties", {}).get("adcode", ""))
        groups.setdefault(adcode[:4] or "all", []).append(f)
    z = args.zoom
    total = lost = missing = ok = 0

    def sample(path, x, y):
        if not os.path.exists(path):
            return -1
        r = subprocess.run([pmt_bin, "tile", path, str(z), str(x), str(y)], capture_output=True)
        return len(r.stdout)

    src_index = {}
    for src in args.src:
        for style, name, qk, size, info, opener in iter_package_tiles(src, tuple(args.style)):
            src_index.setdefault((style, qk), opener)

    def fetch_src(style, qk, x, y):
        opener = src_index.get((style, qk))
        if not opener:
            return -1
        tmp = tempfile.NamedTemporaryFile(suffix=".t", delete=False).name
        try:
            with open(tmp, "wb") as f, opener() as r:
                shutil.copyfileobj(r, f)
            return sample(tmp, x, y)
        finally:
            os.path.exists(tmp) and os.unlink(tmp)

    print(f"{'分组':<8}{'抽样':>6}{'有数据':>8}{'输出缺失':>10}{'文件缺失':>10}")
    rnd = random.Random(args.seed)
    for gname, gfeats in sorted(groups.items()):
        polys = [p for f in gfeats for p in feature_polygons(f)]
        bb = features_bbox(gfeats)
        pts = []
        tries = 0
        while len(pts) < args.samples and tries < args.samples * 200:
            tries += 1
            lon = rnd.uniform(bb[0], bb[2])
            lat = rnd.uniform(bb[1], bb[3])
            if point_in_polygons(polys, lon, lat):
                pts.append((lon, lat))
        g_ok = g_lost = g_missing = 0
        for lon, lat in pts:
            qk = lonlat_to_quadkey(lon, lat, 7)
            x, y = lonlat_to_tile(lon, lat, z)
            for style in args.style:
                out_p = output_tile_path(args.out, style, qk, f"{LAYERS[style]}{qk}V00.t")
                n = sample(out_p, x, y)
                if n > 0:
                    g_ok += 1
                    continue
                if n == -1:
                    g_missing += 1
                if fetch_src(style, qk, x, y) > 0:
                    g_lost += 1
                    print(f"   ❌ {gname} ({lon:.4f},{lat:.4f}) {style} 源有数据、输出没有")
                else:
                    g_ok += 1
        print(f"{gname:<8}{len(pts):>6}{g_ok:>8}{g_lost:>10}{g_missing:>10}")
        ok += g_ok
        lost += g_lost
        missing += g_missing
        total += len(pts) * len(args.style)
    print(f"\n合计 {total} 项：正常 {ok}，裁剪丢失 {lost}，文件缺失 {missing}")
    if lost:
        raise SystemExit("存在数据丢失 ⚠️")


# ------------------------------------------------------------------------ CLI

def main():
    ap = argparse.ArgumentParser(
        prog="coros_map.py",
        description="高驰（COROS）手表离线地图下载 / 子区域提取工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""示例：
  %(prog)s list                                   # 列出中国区 v5 区域包
  %(prog)s download North-China -o ~/maps --check # 下载华北整包并校验
  %(prog)s boundary 110000 -o beijing.json        # 取北京市行政边界
  %(prog)s tiles ~/maps/Map --region beijing.json # 看北京落在哪些瓦片
  %(prog)s extract --src ~/maps/Map --region beijing.json --out bj
  %(prog)s verify --out bj --src ~/maps/Map --region beijing.json
""")
    ap.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list", help="列出区域包与 map_id")
    p.add_argument("--region", help="按区域名过滤")
    p.add_argument("--province", help="按省份过滤，如 安徽")
    p.add_argument("--show-ids", action="store_true", help="同时打印 map_id")
    p.add_argument("--world", action="store_true", help="列出中国之外的全球区域包（v5）")
    p.add_argument("--host", choices=("cn", "us"), default="us", help="取哪台节点的清单")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("download", help="下载区域整包 zip")
    p.add_argument("region", help="区域名，如 North-China")
    p.add_argument("-o", "--out", default=".", help="保存目录")
    p.add_argument("--layer", choices=("ALL", "OSM", "landscape", "topo"), default="ALL",
                   help="中国区：ALL=地貌+等高线 / OSM=仅地貌；全球区：landscape / topo")
    p.add_argument("--host", choices=("cn", "us", "s3"), default="cn",
                   help="cn=国内 OSS，us=全球节点，s3=us-west-1（只有中国区 v3/v4）")
    p.add_argument("--world", action="store_true", help="下载中国之外的区域包")
    p.add_argument("--version", type=int, choices=(3, 4, 5), help="包版本，默认 cn→5 / s3→4")
    p.add_argument("--no-resume", action="store_true", help="不续传，重新下载")
    p.add_argument("--check", action="store_true", help="校验 zip 条目数/总大小/CRC（1.6 GB 约 6 秒）")
    p.add_argument("-q", "--quiet", action="store_true")
    p.set_defaults(func=cmd_download)

    p = sub.add_parser("boundary", help="按 adcode 取行政边界并合并成 GeoJSON（默认源：阿里 DataV）")
    p.add_argument("adcodes", nargs="+", help="GB/T 2260 行政区划代码，如 110000 310000（北京、上海）")
    p.add_argument("-o", "--out", default="region.json")
    p.add_argument("--url-template", metavar="URL",
                   help="改用别的边界数据源，用 {adcode} 占位，例如 "
                        "'https://example.com/bound/{adcode}.json'；也可以传本地文件路径")
    p.add_argument("--no-full", action="store_true",
                   help="只用 {adcode}.json 单圈边界，不试带下级区县的 {adcode}_full.json")
    p.set_defaults(func=cmd_boundary)

    p = sub.add_parser("tiles", help="列出地图包里与边界相交的瓦片")
    p.add_argument("src", nargs="+", help="解压后的包目录或 zip")
    p.add_argument("--region", help="GeoJSON 边界文件")
    p.add_argument("--bbox", help="min_lon,min_lat,max_lon,max_lat")
    p.add_argument("--style", nargs="+", choices=tuple(LAYERS), default=list(LAYERS))
    p.add_argument("--json", help="把结果写成 JSON")
    p.set_defaults(func=cmd_tiles)

    p = sub.add_parser("info", help="查看 PMTiles 头部信息")
    p.add_argument("files", nargs="+")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("extract", help="按边界裁切出子区域（需要 go-pmtiles）")
    p.add_argument("--src", nargs="+", required=True, help="源包目录或 zip（可给多个，如华北+华东+华南）")
    p.add_argument("--region", required=False, help="GeoJSON 边界文件")
    p.add_argument("--bbox", help="min_lon,min_lat,max_lon,max_lat（与 --region 二选一）")
    p.add_argument("--out", required=True, help="输出目录（生成 Map/<图层>/<前缀>/<文件>）")
    p.add_argument("--style", nargs="+", choices=tuple(LAYERS), default=list(LAYERS))
    p.add_argument("--tile", nargs="+", help="只处理指定 quadkey")
    p.add_argument("--no-per-tile-region", dest="per_tile_region", action="store_false",
                   help="不按瓦片拆分 region（默认拆，输出文件的 bbox 更准确）")
    p.add_argument("--pmtiles-bin", help="go-pmtiles 可执行文件路径")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("-q", "--quiet", action="store_true")
    p.set_defaults(func=cmd_extract, per_tile_region=True)

    p = sub.add_parser("verify", help="抽样校验输出包覆盖完整、无丢失")
    p.add_argument("--out", required=True, help="待校验的输出目录")
    p.add_argument("--src", nargs="+", required=True, help="原始包，用于比对是否丢数据")
    p.add_argument("--region", help="GeoJSON 边界文件")
    p.add_argument("--bbox")
    p.add_argument("--style", nargs="+", choices=tuple(LAYERS), default=list(LAYERS))
    p.add_argument("-n", "--samples", type=int, default=12, help="每组抽样点数")
    p.add_argument("--zoom", type=int, default=13)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--pmtiles-bin")
    p.set_defaults(func=cmd_verify)

    args = ap.parse_args()
    if getattr(args, "func", None) in (cmd_tiles, cmd_extract, cmd_verify) and not (
            getattr(args, "region", None) or getattr(args, "bbox", None)):
        ap.error("需要 --region 或 --bbox")
    args.func(args)


if __name__ == "__main__":
    main()

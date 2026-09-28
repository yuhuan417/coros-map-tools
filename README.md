# coros-map-tools

高驰（COROS）手表离线地图的**下载**与**子区域提取**工具。

官方只提供「整包下载」——华北一整包 1.5 GB、全国 6 GB，而手表存储往往只有几个 GB。
本工具可以把地图按**任意行政边界**裁剪：例如整个华北包 → 只要北京市，1.5 GB 变 19 MB。

```
$ python3 coros_map.py list                       # 有哪些区域包
$ python3 coros_map.py download North-China -o ~/maps --check
$ python3 coros_map.py boundary 110000 -o beijing.json      # 北京市边界
$ python3 coros_map.py extract --src ~/maps/Map --region beijing.json --out bj
   C1321001V00.t  VCM  14.0 MB → 6.6 MB  (47%)
   S1321001V00.t  VSM  21.1 MB → 12.4 MB (58%)
   ...
   合计 4 个文件：55.0 MB → 19.2 MB（压到 34%）
$ python3 coros_map.py verify --out bj --src ~/maps/Map --region beijing.json
   合计 168 项：正常 168，裁剪丢失 0，文件缺失 0
```

单个城市是最简单的情况。**多个城市会跨区域包**（北京在华北、上海在华东、深圳在华南……），
完整流程和几个只有多城市才会踩到的坑见 [多个城市](#多个城市会跨区域包)。

> English: a CLI to download COROS watch offline maps and clip them to an arbitrary
> GeoJSON boundary. The map packages are plain zips of [PMTiles](https://protomaps.com/)
> archives, so subsetting is done with the official `go-pmtiles` tool. See
> [技术细节](#技术细节) for the URL layout this relies on.

## 背景：高驰的离线地图是怎么分发的

手表地图有**两种分发方式**，很多人会把它们搞混：

**1. 整包 ZIP（电脑下载 + USB 拷贝）**

```
http://map-oss-cn.coros.com/map/region/v5/map_<区域>_<ALL|OSM>_v5.zip
```

- `ALL` = 地貌 + 等高线，`OSM` = 只有地貌（体积约为 ALL 的 1/5）
- 中国分 8 个区域包（华北 / 华东 / 华南 / 华中 / 东北 / 西南 / 西北 / 全国），见 `list` 子命令
- 解压后是 `Map/VCM/`（等高线）与 `Map/VSM/`（地貌）两个目录，整个 `Map` 拷到手表根目录

**2. 瓦片包（App 里「复制下载链接」+ 手表 WiFi 下载）**

App 里复制的链接长这样：

```
https://map-oss-us.coros.com/mapid/WVUZfiZ9.json#mapv5
```

**这不是地图数据，只是清单**：里面逐条列出瓦片文件名、大小、quadkey。真正的地图数据是
每个瓦片一个 `.t` 文件（PMTiles 格式），由手表通过 WiFi 从 CDN 逐个拉取。
清单里的 `files_size` / `count` 与上面 ZIP 包里解压后的大小、文件数**完全对应**。

## 安装

只需要 Python 3.8+（标准库，无第三方依赖）。按边界裁剪时需要官方 pmtiles 工具：

```bash
go install github.com/protomaps/go-pmtiles@latest
# 若不在 PATH 里：export COROS_PMTILES=$(go env GOPATH)/bin/go-pmtiles
```

## 外部依赖与数据来源

工具本身只用 Python 标准库，但**三样东西来自外部**，用之前先知道自己在依赖谁：

| 依赖 | 用在哪 | 说明 / 风险 |
|---|---|---|
| **COROS 的 CDN**<br>`map-oss-cn.coros.com`<br>`osm-map.s3.us-west-1.amazonaws.com` | `download`、App 链接 | 地图数据本体。无鉴权、无文档、无 SLA，URL 布局是抓官方页面 JS 逆出来的，**随时可能变**；变了你就会看到 404/403（`download` 会直接报错退出） |
| **阿里 DataV.GeoAtlas**<br>`geo.datav.aliyun.com/areas_v3/bound/` | `boundary` | 行政区划**边界**（不是地图数据）。第三方服务，同样无 SLA。取不到时换源即可，见下 |
| **go-pmtiles** | `extract` | 官方 PMTiles 工具，按边界做实际的裁剪计算。默认在 PATH 里找，可用 `COROS_PMTILES` 或 `--pmtiles-bin` 指定 |

关键区别：**地图数据只来自 COROS，行政边界只来自 DataV，两者互不绑定。**
`extract` 接受任意 GeoJSON，所以哪怕 DataV 关站了，你也可以自己找边界数据继续用：

```bash
# 换数据源：模板里的 {adcode} 会被替换，支持 http(s) 与本地路径
python3 coros_map.py boundary 110000 -o bj.json \
    --url-template "https://你的镜像/bound/{adcode}.json"

# 只用单圈边界，不要带下级区县的 _full 版本（体积小、够用）
python3 coros_map.py boundary 110000 -o bj.json --no-full

# 直接用自己准备的 GeoJSON（任何 FeatureCollection 都行），完全绕开 DataV
python3 coros_map.py extract --src 华北/Map --region 我的边界.json --out bj
```

想要权威边界/代码，可以换用民政部「全国行政区划信息查询平台」（`xzqh.mca.gov.cn`）、
国家统计局的年度区划代码，或全国地理信息资源目录服务系统的 1:100 万基础地理数据；
DataV 只是工程上最省事的一个（代码和边界一体、按层级可取）。

**关于 adcode**：就是 **GB/T 2260 行政区划代码**，六位 = 省级(2) + 地级(2) + 县级(2)。
`110000` 北京市、`440300` 深圳市、`810000` 香港、`820000` 澳门。
DataV 支持按层级逐级取，所以不用背代码——`100000_full.json` 是全国省级，
`440000_full.json` 是广东各地级市，`440300_full.json` 是深圳各区。
注意代码会随撤县设区等调整而变化，别抄旧表。

## 用法

| 命令 | 作用 |
|---|---|
| `list` | 列出中国区 v5 区域包、大小、包含省份、App 链接用的 map_id |
| `download <区域>` | 下载整包，支持断点续传；`--check` 校验条目数/总大小/CRC |
| `boundary <adcode...>` | 按 adcode 取行政边界并合并成 GeoJSON（默认阿里 DataV，可换源） |
| `tiles <包...>` | 列出包里与边界相交的瓦片（可直接读 zip，无需解压） |
| `info <文件>` | 查看 PMTiles 头部（zoom 范围、bbox、压缩方式） |
| `extract --src <包...> --region <geojson>` | 按边界裁剪，输出 `Map/<图层>/<前缀>/<文件名>` |
| `verify --out <目录> --src <包...>` | 随机抽样比对，确认没裁丢数据 |

几个常用写法：

```bash
# 只要等高线，不要地貌（省一半以上空间）
python3 coros_map.py extract --src 华北/Map --region beijing.json --out bj-contour --style VCM

# 丢掉最细一级（z13），体积再小一半左右
go-pmtiles extract --maxzoom=12 -q 输入.t 输出.t

# 直接按矩形裁切，不要 GeoJSON
python3 coros_map.py extract --src 华北/Map --bbox 115.42,39.44,117.52,41.07 --out bj
```

## 多个城市（会跨区域包）

只做北京很简单，**多个城市才是真正需要留神的地方**：区域包是按省份分组的，
一个城市属于哪个包得先查，而你想去的几个城市经常分散在好几个包里。

### 1. 先查城市属于哪个区域包

```bash
$ python3 coros_map.py list --province 安徽
区域                              地貌+等高线 (ALL)               仅地貌 (OSM)   省份
East-China                  629.3 MB / 65 文件        299.5 MB / 33 文件   上海、江苏、浙江、安徽、福建、江西、台湾、山东
```

### 2. 下载涉及的每个包（`--src` 可以给多个）

以「北京 + 上海 + 深圳 + 广州 + 澳门 + 香港 + 合肥」七城为例，它们跨了三个包：

```bash
# 取边界：一次传多个 adcode，工具会合并成一个 GeoJSON
python3 coros_map.py boundary 110000 310000 440300 440100 820000 810000 340100 -o cities.json

# 下三个包（北京→华北，上海/合肥→华东，深圳/广州/港澳→华南）
for r in North-China East-China South-China; do
  python3 coros_map.py download $r -o ~/maps --check
done

# 裁剪：--src 直接给 zip，不用解压（也可以给解压后的 Map 目录，或两者混着给）
python3 coros_map.py extract \
    --src ~/maps/map_North-China_ALL_v5.zip ~/maps/map_East-China_ALL_v5.zip ~/maps/map_South-China_ALL_v5.zip \
    --region cities.json --out cities

# 校验：会按城市分组抽样，逐组报告有没有裁丢
python3 coros_map.py verify --out cities \
    --src ~/maps/map_North-China_ALL_v5.zip ~/maps/map_East-China_ALL_v5.zip ~/maps/map_South-China_ALL_v5.zip \
    --region cities.json
```

实测结果（上面七个城市，地貌 + 等高线）：

```
三个区域包 2.9 GB → 49.6 MB / 14 个文件（压到 1.7%）

  C1321001V00.t  VCM  14.0 MB →  6.6 MB   北京主体
  S1321001V00.t  VSM  21.1 MB → 12.4 MB
  C1303223V00.t  VCM  16.5 MB →  102 KB   北京北部山区（延庆/怀柔/密云北）
  C1321222V00.t  VCM  18.3 MB →  2.8 MB   深圳 + 广州 + 香港 + 澳门（同一个瓦片）
  S1321222V00.t  VSM  29.7 MB → 15.1 MB
  C1321201V00.t  VCM  18.5 MB →  1.2 MB   合肥主体
  C1321211V00.t  VCM   2.1 MB →  161 KB   上海主体
  C1303223V00.t  VSM   3.4 MB →   26 KB
  ...

七个城市各抽 10 个境内点 × 2 图层 = 140 项，零丢失
只要等高线（--style VCM）则 11.5 MB
```

### 3. 多城市才会遇到的几个坑

- **相邻城市可能共用同一个瓦片，拆不开。** 瓦片按 level-7 quadkey 切，每块约 240×235 km，
  整个珠三角（深圳/广州/香港/澳门）都在 `1321222` 里，所以「只留深圳」做不到，
  会连广州、香港、澳门一起带上；北京则横跨 `1321001` + `1303223` 两块。
  `tiles` 子命令可以先看清楚边界落在哪些瓦片：
  `python3 coros_map.py tiles ~/maps/map_South-China_ALL_v5.zip --region cities.json`
- **多个包之间有重复瓦片，不用管。** 跨区域边界上的瓦片会在相邻两个包里各存一份
  （内容相同，md5 一致），工具按 `(图层, quadkey)` 自动去重。
- **不要只下一个包。** 想去的城市跨包是常态，`list --province <省>` 用来反查省份
  在哪个包，但一个包只覆盖它列出的那些省份。
- **边界别裁太紧。** 裁掉的地方在手表上就是空白。经常在城际之间跑的话，
  把相邻城市的 adcode 也一起传进去（比如跑步常去燕郊就带上廊坊 `131000`），
  或者自己准备一个比行政边界稍大一圈的 GeoJSON 丢给 `--region`。

## 技术细节

**`.t` 是标准 [PMTiles v3](https://docs.protomaps.com/pmtiles/) 归档**（MVT 矢量瓦片 + gzip），
不是私有格式——所以任何 PMTiles 工具都能读它、裁它、甚至渲染成图。

- 每个文件覆盖一个 **level-7 quadkey**，文件名即 `[CS]<quadkey>V00.t`，
  目录为 `Map/<VCM|VSM>/<quadkey 前三位>/`；`C*` = 等高线，`S*` = 地貌
- 手表按**当前位置的 quadkey** 去找对应文件，所以**文件名必须保持原样**，
  把多个城市合并进一个 `.t` 反而会让手表找不到
- 等高线是 z9–13，地貌是 z8–13
- 一个 level-7 瓦片 ≈ 240 km × 235 km，所以相距近的城市会共用同一个文件
  （例如深圳、广州、香港、澳门都在 `1321222` 里）

**各节点的差异（踩过的坑）**

| 节点 | v5 整包 | v3/v4 整包 |
|---|---|---|
| `map-oss-cn.coros.com`（国内 OSS） | ✅ 有 | ❌ `/map/region/v4/*.zip` 是 **22 字节的空 zip** |
| `osm-map.s3.us-west-1.amazonaws.com` | ❌ 403 | ✅ 有 |

官方 PC 下载页的按钮实际指向的是 S3 上那个 v3/v4 包；v5 的整包只在国内 OSS 上。

**关于体积，两条反直觉的经验**

- 华北全区：等高线 1290 MB vs 地貌 263 MB（4.9:1），但**北京一个瓦片是反过来的**
  （14.0 MB vs 21.1 MB）。原因：等高线体量几乎恒定（各瓦片 2.4–23 MB，极差 10 倍），
  地貌体量随人类活动密度剧烈波动（0.23–21 MB，**极差 93 倍**，草原/戈壁瓦片里
  地貌甚至是 0 字节）。而华北包 94% 的瓦片是农村和山区。
- 裁剪时如果给 `--region` 传了多个**互不相连**的城市，输出文件的头部 bbox 会被写成
  这些城市的**并集外接框**（例如「西宁到上海」）。本工具默认按瓦片拆分 region
  （`--no-per-tile-region` 可关），让每个文件的 bbox 如实描述自己——万一固件是按
  bbox 匹配文件，也不会打开错文件。

## 注意事项

- 输出目录的用法：解压/生成后把**整个 `Map` 目录**合并进手表根目录的 `Map`
  （同名文件覆盖即可，别删原有的），文件名为 `Map/...` 时目录名不要改；
  手表上长按返回键 → 工具箱 → 地图设置 → 地图样式，可切换「地貌 / 等高线 / 混合」
- 裁掉的地方在手表上就是空白，跨出边界就没有地图了；常去的边界区域建议留一点余量
  （`boundary` 支持一次传多个 adcode，也可以自己往 GeoJSON 里加一个稍大的矩形）
- 地图数据版权归 COROS 与 OpenStreetMap 贡献者所有。本工具只做格式转换与裁剪，
  不附带任何地图数据，**请仅用于给自己手表管理离线地图，不要二次分发地图数据**

## License

MIT

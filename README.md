# ncm-pipeline

网易云个人音乐库的小工具。起因是自己歌单攒了一千多首歌，想干两件事：一是把客户端下载的 .ncm 转回普通格式存档，二是统计一下这些年的听歌口味变化。前者做成常驻监视，后者拉自己的数据出报告。

只处理自己账号下载的内容，cookie 和数据都只存在本地，不碰别人的东西。

## 能干什么

- `watch` 盯着下载目录，新的 .ncm 等写完自动转成 flac/mp3，重复运行不会重复转
- `dedup` 成品库按「歌手 - 歌名」去重，同名保留音质最好的那份，其余挪进 `_duplicates`
- `verify` 用 mutagen 抽检转码结果（格式/标签/封面）
- `login` / `fetch` 扫码登录后拉歌单、加入时间、听歌排行、歌曲元数据，存成本地 JSON
- `analyze` 出一份 report.html：每年（或每批次）加入量、语种占比、听新歌还是挖老歌、小众度曲线、年度歌手、收藏了没听过/听了没收藏
- `doctor` 环境自检，`--get-tools` 自动下载转码器

## 安装

不想折腾 Python 的话，去 [Releases](https://github.com/GGX355/ncm-pipeline/releases) 下打包好的 ncm-pipeline.exe，用法一样（`ncm-pipeline.exe doctor -c config.toml`）。

Python >= 3.11：

    git clone https://github.com/GGX355/ncm-pipeline
    cd ncm-pipeline
    pip install -e .
    ncm-pipeline init        # 生成 config.toml，把里面的路径改成自己的
    ncm-pipeline doctor --get-tools

数据分析部分需要一个跑在本机的 API 服务（Node）：

    cd 某个目录
    npm init -y
    npm install @neteaseapireborn/api

然后在 config.toml 的 `[api]` 里把 server_dir 指到这个目录，`ncm-pipeline serve` 启动。

## 一次完整流程

客户端里把下载目录指到 config 的 watch_dirs，对歌单点"下载全部"（注意 VIP 每月有下载额度），然后：

    ncm-pipeline watch                 # 挂着，边下边转，Ctrl+C 退出
    ncm-pipeline verify
    ncm-pipeline dedup --dry-run       # 先看会动哪些文件，没意见再去掉 --dry-run
    ncm-pipeline serve                 # 另开个窗口
    ncm-pipeline login                 # 手机扫码
    ncm-pipeline fetch
    ncm-pipeline analyze               # 报告在 config 配的路径里

以后每年重新 login → fetch → analyze，趋势会接着算。

## 实现时踩过的坑

都是写的过程中真实撞上的，修在代码里了，列出来省得再踩：

- Windows 下 PowerShell 的 `Test-Path "a[b].flac"` 会把方括号当通配符，永远返回 False。像 `SawanoHiroyuki[nZk] - xxx.ncm` 这种文件名会被反复转换。判断存在性要用字面匹配（Python 的 os.path.isfile 没这个问题），另外转码成功落一个 .done 标记双保险。
- 客户端给文件命名时会删掉 Windows 禁用字符（`:` `"` `/` 这些），有的歌元数据里五个歌手文件名里只写三个，歌名里还可能自带 " - "。所以去重和对账不能直接比字符串，得归一化（去标点、转小写）并且把每个切分位置都当候选键。
- @neteaseapireborn/api 和老版接口出入不少：听歌排行是 `/user/record` 不是 `/user/playrecord`，而且只返回前 100；`/song/detail` 拿不到发行时间和热度，要从 `/playlist/track/all` 取；trackIds 的加入时间新版叫 `at`（`t` 恒为 0）；privileges 在响应的独立数组里；分页第一页可能不满页，得循环到取空为止。
- 歌单要是删掉重加过（我就干过一回），所有歌的加入时间会挤在同一天，按年份看趋势完全是假的。analyze 会检测这种情况（单日占比超三成），自动改用歌单内的排列顺序做"收藏批次"——重加时排列顺序保留了收藏先后的信息，越靠前越新。
- matplotlib 画中文图记得设字体（Microsoft YaHei / PingFang SC），再加 `axes.unicode_minus = False`，不然负号是方框。

## 配置

看 config.example.toml，注释挺全。路径都能改，watch_dirs 可以配多个。

## 第三方

- [taurusxin/ncmdump](https://github.com/taurusxin/ncmdump)（MIT）：转码核心。工具按需下载官方编译的 exe，仓库里不带二进制
- [@neteaseapireborn/api](https://www.npmjs.com/package/@neteaseapireborn/api)：自己 npm 装到本地跑，本工具只是往 127.0.0.1 发请求

自用工具，不做批量抓取，不碰版权内容本身。有问题提 issue。

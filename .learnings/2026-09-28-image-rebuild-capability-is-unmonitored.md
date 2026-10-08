# 「服务还能不能重建」是没人查的盲点 —— 容器照跑，而部署能力已经死了

日期：2026-09-28（修 pmf ops 探测时连带发现）
级别：P1（部署能力静默失效；四个 Docker 项目同族风险）
✅已升级(2026-10-04)

## 现象

修 pmf 的 ops 探测（代码改完了、验证逻辑也对），要生效必须先重建镜像 —— 结果
`docker compose build` **直接 exit 2**：

```
docker-php-ext-install -j$(nproc) pdo_pgsql pgsql bcmath zip sockets opcache && pecl install redis ...
...
Installing shared extensions: /usr/local/lib/php/extensions/no-debug-non-zts-20250925/
cp: can't stat 'modules/*': No such file or directory
make: *** [Makefile:89: install-modules] Error 1
```

而这个失败**没有任何检查会发现**：容器还在跑（`Up 12 days (healthy)`）、面板照常打开、
周报里 pmf 一直标「🟢 CI 绿」。**「跑着」与「能重新部署」是两件事。**

## 真因（实测，含一次自我纠错）

第一反应是「并行 make 竞态」（日志里 `install-modules` 报错后紧跟 `make: *** Waiting for unfinished jobs`）。
把 `-j$(nproc)` 去掉改成串行 —— **仍然同样失败** → 假设被证伪，回滚了那个改动（避免留下无依据的改动）。

真因用**逐个隔离**测出来：

```bash
docker run --rm php:8.5-fpm-alpine sh -c 'echo "=== 内置吗 ==="; php -m | grep -i opcache
apk add --no-cache autoconf g++ gcc make pkgconf re2c >/dev/null
for e in bcmath sockets opcache; do echo -n "$e: "; docker-php-ext-install $e >/tmp/e.log 2>&1 && echo OK || { echo FAIL; tail -4 /tmp/e.log; }; done'
```

```
=== 内置吗 ===   Zend OPcache / with Zend OPcache v8.5.11
bcmath: OK      sockets: FAIL(缺 linux-headers，正式构建里有)     opcache: FAIL ← 就是它
```

**`opcache` 基础镜像已内置**，再 `docker-php-ext-install opcache` 会走到 `install-modules`
却产不出 `modules/*` → 整个 RUN 失败。删掉它即可（其余扩展实测全 OK）。

## 可复用判据

1. **`cp: can't stat 'modules/*'` + `install-modules` 报错** =「这个扩展没东西可装」，
   优先怀疑**基础镜像已内置**（`php -m | grep -i <ext>` 一行验证），不要去调并行度。
2. **基础镜像刷新会引爆 Dockerfile 里陈旧的假设**（"这包肯定有/这步骤肯定需要"）。
   旧镜像跑得好 ≠ Dockerfile 还能构建。**定期真的构建一次**是唯一可靠的检查。
3. 「重建能力」必须纳入监控视野，否则它与"服务健康"完全解耦：
   建议每个带 Dockerfile 的项目，把 `docker compose build`（或 `docker build`）纳入**月度**巡检
   ——它慢，但**月度足够**，而"什么时候坏的没人知道"是不可接受的。
4. 排除法要留痕：把 `-j` 改掉又改回来这件事必须写进 commit/日志，否则后人会再试一遍同样的错假设。

## 处置

- pmf `Dockerfile`：去掉 `opcache`，注释写明实测证据与日期；`-j$(nproc)` 保持原样。
- `docker compose build` 连续两次 rc=0；`up -d` 重建容器后 `ops:health-scan` 由 6/6 全失败 → 7/7 正常。
- 关联：pmf 的 ops 探测本身也是「看着在跑其实没查过」（6/6 恒失败、13 万行日志全红）——
  两条合起来说明一件事：**没人验证的自动**（自动部署 / 自动检查）都会在某天变成装饰。

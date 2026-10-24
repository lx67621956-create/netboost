"""
netboost 命令行入口
==================

用法示例::

    python cli.py https://example.com/big.iso
    python cli.py URL -o D:\\Downloads -c 16 -m 64
    python cli.py URL --rate-limit 5M     # 限速 5MB/s
    python cli.py URL --insecure          # 忽略证书错误
    python cli.py -i urls.txt -o D:\\Downloads   # 批量（每行一个 URL）
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time

# Windows 控制台默认 GBK，强制 UTF-8 以免中文/符号报错
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import DownloadError, Downloader, format_size, format_speed  # noqa: E402


def parse_rate(value: str | None) -> float | None:
    if not value:
        return None
    v = value.strip().lower().replace(" ", "")
    mult = 1.0
    for suffix, m in (("gb/s", 1024 ** 3), ("mb/s", 1024 ** 2), ("kb/s", 1024),
                      ("gb", 1024 ** 3), ("mb", 1024 ** 2), ("kb", 1024),
                      ("g", 1024 ** 3), ("m", 1024 ** 2), ("k", 1024), ("b", 1)):
        if v.endswith(suffix):
            v = v[: -len(suffix)]
            mult = m
            break
    try:
        return float(v) * mult
    except ValueError:
        raise argparse.ArgumentTypeError(f"无法解析限速值：{value}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="netboost",
        description="多连接分片下载器：把单连接限速的服务器跑到你的带宽上限",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("url", nargs="?", help="下载链接")
    p.add_argument("-i", "--input", help="从文本文件读取 URL 列表（每行一个）")
    p.add_argument("-o", "--out-dir", default=".", help="保存目录（默认当前目录）")
    p.add_argument("-O", "--dest", help="直接指定保存的完整路径")
    p.add_argument("-c", "--connections", type=int, default=8, help="起始连接数（默认 8）")
    p.add_argument("-m", "--max-connections", type=int, default=32, help="最大连接数（默认 32）")
    p.add_argument("-s", "--chunk-size", help="分片大小，如 8M（默认按文件自动）")
    p.add_argument("--rate-limit", type=parse_rate, help="总限速，如 5M / 800K")
    p.add_argument("--ua", help="自定义 User-Agent")
    p.add_argument("--referer", help="Referer（有些站点需要）")
    p.add_argument("--header", action="append", default=[], metavar="K:V",
                   help="额外请求头，可重复，例如 --header 'Cookie: a=1'")
    p.add_argument("--insecure", action="store_true", help="不校验证书")
    p.add_argument("--no-resume", action="store_true", help="忽略已有进度重新下载")
    p.add_argument("--no-adaptive", action="store_true", help="关闭自动增压")
    p.add_argument("--timeout", type=float, default=30.0, help="超时秒数")
    p.add_argument("--retries", type=int, default=6, help="单块重试次数")
    p.add_argument("-q", "--quiet", action="store_true", help="不显示进度条")
    p.add_argument("--json", action="store_true", help="结束时输出 JSON 摘要")
    return p


def parse_chunk(value: str | None) -> int | None:
    if not value:
        return None
    v = value.strip().lower()
    mult = 1
    for suffix, m in (("m", 1024 ** 2), ("k", 1024), ("g", 1024 ** 3)):
        if v.endswith(suffix):
            v = v[: -len(suffix)]
            mult = m
            break
    try:
        return int(float(v) * mult)
    except ValueError:
        return None


def parse_headers(items: list[str]) -> dict:
    out = {}
    for item in items:
        if ":" in item:
            k, v = item.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def collect_urls(args) -> list[str]:
    urls: list[str] = []
    if args.url:
        urls.append(args.url)
    if args.input:
        with open(args.input, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    urls.append(line)
    return urls


def human_pad(text: str, width: int) -> str:
    """中文等宽估算：CJK 字符占 2 列。"""
    def w(s):
        return sum(2 if ord(ch) > 0x2E80 else 1 for ch in s)
    cur = w(text)
    if cur >= width:
        return text
    return text + " " * (width - cur)


def download_one(url: str, args, index: int = 1, total_items: int = 1) -> bool:
    kw = dict(
        out_dir=args.out_dir,
        connections=args.connections,
        max_connections=args.max_connections,
        chunk_size=parse_chunk(args.chunk_size),
        headers=parse_headers(args.header),
        timeout=args.timeout,
        retries=args.retries,
        rate_limit=args.rate_limit,
        adaptive=not args.no_adaptive,
        resume=not args.no_resume,
        insecure=args.insecure,
        referer=args.referer,
    )
    if args.ua:
        kw["user_agent"] = args.ua
    if args.dest:
        kw["dest"] = args.dest

    d = Downloader(url, **kw)
    prefix = f"[{index}/{total_items}] " if total_items > 1 else ""
    # --json 时把人类可读信息全部送到 stderr，保证 stdout 只有 JSON
    info = sys.stderr if args.json else sys.stdout

    if not args.quiet:
        print(f"{prefix}探测 {url}", file=info, flush=True)
    try:
        d.probe()
    except DownloadError as e:
        print(f"{prefix}✗ {e}", file=sys.stderr, flush=True)
        return False

    if not args.quiet:
        print(f"{prefix}文件名 {d.filename}", file=info, flush=True)
        print(f"{prefix}大小   {format_size(d.total) if d.total else '未知'}", file=info, flush=True)
        if d.supports_ranges:
            print(f"{prefix}分段   支持（多连接加速可用）", file=info, flush=True)
        else:
            print(f"{prefix}分段   不支持 → 只能单连接（服务端限制，无法加速）", file=info, flush=True)
        if d.total and d.total < 4 * 1024 * 1024:
            print(f"{prefix}提示   文件较小（{format_size(d.total)}），加速效果不明显", file=info, flush=True)

    stop_flag = {"v": False}
    show_progress = not args.quiet and not args.json

    def on_progress(dl: Downloader):
        if not show_progress:
            return
        line = f"{prefix}{dl.progress_line()}"
        sys.stdout.write("\r" + human_pad(line, 0) + "\x1b[K")
        sys.stdout.flush()

    try:
        result = d.run(on_progress=on_progress)
    except KeyboardInterrupt:
        d.stop()
        print("\n已中断，进度已保存，下次同命令可续传。", file=sys.stderr)
        return False
    except DownloadError as e:
        if not args.quiet:
            sys.stdout.write("\n")
        print(f"{prefix}✗ {e}", file=sys.stderr, flush=True)
        return False

    status = "未完成（可续传）" if result.partial else "完成"
    if args.json:
        import json
        print(json.dumps({
            "url": url,
            "path": result.path,
            "total": result.total,
            "downloaded": result.downloaded,
            "elapsed_s": round(result.elapsed, 3),
            "avg_speed": round(result.avg_speed, 1),
            "peak_connections": result.peak_connections,
            "resumed": result.resumed,
            "partial": result.partial,
            "md5_verified": result.md5_verified,
        }, ensure_ascii=False))
    else:
        if show_progress:
            sys.stdout.write("\n")
        print(f"{prefix}✓ {status}  {result.path}")
        print(f"{prefix}  用时 {result.elapsed:.1f}s  平均 {format_speed(result.avg_speed)}  "
              f"峰值连接 {result.peak_connections}"
              + ("  已续传" if result.resumed else ""))
        if result.md5_verified is True:
            print(f"{prefix}  校验 MD5 一致")
        elif result.md5_verified is False:
            print(f"{prefix}  ⚠ 校验失败：文件可能损坏", file=sys.stderr)
    return not result.partial


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    urls = collect_urls(args)
    if not urls:
        build_parser().print_help()
        return 2
    ok = True
    for i, url in enumerate(urls, 1):
        ok = download_one(url, args, i, len(urls)) and ok
        if i < len(urls):
            print()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

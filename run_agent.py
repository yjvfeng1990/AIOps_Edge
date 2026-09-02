"""EdgeOps 运维助手启动入口：系统托盘 + 后台 uvicorn 服务。"""

import argparse
import os
import sys
import threading
import time
import webbrowser

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

HOST = os.environ.get("EDGEOPS_HOST", "127.0.0.1")
PORT = int(os.environ.get("EDGEOPS_PORT", "8766"))
URL = f"http://{HOST}:{PORT}"

GRAY = (139, 152, 168)
GREEN = (34, 197, 94)
RED = (239, 68, 68)


def make_icon(color):
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([3, 10, 61, 54], radius=12, fill=(26, 32, 40, 255),
                        outline=color + (255,), width=4)
    try:
        font = ImageFont.truetype("arialbd.ttf", 17)
    except Exception:
        font = ImageFont.load_default()
    d.text((32, 31), "OPS", fill=color + (255,), font=font, anchor="mm")
    return img


class Agent:
    def __init__(self, host=HOST, port=PORT):
        self.host = host
        self.port = port
        self.url = f"http://{host}:{port}"
        self.server = None
        self.thread = None
        self.state = "starting"
        self.error_msg = ""
        self._first_start = True
        self.icon = None

    def _build_icon(self):
        import pystray

        menu = pystray.Menu(
            pystray.MenuItem("打开运维助手", self.open_ui, default=True),
            pystray.MenuItem(lambda item: f"状态：{self.state_text()}", None, enabled=False),
            pystray.MenuItem("重启服务", self.restart_service,
                             enabled=lambda item: self.state != "starting"),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", self.quit),
        )
        return pystray.Icon("edgeops", make_icon(GRAY), f"EdgeOps 启动中… {self.url}", menu)

    def state_text(self):
        if self.state == "running":
            return f"运行中 :{self.port}"
        if self.state == "error":
            return f"异常 ({self.error_msg})"
        return "启动中…"

    def open_ui(self, icon=None, item=None):
        webbrowser.open(self.url)

    def start_server(self):
        import uvicorn
        from edgeops.webui import app

        for _ in range(15):
            if not _port_in_use(self.host, self.port):
                break
            time.sleep(1)

        log_config = {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "plain": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"},
            },
            "handlers": {
                "file": {
                    "class": "logging.FileHandler",
                    "filename": os.path.join(ROOT, "agent_server.log"),
                    "encoding": "utf-8",
                    "formatter": "plain",
                },
            },
            "loggers": {
                "uvicorn": {"handlers": ["file"], "level": "INFO", "propagate": False},
                "uvicorn.error": {"level": "INFO"},
                "uvicorn.access": {"level": "WARNING"},
            },
        }
        config = uvicorn.Config(app, host=self.host, port=self.port,
                                log_config=log_config, lifespan="off")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()

    def _watch(self):
        try:
            deadline = time.time() + 25
            while time.time() < deadline:
                if getattr(self.server, "started", False):
                    self._set_running()
                    return
                if self.thread and not self.thread.is_alive():
                    self._set_failed()
                    return
                time.sleep(0.2)
            self._set_failed()
        except Exception as e:
            _log_crash(e)
            self.state = "error"
            self.error_msg = str(e)

    def _apply_icon(self, color, title):
        if not self.icon:
            return
        try:
            self.icon.icon = make_icon(color)
        except Exception:
            pass
        try:
            self.icon.title = title
        except Exception:
            pass
        try:
            self.icon.update_menu()
        except Exception:
            pass

    def _set_running(self):
        was_first = self._first_start
        self._first_start = False
        self.state = "running"
        self.error_msg = ""
        self._apply_icon(GREEN, f"EdgeOps 运维助手 {self.url}")
        if was_first:
            threading.Thread(target=self._post_start_followup, daemon=True).start()

    def _post_start_followup(self):
        deadline = time.time() + 20
        painted = False
        notified = False
        while time.time() < deadline and not (painted and notified):
            try:
                if self.icon and self.icon.visible:
                    if not painted:
                        self._apply_icon(GREEN, f"EdgeOps 运维助手 {self.url}")
                        painted = True
                    if not notified:
                        self.icon.notify(f"已启动: {self.url}", "EdgeOps 运维助手")
                        notified = True
            except Exception:
                pass
            if not (painted and notified):
                time.sleep(0.5)
        self.open_ui()

    def _set_failed(self):
        err = "启动失败"
        try:
            import requests
            r = requests.get(f"{self.url}/api/config", timeout=2)
            if r.status_code == 200:
                err = "已有实例在运行"
        except Exception:
            pass
        self.state = "error"
        self.error_msg = err
        self._apply_icon(RED, f"EdgeOps {err} {self.url}")

    def restart_service(self, icon=None, item=None):
        """完整重启应用进程：重新加载全部 Python 代码（仅重启 HTTP 服务不会重载模块）。"""
        self.state = "starting"
        self.stop_server()
        try:
            import subprocess
            subprocess.Popen([sys.executable, os.path.abspath(__file__),
                              "--host", self.host, "--port", str(self.port)])
        except Exception:
            _log_crash(RuntimeError("respawn failed"))
            return
        if self.icon:
            self.icon.stop()
        time.sleep(0.8)
        os._exit(0)

    def stop_server(self):
        if self.server:
            self.server.should_exit = True
        if self.thread:
            self.thread.join(timeout=6)
        self.server = None
        self.thread = None

    def quit(self, icon=None, item=None):
        self.stop_server()
        if self.icon:
            self.icon.stop()


def _log_crash(exc):
    import traceback
    log = os.path.join(ROOT, "agent_error.log")
    with open(log, "a", encoding="utf-8") as f:
        f.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}]\n")
        f.write(traceback.format_exc())


def _port_in_use(host, port):
    import socket
    s = socket.socket()
    s.settimeout(1)
    try:
        s.bind((host, port))
        return False
    except OSError:
        return True
    finally:
        s.close()


def _acquire_single_instance(timeout_s=10):
    """获取单实例互斥锁。返回互斥锁句柄（进程存活期间持有），失败返回 None。

    托盘「重启服务」先拉新进程再退旧进程，旧锁会残留一两秒：
    期间关闭句柄重试，等旧进程真正退出；超时视为已有实例在运行。
    """
    import ctypes
    k32 = ctypes.windll.kernel32
    deadline = time.time() + timeout_s
    while True:
        mtx = k32.CreateMutexW(None, False, "Local\\EdgeOpsAgentSingle")
        already = (k32.GetLastError() == 183)
        if not already:
            return mtx  # 保持句柄存活（单实例占位）直到进程退出
        k32.CloseHandle(mtx)  # 关闭句柄再探测，否则自身句柄会让 mutex 永远"已存在"
        if time.time() >= deadline:
            return None
        time.sleep(0.5)


def _main():
    global HOST, PORT, URL

    parser = argparse.ArgumentParser(prog="EdgeOps运维助手")
    parser.add_argument("--host", default=os.environ.get("EDGEOPS_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int,
                        default=int(os.environ.get("EDGEOPS_PORT", "8766")))
    parser.add_argument("--no-tray", action="store_true",
                        help="前台控制台模式运行(不显示托盘图标)")
    args = parser.parse_args()
    HOST, PORT = args.host, args.port
    URL = f"http://{HOST}:{PORT}"

    if _acquire_single_instance() is None:
        webbrowser.open(URL)
        sys.exit(0)

    if _port_in_use(HOST, PORT):
        try:
            import requests as _rq
            r = _rq.get(f"{URL}/api/config", timeout=2)
            if r.status_code == 200:
                webbrowser.open(URL)
                sys.exit(0)
        except Exception:
            pass
        # 端口被占但不是本服务：可能是旧进程尚未退出，短暂等待重试
        for _ in range(8):
            time.sleep(1)
            if not _port_in_use(HOST, PORT):
                break
        else:
            _log_crash(RuntimeError(f"端口 {PORT} 被其他程序占用，无法启动"))
            sys.exit(1)

    agent = Agent(HOST, PORT)
    agent.start_server()
    watcher = threading.Thread(target=agent._watch, daemon=True)
    watcher.start()

    if args.no_tray or os.environ.get("EDGEOPS_NO_TRAY") or os.name != "nt":
        if os.name != "nt":
            print("Linux/非Windows 环境：使用控制台模式（系统托盘仅 Windows 支持）")
        print(f"EdgeOps 运维助手 Web UI: {URL}  (Ctrl+C 退出)")
        try:
            while agent.thread and agent.thread.is_alive():
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            agent.stop_server()
        sys.exit(0 if agent.state == "running" else 1)

    try:
        import pystray
    except ImportError:
        print("缺少 pystray，无法显示托盘: pip install pystray")
        print("回退为前台模式…")
        try:
            while agent.thread and agent.thread.is_alive():
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            agent.stop_server()
        sys.exit(0 if agent.state == "running" else 1)

    agent.icon = agent._build_icon()
    agent.icon.run(setup=lambda ic: setattr(ic, "visible", True))


def main():
    try:
        _main()
    except SystemExit:
        raise
    except Exception:
        _log_crash(sys.exc_info()[1])
        raise


if __name__ == "__main__":
    main()

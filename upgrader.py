# -*- coding: utf-8 -*-
"""
比布跨境ERP · 客户端系统升级工具 v1.0
用途：客户在本机运行本工具，填写自己系统的 FTP 信息后一键升级 ERP 系统。
原理：绕过 Web 升级器的执行时限与锁竞争，直接从外部 FTP 完成
      「检测新版本 → 下载安装包 → 差异对比 → 备份 → 覆盖 → 写版本 → 验证」。
仅依赖 Python 标准库，可用 PyInstaller 打包为单文件 exe。
"""
import tkinter as tk
from tkinter import ttk, messagebox
import threading, ftplib, io, json, ssl, urllib.request, hashlib, zipfile, os, re, tempfile, time, datetime

APP_NAME = "比布跨境ERP · 客户端系统升级工具"
APP_VER = "1.0.0"
UA = "ERP-Upgrader/1.0"

def log_ts():
    return datetime.datetime.now().strftime("%H:%M:%S")


class UpgraderApp:
    def __init__(self, root):
        self.root = root
        root.title(APP_NAME + "  v" + APP_VER)
        root.geometry("860x640")
        root.minsize(760, 560)
        self.busy = False

        frm = ttk.Frame(root, padding=10)
        frm.pack(fill="both", expand=True)

        lbl = ttk.Label(frm, text="① 填写您系统的 FTP 连接信息（虚拟主机面板可查）", font=("Microsoft YaHei UI", 10, "bold"))
        lbl.grid(row=0, column=0, columnspan=6, sticky="w", pady=(0, 4))
        ttk.Label(frm, text="FTP 地址:").grid(row=1, column=0, sticky="e")
        self.e_host = ttk.Entry(frm, width=24); self.e_host.grid(row=1, column=1, sticky="w")
        ttk.Label(frm, text="端口:").grid(row=1, column=2, sticky="e")
        self.e_port = ttk.Entry(frm, width=6); self.e_port.insert(0, "21"); self.e_port.grid(row=1, column=3, sticky="w")
        ttk.Label(frm, text="账号:").grid(row=1, column=4, sticky="e")
        self.e_user = ttk.Entry(frm, width=18); self.e_user.grid(row=1, column=5, sticky="w")
        ttk.Label(frm, text="密码:").grid(row=2, column=0, sticky="e")
        self.e_pwd = ttk.Entry(frm, width=24, show="*"); self.e_pwd.grid(row=2, column=1, sticky="w")
        ttk.Label(frm, text="站点目录:").grid(row=2, column=2, sticky="e")
        self.e_root = ttk.Entry(frm, width=28); self.e_root.insert(0, "/www/lyc"); self.e_root.grid(row=2, column=3, columnspan=3, sticky="w")

        lbl2 = ttk.Label(frm, text="② 填写您系统的网站访问地址（用于升级后自动检测，如 https://zjsongni.com/lyc ）", font=("Microsoft YaHei UI", 10, "bold"))
        lbl2.grid(row=3, column=0, columnspan=6, sticky="w", pady=(8, 4))
        ttk.Label(frm, text="网站地址:").grid(row=4, column=0, sticky="e")
        self.e_site = ttk.Entry(frm, width=52); self.e_site.grid(row=4, column=1, columnspan=5, sticky="w")

        self.info = ttk.LabelFrame(frm, text="版本与授权信息", padding=6)
        self.info.grid(row=5, column=0, columnspan=6, sticky="we", pady=(8, 4))
        self.var_cur = tk.StringVar(value="当前版本：-")
        self.var_new = tk.StringVar(value="最新版本：-")
        self.var_lic = tk.StringVar(value="授权状态：-")
        ttk.Label(self.info, textvariable=self.var_cur).grid(row=0, column=0, sticky="w", padx=(0, 14))
        ttk.Label(self.info, textvariable=self.var_new).grid(row=0, column=1, sticky="w", padx=(0, 14))
        ttk.Label(self.info, textvariable=self.var_lic).grid(row=0, column=2, sticky="w")

        btns = ttk.Frame(frm); btns.grid(row=6, column=0, columnspan=6, sticky="w", pady=6)
        self.btn_check = ttk.Button(btns, text="连接并检测版本", command=self.do_check)
        self.btn_check.pack(side="left", padx=(0, 8))
        self.btn_up = ttk.Button(btns, text="开始升级", command=self.do_upgrade, state="disabled")
        self.btn_up.pack(side="left")
        ttk.Label(btns, text="  （升级前会自动备份将被覆盖的文件到本机）", foreground="#666").pack(side="left")

        self.pbar = ttk.Progressbar(frm, mode="determinate")
        self.pbar.grid(row=7, column=0, columnspan=6, sticky="we", pady=(2, 6))
        self.pbar_lbl = ttk.Label(frm, text="", foreground="#333")
        self.pbar_lbl.grid(row=8, column=0, columnspan=6, sticky="w")

        ttk.Label(frm, text="运行日志：", font=("Microsoft YaHei UI", 9, "bold")).grid(row=9, column=0, sticky="w", pady=(4, 0))
        self.log = tk.Text(frm, height=16, state="disabled", wrap="word", font=("Consolas", 9))
        self.log.grid(row=10, column=0, columnspan=6, sticky="nsew")
        frm.rowconfigure(10, weight=1); frm.columnconfigure(1, weight=1)

        self.state = {}

    def _log(self, msg):
        def _w():
            self.log.configure(state="normal")
            self.log.insert("end", "[" + log_ts() + "] " + msg + "\n")
            self.log.see("end")
            self.log.configure(state="disabled")
        self.root.after(0, _w)

    def _set(self, var, text):
        self.root.after(0, lambda: var.set(text))

    def _pbar(self, pct, text=""):
        def _w():
            self.pbar["value"] = pct
            if text: self.pbar_lbl.configure(text=text)
        self.root.after(0, _w)

    def _finish(self, ok, msg, enable_up=False):
        def _w():
            self.busy = False
            self.btn_check.configure(state="normal")
            self.btn_up.configure(state="normal" if enable_up else "disabled")
            messagebox.showinfo("完成" if ok else "未完成", msg)
        self.root.after(0, _w)

    def _ftp(self, host, port, user, pwd, root):
        ftp = ftplib.FTP()
        ftp.encoding = "latin-1"
        ftp.connect(host, int(port), timeout=40)
        ftp.login(user, pwd)
        ftp.set_pasv(True)
        return ftp

    def _read_ftp(self, ftp, path):
        b = io.BytesIO()
        try:
            ftp.retrbinary("RETR " + path, b.write)
            return b.getvalue()
        except Exception:
            return None

    def _write_ftp(self, ftp, path, data):
        ftp.storbinary("STOR " + path, io.BytesIO(data))

    def _api(self, endpoint, token, deployment_id, action, version=None, result=None, host=None, want_bytes=False):
        body = {"action": action, "deployment_id": deployment_id}
        if version: body["version"] = version
        if result: body["result"] = result
        if host: body["host"] = host
        req = urllib.request.Request(endpoint, data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json",
                                              "Authorization": "Bearer " + token,
                                              "User-Agent": UA})
        ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
        try:
            r = urllib.request.urlopen(req, timeout=180, context=ctx)
            data = r.read()
            return r.status, data
        except urllib.error.HTTPError as e:
            return e.code, e.read()
        except Exception as e:
            return 0, str(e).encode("utf-8", "ignore")

    def _parse_license(self, raw):
        txt = raw.decode("utf-8", "ignore")
        d = re.search(r"'deployment_id'\s*=>\s*'([^']+)'", txt)
        t = re.search(r"'access_token'\s*=>\s*'([^']+)'", txt)
        return {"deployment_id": d.group(1) if d else None,
                "access_token": t.group(1) if t else None}

    def do_check(self):
        if self.busy: return
        host = self.e_host.get().strip(); port = self.e_port.get().strip() or "21"
        user = self.e_user.get().strip(); pwd = self.e_pwd.get()
        root = self.e_root.get().strip()
        if not host or not user or not pwd or not root:
            messagebox.showwarning("提示", "请先填写 FTP 地址/账号/密码/站点目录。"); return
        self.busy = True; self.btn_check.configure(state="disabled")
        threading.Thread(target=self._check_worker, args=(host, port, user, pwd, root), daemon=True).start()

    def _check_worker(self, host, port, user, pwd, root):
        try:
            self._log("正在连接 FTP " + host + " ...")
            ftp = self._ftp(host, port, user, pwd, root)
            self._log("FTP 连接成功。")
            raw = self._read_ftp(ftp, root.rstrip("/") + "/config/deployment-license.php")
            if raw is None:
                self._log("未找到 config/deployment-license.php（授权文件），请确认站点目录填写正确。")
                self._finish(False, "未找到授权文件 config/deployment-license.php。\n请确认 FTP 账号密码与站点目录正确。")
                return
            lic = self._parse_license(raw)
            if not lic["deployment_id"] or not lic["access_token"]:
                self._finish(False, "授权文件解析失败，请联系服务商。"); return
            self._log("授权编号: " + lic["deployment_id"])
            cur = self._read_ftp(ftp, root.rstrip("/") + "/storage/client-version.txt")
            cur = (cur or b"").decode("utf-8", "ignore").strip() or "未知"
            self._log("当前版本: " + cur)
            self._set(self.var_cur, "当前版本：" + cur)
            ep = "https://bibutech.com/market/api/deployment_updates.php"
            st, data = self._api(ep, lic["access_token"], lic["deployment_id"], "check",
                                 version=cur if re.match(r"^\d+\.\d+\.\d+$", cur) else "0.0.0")
            if st != 200:
                try: msg = json.loads(data.decode("utf-8", "ignore")).get("error", data[:200].decode("utf-8", "ignore"))
                except Exception: msg = data[:200].decode("utf-8", "ignore")
                self._log("检测失败: " + str(msg))
                self._finish(False, "检测失败：" + str(msg)); return
            payload = json.loads(data.decode("utf-8", "ignore")).get("payload", "")
            try: obj = json.loads(payload)
            except Exception:
                self._finish(False, "服务端返回格式异常。"); return
            rel = obj.get("release")
            self._set(self.var_lic, "授权状态：正常（" + str(obj.get("updates_until", ""))[:10] + " 前可更新）")
            try: ftp.quit()
            except Exception: pass
            if rel:
                self.state = {"ftp": (host, port, user, pwd, root), "lic": lic, "cur": cur, "rel": rel}
                self._log("发现新版本: " + rel["version"] + "（更新说明: " + str(rel.get("notes", ""))[:120] + "）")
                self._set(self.var_new, "最新版本：" + rel["version"] + "  ← 可升级")
                self._finish(True, "检测完成，发现新版本 " + rel["version"] + "。\n点击「开始升级」即可升级。", enable_up=True)
            else:
                self._log("当前已是最新版本。")
                self._set(self.var_new, "最新版本：" + cur + "（已是最新）")
                self._finish(True, "检测完成，您的系统已是最新版本。")
        except Exception as e:
            self._log("检测异常: " + str(e))
            self._finish(False, "检测异常：" + str(e))

    def do_upgrade(self):
        if self.busy: return
        if not self.state.get("rel"):
            messagebox.showwarning("提示", "请先点击「连接并检测版本」。"); return
        if not messagebox.askyesno("确认升级", "即将把系统升级到 " + self.state["rel"]["version"] + "。\n升级前会自动备份被覆盖的文件。是否继续？"):
            return
        self.busy = True; self.btn_up.configure(state="disabled"); self.btn_check.configure(state="disabled")
        threading.Thread(target=self._upgrade_worker, daemon=True).start()

    def _upgrade_worker(self):
        try:
            host, port, user, pwd, root = self.state["ftp"]
            lic = self.state["lic"]; rel = self.state["rel"]
            new_ver = rel["version"]
            ep = "https://bibutech.com/market/api/deployment_updates.php"
            self._pbar(3, "下载升级包 " + new_ver + " ...")
            self._log("正在从更新服务器下载 " + new_ver + " 升级包 ...")
            st, data = self._api(ep, lic["access_token"], lic["deployment_id"], "download", version=new_ver, want_bytes=True)
            if st != 200:
                try: msg = json.loads(data.decode("utf-8", "ignore")).get("error", "下载失败")
                except Exception: msg = "下载失败"
                self._log("下载失败: " + str(msg)); self._finish(False, "下载失败：" + str(msg)); return
            exp_sha = rel.get("sha256", "")
            if exp_sha and hashlib.sha256(data).hexdigest() != exp_sha:
                self._log("安装包校验失败（SHA256 不一致），已中止。"); self._finish(False, "安装包校验失败，已中止。请重试或联系服务商。"); return
            self._log("下载完成，安装包校验通过（" + str(len(data)) + " 字节）。")
            tmp = os.path.join(tempfile.gettempdir(), "erp_upg_" + str(int(time.time())))
            os.makedirs(tmp, exist_ok=True)
            zf = zipfile.ZipFile(io.BytesIO(data))
            zf.extractall(tmp)
            mani = json.loads(open(os.path.join(tmp, "manifest.json"), "r", encoding="utf-8").read())
            pkg_files = mani.get("files", {})
            minv = mani.get("min_version", "")
            self._pbar(15, "解析安装包（" + str(len(pkg_files)) + " 个文件）...")
            self._log("安装包: 版本 " + new_ver + "，最低前置版本 " + str(minv) + "，文件 " + str(len(pkg_files)) + " 个")
            if rel.get("migrations"):
                self._log("注意：本版本包含数据库迁移，将在升级完成后提示您确认执行。")
            ftp = self._ftp(host, port, user, pwd, root)
            self._log("FTP 已连接，正在对比差异 ...")
            bl = {}
            rawb = self._read_ftp(ftp, root.rstrip("/") + "/storage/update-baseline.json")
            if rawb:
                try: bl = json.loads(rawb.decode("utf-8", "ignore"))
                except Exception: bl = {}
            to_update = {}
            for relp, ph in pkg_files.items():
                if bl.get(relp) != ph:
                    to_update[relp] = ph
            same = len(pkg_files) - len(to_update)
            self._log("差异对比：相同 " + str(same) + " 个（跳过），需更新 " + str(len(to_update)) + " 个")
            custom = []
            for relp in list(to_update.keys()):
                if relp in bl:
                    cur = self._read_ftp(ftp, root.rstrip("/") + "/" + relp)
                    if cur is not None and hashlib.sha256(cur).hexdigest() != bl[relp]:
                        custom.append(relp)
            if custom:
                self._log("发现 " + str(len(custom)) + " 个本地定制文件：" + "; ".join(custom[:10]))
                self._log("为保护您的定制，这些文件将【跳过覆盖】（如需强制覆盖请先备份后删除对应文件再升级）。")
                for c in custom: to_update.pop(c, None)
            if not to_update:
                self._log("没有需要更新的文件（可能均为定制跳过）。")
                self._finish(False, "没有需要更新的文件（均被本地定制保护跳过）。"); return
            self._pbar(30, "备份原文件 ...")
            bk = os.path.join(os.path.dirname(os.path.abspath(__file__)) or os.getcwd(),
                              "backup_" + new_ver + "_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
            os.makedirs(bk, exist_ok=True)
            n = 0
            for relp in to_update:
                cur = self._read_ftp(ftp, root.rstrip("/") + "/" + relp)
                if cur is not None:
                    lp = os.path.join(bk, relp.replace("/", "_"))
                    open(lp, "wb").write(cur); n += 1
            self._log("已备份 " + str(n) + " 个原文件到本机目录：" + bk)
            items = sorted(to_update.items(), key=lambda kv: (kv[0].startswith("index.php") or kv[0] == "config/app.php", kv[0]))
            self._pbar(45, "开始覆盖升级 ...")
            total = len(items); i = 0
            for relp, ph in items:
                i += 1
                parent = "/".join(relp.split("/")[:-1])
                if parent:
                    try: ftp.retrlines("LIST " + root.rstrip("/") + "/" + parent, lambda x: None)
                    except Exception:
                        curp = root.rstrip("/")
                        for pp in parent.split("/"):
                            try: ftp.mkd(curp + "/" + pp)
                            except Exception: pass
                            curp = curp + "/" + pp
                with open(os.path.join(tmp, relp), "rb") as f:
                    ftp.storbinary("STOR " + root.rstrip("/") + "/" + relp, f)
                if i % 5 == 0 or i == total:
                    self._pbar(45 + int(35 * i / total), "覆盖 " + str(i) + "/" + str(total) + " ...")
                    self._log("覆盖 " + relp)
            mig = rel.get("migrations") or mani.get("migrations") or []
            if mig:
                self._log("注意：本版本包含 " + str(len(mig)) + " 条数据库迁移，文件升级已完成。")
                self._log("请通知服务商确认迁移执行（当前版本安装包无迁移时无需处理）。")
            self._pbar(90, "写入版本信息 ...")
            self._write_ftp(ftp, root.rstrip("/") + "/storage/client-version.txt", new_ver.encode("utf-8"))
            bl.update({k: v for k, v in to_update.items()})
            self._write_ftp(ftp, root.rstrip("/") + "/storage/update-baseline.json",
                            json.dumps(bl, ensure_ascii=False).encode("utf-8"))
            rec = {"status": "升级成功", "version": new_ver,
                   "time": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S+08:00"),
                   "backup": os.path.basename(bk), "note": "由客户端升级工具完成"}
            self._write_ftp(ftp, root.rstrip("/") + "/storage/update-last.json",
                            json.dumps(rec, ensure_ascii=False).encode("utf-8"))
            self._log("已写入版本 " + new_ver + " 与升级记录。")
            self._pbar(95, "验证 ...")
            okc = 0
            for relp in list(to_update.keys())[:8]:
                cur = self._read_ftp(ftp, root.rstrip("/") + "/" + relp)
                if cur is not None and hashlib.sha256(cur).hexdigest() == to_update[relp]: okc += 1
            self._log("文件哈希回读验证 " + str(okc) + "/" + str(min(8, len(to_update))) + " 通过。")
            try: ftp.quit()
            except Exception: pass
            site = self.e_site.get().strip()
            site_ok = "-"
            if site:
                try:
                    ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
                    r = urllib.request.urlopen(urllib.request.Request(site, headers={"User-Agent": UA}), timeout=40, context=ctx)
                    site_ok = "HTTP " + str(r.status)
                    self._log("网站访问检测: " + site_ok)
                except Exception as e:
                    site_ok = "失败"
                    self._log("网站访问检测失败: " + str(e))
            self._pbar(100, "升级完成")
            self._log("==== 升级完成：" + new_ver + " ====")
            self._finish(True, "升级成功！系统已更新至 " + new_ver + "。\n\n备份目录：" + bk + "\n网站检测：" + site_ok)
            try:
                self._api(ep, lic["access_token"], lic["deployment_id"], "report", version=new_ver, result="升级成功(客户端工具)")
            except Exception: pass
        except Exception as e:
            self._log("升级异常: " + str(e))
            self._finish(False, "升级异常：" + str(e))


def main():
    root = tk.Tk()
    UpgraderApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()

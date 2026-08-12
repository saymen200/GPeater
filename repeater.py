#!/usr/bin/env python3
import os, re, shlex, subprocess, time, tkinter as tk
from tkinter import ttk, scrolledtext, messagebox

BASE = "/tmp"
HISTDIR = os.path.join(BASE, "history")
SSH_TARGET = "user@vm"
REMOTE_CURL = "/opt/cprocsp/bin/amd64/curl"
os.makedirs(HISTDIR, exist_ok=True)


def parse_request(text):
    head, sep, body = text.partition("\n\n")
    if not sep:
        raise ValueError("Нет пустой строки между заголовками и телом (оставь пустую строку в конце, даже без тела)")
    lines = head.split("\n")
    m = re.match(r"(\S+)\s+(\S+)\s+HTTP/\S+", lines[0].strip())
    if not m:
        raise ValueError(f"Не могу разобрать строку запроса: {lines[0]!r}")
    method, path = m.group(1), m.group(2)
    headers = []
    for line in lines[1:]:
        if not line.strip():
            continue
        k, _, v = line.partition(":")
        headers.append((k.strip(), v.strip()))
    return method, path, headers, body.rstrip("\n")


def build_and_send(method, path, headers, body):
    host = None
    clean_headers = []
    has_ct = False
    for k, v in headers:
        lk = k.lower()
        if lk == "host":
            host = v
            continue
        if lk == "content-length":
            continue
        if lk == "content-type":
            has_ct = True
        clean_headers.append((k, v))
    if not host:
        raise ValueError("Нет заголовка Host: — не знаю куда слать")

    url = f"https://{host}{path}"
    cmd = [REMOTE_CURL, "-sk", "-i", "-X", method, url]
    for k, v in clean_headers:
        cmd += ["-H", f"{k}: {v}"]
    if body:
        if not has_ct and method in ("POST", "PUT", "PATCH"):
            cmd += ["-H", "Content-Type: application/json"]
        cmd += ["--data-raw", body]

    remote_cmd = shlex.join(cmd)
    t0 = time.time()
    proc = subprocess.run(["ssh", SSH_TARGET, remote_cmd], capture_output=True, timeout=35)
    dt = round((time.time() - t0) * 1000)
    return proc, dt, url


def save_history(method, path, headers, body, req_raw, resp_text, dt, url):
    ts = time.strftime("%Y%m%d_%H%M%S_%f")
    fname = os.path.join(HISTDIR, f"{ts}.txt")
    with open(fname, "w") as f:
        f.write(f"# {method} {url}  ({dt} ms)\n\n--- REQUEST ---\n{req_raw}\n--- RESPONSE ---\n{resp_text}")
    return fname


def load_history_file(fname):
    with open(fname) as f:
        text = f.read()
    _, _, rest = text.partition("--- REQUEST ---\n")
    req, _, resp = rest.partition("--- RESPONSE ---\n")
    return req.rstrip("\n"), resp


STATUS_COLORS = {"2": "#2e7d32", "3": "#e0af68", "4": "#c62828", "5": "#c62828"}


class RepeaterGUI:
    def __init__(self, root):
        self.root = root
        root.title("GOST Repeater")
        root.geometry("1100x750")

        top = ttk.Frame(root, padding=6)
        top.pack(fill="x")
        ttk.Label(top, text="Host:").pack(side="left")
        self.host_e = ttk.Entry(top, width=30)
        self.host_e.insert(0, "host")
        self.host_e.pack(side="left", padx=4)
        ttk.Label(top, text="Path:").pack(side="left")
        self.path_e = ttk.Entry(top, width=25)
        self.path_e.insert(0, "/")
        self.path_e.pack(side="left", padx=4)
        ttk.Label(top, text="Method:").pack(side="left")
        self.method_cb = ttk.Combobox(top, width=8, values=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"])
        self.method_cb.set("GET")
        self.method_cb.pack(side="left", padx=4)
        ttk.Button(top, text="New", command=self.new_request).pack(side="left", padx=6)
        ttk.Button(top, text="Send  (Ctrl+Enter)", command=self.send).pack(side="left", padx=6)
        self.status_lbl = ttk.Label(top, text="", font=("monospace", 10, "bold"))
        self.status_lbl.pack(side="left", padx=10)

        paned = ttk.PanedWindow(root, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=6, pady=6)

        hist_frame = ttk.Frame(paned, width=260)
        ttk.Label(hist_frame, text="История").pack(anchor="w")
        self.hist_list = tk.Listbox(hist_frame)
        self.hist_list.pack(fill="both", expand=True)
        self.hist_list.bind("<<ListboxSelect>>", self.load_from_history)
        paned.add(hist_frame, weight=1)

        main_frame = ttk.Frame(paned)
        paned.add(main_frame, weight=4)

        ttk.Label(main_frame, text="Request:").pack(anchor="w")
        self.req_box = scrolledtext.ScrolledText(main_frame, height=16, font=("monospace", 10))
        self.req_box.pack(fill="both", expand=True)
        self.req_box.bind("<Control-Return>", lambda e: self.send())

        ttk.Label(main_frame, text="Response:").pack(anchor="w", pady=(8, 0))
        self.resp_box = scrolledtext.ScrolledText(main_frame, height=20, font=("monospace", 10))
        self.resp_box.pack(fill="both", expand=True)

        self.new_request()
        self.refresh_history()

    def new_request(self):
        host = self.host_e.get().strip()
        path = self.path_e.get().strip() or "/"
        method = self.method_cb.get().strip() or "GET"
        content = f"{method} {path} HTTP/1.1\nHost: {host}\nUser-Agent: gcurl-repeater\nAccept: */*\n\n"
        self.req_box.delete("1.0", "end")
        self.req_box.insert("1.0", content)
        self.resp_box.delete("1.0", "end")
        self.status_lbl.config(text="")

    def send(self):
        req_raw = self.req_box.get("1.0", "end-1c")
        try:
            method, path, headers, body = parse_request(req_raw)
            proc, dt, url = build_and_send(method, path, headers, body)
        except Exception as e:
            messagebox.showerror("Ошибка", str(e))
            return

        out = proc.stdout.decode(errors="replace")
        if proc.returncode != 0:
            out += f"\n\n[curl завершился с кодом {proc.returncode}]\n" + proc.stderr.decode(errors="replace")

        self.resp_box.delete("1.0", "end")
        self.resp_box.insert("1.0", out)

        m = re.search(r"HTTP/\S+\s+(\d{3})", out)
        code = m.group(1) if m else "ERR"
        color = STATUS_COLORS.get(code[0], "#888")
        self.status_lbl.config(text=f"{code}  ·  {dt} ms  ·  {len(out)} B", foreground=color)

        save_history(method, path, headers, body, req_raw, out, dt, url)
        self.refresh_history()

    def refresh_history(self):
        self.hist_list.delete(0, "end")
        files = sorted(os.listdir(HISTDIR), reverse=True)
        self._hist_files = files
        for fn in files:
            self.hist_list.insert("end", fn)

    def load_from_history(self, event):
        sel = self.hist_list.curselection()
        if not sel:
            return
        fname = os.path.join(HISTDIR, self._hist_files[sel[0]])
        req, resp = load_history_file(fname)
        self.req_box.delete("1.0", "end")
        self.req_box.insert("1.0", req)
        self.resp_box.delete("1.0", "end")
        self.resp_box.insert("1.0", resp)
        m = re.search(r"HTTP/\S+\s+(\d{3})", resp)
        code = m.group(1) if m else "ERR"
        color = STATUS_COLORS.get(code[0], "#888")
        self.status_lbl.config(text=f"[история] {code}", foreground=color)


if __name__ == "__main__":
    root = tk.Tk()
    RepeaterGUI(root)
    root.mainloop()

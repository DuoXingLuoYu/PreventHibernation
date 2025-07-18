#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
功能：
1. 每 cycle_sec 秒播放 play_sec 秒正弦；
2. 托盘最小化，双击托盘图标恢复窗口；
3. GUI 实时调节参数并永久保存；
4. 左右声道瞬时幅值可视化；
5. 兼容 PyInstaller 单文件打包。
"""
import tkinter as tk
from tkinter import ttk
import sounddevice as sd
import numpy as np
import json
import os
import sys
import threading
import pystray
from PIL import Image

# ----------------- 资源路径兼容 PyInstaller -----------------
def resource_path(rel):
    """返回正确的资源路径（PyInstaller 或源码目录）"""
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, rel)
    return os.path.join(os.path.abspath('.'), rel)

# ----------------- 参数文件 -----------------
PARAM_FILE = resource_path('params.json')

DEFAULT = {
    'frequency': 440.0,
    'volume': 0.001,
    'play_sec': 1.0,
    'cycle_sec': 60.0
}

def load_params():
    if os.path.exists(PARAM_FILE):
        with open(PARAM_FILE, encoding='utf-8') as f:
            data = json.load(f)
            return {k: type(DEFAULT[k])(data.get(k, DEFAULT[k])) for k in DEFAULT}
    return DEFAULT.copy()

def save_params(params):
    with open(PARAM_FILE, 'w', encoding='utf-8') as f:
        json.dump(params, f, indent=2)

params = load_params()
samplerate = int(sd.query_devices(None, 'output')['default_samplerate'])

# ----------------- 循环播放器 -----------------
class CyclicPlayer:
    def __init__(self):
        self._thread = None
        self._stop_evt = threading.Event()

    def _run(self):
        fs = samplerate
        play_samples = int(params['play_sec'] * fs)
        cycle_samples = int(params['cycle_sec'] * fs)
        silence = np.zeros((cycle_samples - play_samples, 2), dtype=np.float32)

        while not self._stop_evt.is_set():
            # 生成正弦
            t = np.arange(play_samples) / fs
            sig = params['volume'] * np.sin(2 * np.pi * params['frequency'] * t)
            sig = np.column_stack([sig, sig]).astype(np.float32)

            # 播放
            with sd.OutputStream(samplerate=fs, channels=2, blocksize=1024):
                sd.play(sig, samplerate=fs)
                sd.wait()
            update_status('等待中')
            update_tray_tooltip('等待下一轮')

            # 静音等待
            sd.play(silence, samplerate=fs)
            for _ in range(int(params['cycle_sec'] - params['play_sec'])):
                if self._stop_evt.wait(1):
                    break
        update_status('已停止')
        update_tray_tooltip('已停止')

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_evt.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_evt.set()
        sd.stop()

player = CyclicPlayer()

# ----------------- GUI -----------------
root = None
left_lbl = right_lbl = status_lbl = None
freq_ent = vol_ent = play_ent = cycle_ent = None

def create_window():
    global root, left_lbl, right_lbl, status_lbl
    global freq_ent, vol_ent, play_ent, cycle_ent

    root = tk.Tk()
    root.title('禁止休眠')
    root.resizable(False, False)
    root.protocol('WM_DELETE_WINDOW', hide_window)
    root.bind('<Unmap>', lambda e: hide_window() if root.state() == 'iconic' else None)

    frm = ttk.LabelFrame(root, text='参数', padding=8)
    frm.grid(row=0, column=0, padx=8, pady=8, sticky='ew')

    labels = ['频率(Hz):', '音量(0-1):', '播放时长(s):', '周期(s):']
    vars = [params['frequency'], params['volume'], params['play_sec'], params['cycle_sec']]
    ents = []
    for i, (lab, val) in enumerate(zip(labels, vars)):
        ttk.Label(frm, text=lab).grid(row=i, column=0, sticky='e')
        ent = ttk.Entry(frm, width=10)
        ent.insert(0, val)
        ent.grid(row=i, column=1)
        ents.append(ent)
    freq_ent, vol_ent, play_ent, cycle_ent = ents

    ttk.Button(frm, text='应用', command=apply_params).grid(row=len(labels), column=0, columnspan=2, pady=4)

    status_lbl = ttk.Label(root, text='已停止', foreground='red')
    status_lbl.grid(row=1, column=0, pady=4)

    level_frm = ttk.LabelFrame(root, text='瞬时幅值', padding=8)
    level_frm.grid(row=2, column=0, padx=8, pady=4)
    left_lbl = ttk.Label(level_frm, text='L: 0.000', font=('Consolas', 11))
    left_lbl.grid(row=0, column=0, padx=6)
    right_lbl = ttk.Label(level_frm, text='R: 0.000', font=('Consolas', 11))
    right_lbl.grid(row=0, column=1, padx=6)

    toggle_btn = ttk.Button(root, text='开始', command=toggle_play)
    toggle_btn.grid(row=3, column=0, pady=6)

    return root

def apply_params():
    try:
        params['frequency'] = float(freq_ent.get())
        params['volume'] = max(0, min(1, float(vol_ent.get())))
        params['play_sec'] = max(0.1, float(play_ent.get()))
        params['cycle_sec'] = max(0.1, float(cycle_ent.get()))
    except ValueError:
        return
    save_params(params)

def update_status(txt):
    if root:
        status_lbl.config(text=txt, foreground='green' if '播放' in txt else 'red')

def update_levels(left, right):
    if root:
        left_lbl.config(text=f'L: {left:+.3f}')
        right_lbl.config(text=f'R: {right:+.3f}')

def toggle_play():
    if player._thread and player._thread.is_alive():
        player.stop()
    else:
        player.start()

# ----------------- 托盘 -----------------
tray_icon = None

def icon_image():
    try:
        return Image.open(resource_path('icon.ico'))
    except Exception:
        img = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
        return img

def show_window(icon=None, item=None):
    icon.stop()
    threading.Thread(target=lambda: create_window().mainloop(), daemon=True).start()

def hide_window():
    root.withdraw()
    show_tray()

def tray_toggle(icon, item):
    toggle_play()

def exit_all(icon, item):
    player.stop()
    icon.stop()
    os._exit(0)

def update_tray_tooltip(tip):
    if tray_icon:
        tray_icon.title = tip

def show_tray():
    global tray_icon
    tray_icon = pystray.Icon('CyclicNoise', icon_image(), menu=pystray.Menu(
        pystray.MenuItem('显示', show_window),
        pystray.MenuItem('开始/停止', tray_toggle),
        pystray.MenuItem('退出', exit_all)
    ))
    tray_icon.run_detached()

# ----------------- 入口 -----------------
if __name__ == '__main__':
    create_window().mainloop()
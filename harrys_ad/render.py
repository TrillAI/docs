#!/usr/bin/env python3
"""Harry's Gas Station — cinematic commercial renderer.
Frames are synthesized in numpy (virtual camera: pan/zoom/drift + ease curves,
film grain, vignette, unsharp) and piped raw to ffmpeg (libx264, yuv420p, 30fps).
Audio: VO (recorded) + generated soft music bed with sidechain-ish ducking.
"""
import subprocess, math, wave, os, sys
import numpy as np
from PIL import Image
import imageio_ffmpeg

FF = imageio_ffmpeg.get_ffmpeg_exe()
AD = "/home/user/docs/harrys_ad"
W, H, FPS = 1280, 720, 30
SR = 44100

# ---------------------------------------------------------------- easing
def ease_io(t):  return 0.5 - 0.5 * math.cos(math.pi * min(max(t,0),1))
def ease_in(t):  return min(max(t,0),1) ** 2
def ease_out(t): return 1 - (1 - min(max(t,0),1)) ** 2

def lerp(a, b, t): return a + (b - a) * t

# ------------------------------------------------------- virtual camera
def load(path):
    im = Image.open(path).convert("RGB")
    return np.asarray(im, dtype=np.float32)

Hb, Wb = None, None
def set_base(img):
    global Hb, Wb
    Hb, Wb = img.shape[:2]

def frame(img, t01, z0, z1, drift=(0.0, 0.0), ease=ease_io, roll_deg=0.0):
    """t01 in [0,1]; z = scale (1.0 shows full frame); drift in output px over full move."""
    set_base(img)
    s  = lerp(z0, z1, ease(t01))
    fz = s * min(Wb / W, Hb / H)          # source px per output px
    cx = Wb / 2 + (W - W * (Wb / (W * fz))) / 2 if False else Wb / 2
    cx = Wb / 2 + drift[0] * ease(t01) * fz   # pan: shift sample window
    cy = Hb / 2 + drift[1] * ease(t01) * fz
    invz = 1.0 / fz
    xx, yy = np.meshgrid(np.arange(W, dtype=np.float32),
                         np.arange(H, dtype=np.float32))
    if roll_deg:
        a = math.radians(roll_deg) * ease(t01)
        ca, sa = math.cos(a), math.sin(a)
        xr = (xx - W/2) * ca - (yy - H/2) * sa + W/2
        yr = (xx - W/2) * sa + (yy - H/2) * ca + H/2
        xx, yy = xr, yr
    sx = (xx - W/2) * invz + cx
    sy = (yy - H/2) * invz + cy
    x0 = np.clip(np.floor(sx).astype(np.int32), 0, Wb - 2)
    y0 = np.clip(np.floor(sy).astype(np.int32), 0, Hb - 2)
    fx = np.clip(sx - x0, 0, 1)[..., None]
    fy = np.clip(sy - y0, 0, 1)[..., None]
    a = img[y0, x0];     b = img[y0, x0 + 1]
    c = img[y0 + 1, x0]; d = img[y0 + 1, x0 + 1]
    out = (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy
    return out

# ------------------------------------------------------------ look pass
yy_g, xx_g = np.meshgrid(np.linspace(-1, 1, W, dtype=np.float32),
                         np.linspace(-1, 1, H, dtype=np.float32))
VIG = (1.0 - 0.20 * np.clip((xx_g**2 + yy_g**2) - 0.25, 0, 1.4) / 1.4)[..., None].astype(np.float32)

def unsharp(im):
    blur = (np.roll(im, 1, 0) + np.roll(im, -1, 0) + np.roll(im, 1, 1) + np.roll(im, -1, 1) + 4*im) / 8.0
    return im + 0.35 * (im - blur)

def grade(im, warmth=1.0):
    im = im * VIG
    im[..., 0] *= 1.02 * warmth   # R
    im[..., 1] *= 1.005           # G
    im[..., 2] *= 1.03            # B  (teal-ish lift in shadows via gamma below)
    im = 255.0 * np.power(np.clip(im / 255.0, 0, 1), 0.96)
    return im

def encode_segment(name, img, nframes, cam_fn, warmth=1.0):
    out = os.path.join(AD, name)
    p = subprocess.Popen(
        [FF, "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
         "-r", str(FPS), "-i", "-", "-an", "-c:v", "libx264", "-preset", "medium",
         "-crf", "16", "-pix_fmt", "yuv420p", out],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    rng = np.random.default_rng(7)
    for i in range(nframes):
        f = cam_fn(i / (nframes - 1))
        f = unsharp(f)
        f = grade(f, warmth)
        f += rng.normal(0, 2.2, f.shape).astype(np.float32)   # film grain
        p.stdin.write(np.clip(f, 0, 255).astype(np.uint8).tobytes())
    p.stdin.close(); p.wait()
    print("  encoded", name)

# ------------------------------------------------------------- segments
def render_all(T):
    segs = []
    def add(name, img_path, dur, cam_fn, warmth=1.0):
        n = int(round(dur * FPS))
        encode_segment(name, load(img_path), n, cam_fn, warmth)
        segs.append((name, n / FPS))

    print("Rendering segments ...")

    # S1 — hero establishing: slow settle down & right, gentle push-in
    add("seg1.mp4", f"{AD}/shot1_hero.png", T["s1"],
        lambda t: frame(IMG["s1"], t, 1.00, 1.13, (26, 20), ease_io))

    # S2 — happy couple: drift up-left, slight pull-back
    add("seg2.mp4", f"{AD}/shot2_couple.png", T["s2"],
        lambda t: frame(IMG["s2"], t, 1.13, 1.02, (-30, -24), ease_io), 1.04)

    # S3 — store, cashier & shoppers: lateral dolly right
    add("seg3.mp4", f"{AD}/shot3_store.png", T["s3"],
        lambda t: frame(IMG["s3"], t, 1.06, 1.15, (36, 10), ease_io))

    # S4 — coffee handoff: slow romantic push-in
    add("seg4.mp4", f"{AD}/shot4_coffee.png", T["s4"],
        lambda t: frame(IMG["s4"], t, 1.03, 1.17, (0, -8), ease_io), 1.05)

    # S5 — between the pumps: accelerating forward dolly down the lane
    add("seg5.mp4", f"{AD}/shot5_lane.png", T["s5"],
        lambda t: frame(IMG["s5"], t, 1.02, 1.30, (0, -14), ease_in))

    # S6 — the sign: push in, then soften to blur (rack focus to end card)
    def s6(t):
        blur_t = ease_in(max(0.0, (t - 0.80) / 0.20))
        f = frame(IMG["s6"], t, 1.05, 1.32, (6, -10), ease_io)
        if blur_t > 0:
            k = max(1, int(1 + blur_t * 12))
            im = Image.fromarray(np.clip(f, 0, 255).astype(np.uint8))
            im = im.resize((max(2, W // k), max(2, H // k)), Image.BILINEAR)
            f = np.asarray(im.resize((W, H), Image.BILINEAR), dtype=np.float32)
        return f
    add("seg6.mp4", f"{AD}/shot6_sign.png", T["s6"], s6)

    # S7 — end card: tiny push on the logo
    add("seg7.mp4", f"{AD}/shot7_card.png", T["s7"],
        lambda t: frame(IMG["s7"], t, 1.04, 1.10, (0, 0), ease_io))

    # concat
    lst = os.path.join(AD, "concat.txt")
    with open(lst, "w") as fh:
        for name, _ in segs:
            fh.write(f"file '{AD}/{name}'\n")
    subprocess.run([FF, "-y", "-f", "concat", "-safe", "0", "-i", lst,
                    "-c", "copy", f"{AD}/video_silent.mp4"],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    total = sum(d for _, d in segs)
    print("  video_silent.mp4", round(total, 2), "s")
    return total

# ---------------------------------------------------------------- audio
def read_audio(path):
    raw = subprocess.run([FF, "-v", "quiet", "-i", path, "-f", "f32le",
                          "-ac", "1", "-ar", str(SR), "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.float32).copy()

def write_wav(path, x):
    x16 = (np.clip(x, -1, 1) * 32767).astype(np.int16)
    w = wave.open(path, "w"); w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
    w.writeframes(x16.tobytes()); w.close()

def moving_avg(x, n):
    c = np.cumsum(np.pad(x, (n//2, n - n//2), mode="edge"))
    return ((c[n:] - c[:-n]) / n)[: len(x)] if len(c) > n else np.full_like(x, x.mean())

def music_bed(total):
    """Soft warm arpeggio pad (Am F C G), generated in numpy."""
    n = int(total * SR)
    t_all = np.arange(n) / SR
    bpm = 100.0; eighth = 60.0 / bpm / 2
    bars = [[57, 60, 64, 69], [53, 57, 60, 65], [60, 64, 67, 72], [55, 59, 62, 67]]
    pattern = [0, 1, 2, 3, 2, 1, 0, 2]
    out = np.zeros(n, dtype=np.float32)
    step = 0
    t0 = 0.0
    while t0 < total:
        bar = bars[(step // 8) % 4]
        for k in range(8):
            ts = t0 + k * eighth
            if ts >= total: break
            midi = bar[pattern[k]]
            f = 440.0 * 2 ** ((midi - 69) / 12)
            i0 = int(ts * SR); dur = eighth * 1.6
            i1 = min(n, i0 + int(dur * SR))
            tt = np.arange(i1 - i0) / SR
            env = np.exp(-tt * 3.0) * (1 - np.exp(-tt * 200))
            tone = (np.sin(2*np.pi*f*tt) + 0.35*np.sin(2*np.pi*2*f*tt)) * env * 0.10
            out[i0:i1] += tone.astype(np.float32)
        t0 += 8 * eighth; step += 8
    # deep warm pad on bar roots
    for bstart in np.arange(0, total, 8 * eighth):
        bar = bars[int((bstart / (8 * eighth))) % 4]
        f = 440.0 * 2 ** ((bar[0] - 12 - 69) / 12)
        i0 = int(bstart * SR); i1 = min(n, i0 + int(8 * eighth * SR))
        tt = np.arange(i1 - i0) / SR
        env = np.minimum(tt / 1.0, 1) * np.minimum((8 * eighth - tt) / 1.0, 1)
        out[i0:i1] += (0.05 * np.sin(2*np.pi*f*tt) * env).astype(np.float32)
    # fade in / out
    fi, fo = int(2.0*SR), int(3.0*SR)
    out[:fi] *= np.linspace(0, 1, fi)**2
    out[-fo:] *= np.linspace(1, 0, fo)**2
    return out

def build_audio(total, T):
    print("Building audio ...")
    voA = read_audio(f"{AD}/voA.mp3"); voB = read_audio(f"{AD}/voB.mp3")
    n = int(total * SR)
    vo = np.zeros(n, dtype=np.float32)
    sA = int(3.2 * SR)                                   # VO part A start
    s7_start = T["s1"]+T["s2"]+T["s3"]+T["s4"]+T["s5"]+T["s6"]
    sB_end = s7_start + 0.9                               # VO part B ends on the card
    sB = max(sA + len(voA) + int(0.35*SR), int(sB_end - len(voB)/SR * SR))
    sB = int(sB)
    segA = voA[: max(0, n - sA)]
    vo[sA : sA + len(segA)] += segA
    if sB < n:
        segB = voB[: n - sB]
        vo[sB : sB + len(segB)] += segB
    mus = music_bed(total)
    env = moving_avg(np.abs(vo), int(0.35 * SR))
    env = np.clip(env / (env.max() + 1e-6), 0, 1)
    duck = 1.0 - 0.55 * env
    mix = vo * 1.0 + mus * duck * 2.2
    # master fades
    fo = int(1.2 * SR); mix[-fo:] *= np.linspace(1, 0, fo)
    fi = int(0.3 * SR); mix[:fi]  *= np.linspace(0, 1, fi)
    # gentle limiter
    mix = np.tanh(mix * 1.1) / 1.1
    write_wav(f"{AD}/audio.wav", mix)
    print("  audio.wav", round(len(mix)/SR, 2), "s  (VO A @3.2s, VO B ends @%.1fs)" % (sB/SR + len(voB)/SR))

# ------------------------------------------------------------------ run
if __name__ == "__main__":
    _names = ["hero", "couple", "store", "coffee", "lane", "sign", "card"]
    IMG = {f"s{i+1}": load(f"{AD}/shot{i+1}_{_names[i]}.png") for i in range(7)}
    voA_len = len(read_audio(f"{AD}/voA.mp3")) / SR
    voB_len = len(read_audio(f"{AD}/voB.mp3")) / SR
    total_target = 3.2 + voA_len + 0.4 + voB_len + 8.0
    T = {"s1": 0.215, "s2": 0.14, "s3": 0.14, "s4": 0.12, "s5": 0.135, "s6": 0.155}
    T = {k: max(v * total_target, 3.6) for k, v in T.items()}
    T["s6"] = max(T["s6"], 5.0)
    T["s7"] = max(total_target - sum(T.values()), 4.0)
    print("Timeline:", {k: round(v,1) for k,v in T.items()})
    total = render_all(T)
    build_audio(total, T)
    s7_start = total - T["s7"]
    tag = f"Fueling Your Journey"
    vf = (f"drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:"
          f"text='{tag}':fontcolor=white:fontsize=44:borderw=2:bordercolor=black@0.5:"
          f"shadowx=3:shadowy=3:shadowcolor=black@0.6:"
          f"x=(w-text_w)/2:y=h*0.80:"
          f"alpha='if(lt(t,{s7_start}),0,if(lt(t,{s7_start+0.9}),(t-{s7_start})/0.9,1))':"
          f"enable='gte(t,{s7_start})'")
    subprocess.run([FF, "-y", "-i", f"{AD}/video_silent.mp4", "-i", f"{AD}/audio.wav",
                    "-vf", vf, "-c:v", "libx264", "-preset", "medium", "-crf", "17",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                    "-movflags", "+faststart", f"{AD}/harrys_commercial.mp4"],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("DONE -> harrys_commercial.mp4", round(total,1), "s")

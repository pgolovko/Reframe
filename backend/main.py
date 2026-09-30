import asyncio
import os
import re
import uuid
import subprocess
import json
import base64
import math
import shutil
from fastapi import FastAPI, UploadFile, Form, File, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from typing import Dict, List, Optional
from PIL import Image, ImageDraw, ImageFont
import threading
try:
    from faster_whisper import WhisperModel
except ImportError:
    WhisperModel = None

app = FastAPI(title="Reframe API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Paths
UPLOAD_DIR = "/app/uploads" if os.environ.get("DOCKER") else os.path.join(os.getcwd(), "uploads")
EXPORT_DIR = "/app/exports" if os.environ.get("DOCKER") else os.path.join(os.getcwd(), "exports")
FONTS_DIR = "/app/fonts" if os.environ.get("DOCKER") else os.path.join(os.getcwd(), "fonts")
MODELS_DIR = "/app/models" if os.environ.get("DOCKER") else os.path.join(os.getcwd(), "models")
FRONTEND_DIR = "/app/frontend" if os.environ.get("DOCKER") else os.path.join(os.path.dirname(os.getcwd()), "frontend", "dist")

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(EXPORT_DIR, exist_ok=True)
os.makedirs(FONTS_DIR, exist_ok=True)
os.makedirs(MODELS_DIR, exist_ok=True)

# State stores
job_progress: Dict[str, int] = {}
job_status: Dict[str, str] = {}
active_connections: Dict[str, list[WebSocket]] = {}

def get_video_duration(filepath: str) -> float:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "format=duration", "-of",
             "default=noprint_wrappers=1:nokey=1", filepath],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )
        return float(result.stdout.strip())
    except Exception:
        return 0.0

def get_video_dimensions(filepath: str) -> tuple[int, int]:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of",
             "csv=p=0", filepath],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )
        w, h = result.stdout.strip().split(',')
        return int(w), int(h)
    except Exception:
        return 1920, 1080

def get_video_metadata(filepath: str) -> dict:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height,r_frame_rate,duration", "-of",
             "json", filepath],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )
        data = json.loads(result.stdout)
        stream = data.get("streams", [{}])[0]
        
        width = stream.get("width", 0)
        height = stream.get("height", 0)
        duration_str = stream.get("duration")
        duration = float(duration_str) if duration_str else 0.0
        
        r_frame_rate = stream.get("r_frame_rate", "0/1")
        if "/" in r_frame_rate:
            num, den = r_frame_rate.split("/")
            fps = float(num) / float(den) if float(den) != 0 else 0
        else:
            fps = float(r_frame_rate)
            
        return {
            "width": width,
            "height": height,
            "duration": duration,
            "fps": round(fps, 2)
        }
    except Exception as e:
        print(f"Error getting metadata for {filepath}: {e}")
        return {"width": 0, "height": 0, "duration": 0.0, "fps": 0.0}

def parse_ass_transcript(filepath: str) -> str:
    try:
        if not os.path.exists(filepath):
            return ""
        with open(filepath, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        transcript = []
        for line in lines:
            if line.startswith("Dialogue:"):
                parts = line.split(",", 9)
                if len(parts) >= 10:
                    text = parts[9].strip()
                    text = re.sub(r'\{[^}]+\}', '', text)
                    if text:
                        transcript.append(text)
        deduped = []
        for t in transcript:
            if not deduped or t != deduped[-1]:
                deduped.append(t)
        return " ".join(deduped)
    except Exception:
        return ""

def hex_to_ass_color(hex_str: str) -> str:
    hex_str = hex_str.lstrip('#')
    if len(hex_str) == 6:
        return f"&H00{hex_str[4:6]}{hex_str[2:4]}{hex_str[0:2]}&"
    return "&H00FFFFFF&"

def get_font_family_name(font_path: str, default_name: str) -> str:
    try:
        result = subprocess.run(
            ["fc-query", "-f", "%{family}\\n", font_path],
            capture_output=True, text=True, check=True
        )
        family_names = result.stdout.strip()
        if family_names:
            return family_names.split(",")[0]
    except Exception:
        pass
    return default_name

def generate_tiktok_ass(
    words: list, output_ass: str, cw: int, ch: int,
    x: int, y: int, w: int, h: int, font_name: str, font_size: int,
    base_color: str, highlight_color: str, stroke_color: str
):
    ass_base_color = hex_to_ass_color(base_color)
    ass_high_color = hex_to_ass_color(highlight_color)
    ass_stroke_color = hex_to_ass_color(stroke_color)
    
    outline_size = max(1, int(font_size * 0.05))
    shadow_size = max(1, int(font_size * 0.05))
    
    ass_content = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {cw}",
        f"PlayResY: {ch}",
        "WrapStyle: 1",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,{font_name},{font_size},{ass_base_color},{ass_high_color},{ass_stroke_color},&H00000000&,-1,0,0,0,100,100,0,0,1,{outline_size},{shadow_size},5,0,0,0,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"
    ]
    
    center_x = x + w // 2
    center_y = y + h // 2
    
    def format_time(seconds: float) -> str:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = seconds % 60
        return f"{h:01d}:{m:02d}:{s:05.2f}"
    
    chunk_size = 3
    for i in range(0, len(words), chunk_size):
        chunk = words[i:i+chunk_size]
        if not chunk: continue
        
        chunk_start = chunk[0].start
        chunk_end = chunk[-1].end
        
        for j, active_word in enumerate(chunk):
            start_t = active_word.start
            end_t = active_word.end if j < len(chunk)-1 else chunk_end
            
            text_parts = []
            for k, w_obj in enumerate(chunk):
                word_text = w_obj.word.strip()
                if k == j:
                    text_parts.append(f"{{\\c{ass_high_color}}}{word_text}{{\\c{ass_base_color}}}")
                else:
                    text_parts.append(word_text)
            
            full_text = f"{{\\pos({center_x},{center_y})}}" + " ".join(text_parts)
            ass_content.append(f"Dialogue: 0,{format_time(start_t)},{format_time(end_t)},Default,,0,0,0,,{full_text}")
            
    with open(output_ass, 'w', encoding='utf-8') as f:
        f.write("\n".join(ass_content))

import contextlib
import io

class CallbackStream(io.StringIO):
    def __init__(self, callback):
        super().__init__()
        self.callback = callback
    def write(self, s):
        if s.strip():
            self.callback(s)
        return super().write(s)

def transcribe_audio(input_file: str, model_size: str, log_callback=None):
    if not WhisperModel: return []
    
    import av
    try:
        with av.open(input_file) as container:
            if not any(s.type == 'audio' for s in container.streams):
                return []
    except Exception:
        return []
        
    def run_model():
        if log_callback:
            log_callback(f"Downloading Model: {model_size}...\n")
        model = WhisperModel(model_size, device="cpu", compute_type="int8", download_root=MODELS_DIR)
        if log_callback:
            log_callback(f"Transcribing audio...\n")
        segments, _ = model.transcribe(input_file, word_timestamps=True)
        words = []
        for segment in segments:
            for word in segment.words:
                words.append(word)
        return words

    if log_callback:
        stream = CallbackStream(log_callback)
        with contextlib.redirect_stderr(stream):
            return run_model()
    return run_model()

def create_text_image(t_item: dict, output_path: str, cw: int) -> tuple[int, int]:
    try:
        from PIL import Image, ImageDraw, ImageFont, ImageFilter
        content = t_item.get("content", "")
        color = t_item.get("color", "#ffffff")
        shadow = t_item.get("shadow", True)
        
        rel_scale = t_item.get("relativeScale", 0)
        if rel_scale <= 0:
            rel_scale = 1.0 / 800.0
            
        font_size = int(24 * rel_scale * cw)
        if font_size < 10: font_size = 10
        
        img = Image.new('RGBA', (4000, 4000), (255, 255, 255, 0))
        d = ImageDraw.Draw(img)
        
        font_name = t_item.get("font", "Arial")
        font_map = {
            "Fira Code": "/usr/share/fonts/truetype/firacode/FiraCode-Regular.ttf",
            "Arial": "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "Verdana": "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "Tahoma": "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "Inter": "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"
        }
        
        custom_font_path = None
        if os.path.exists(FONTS_DIR):
            for f in os.listdir(FONTS_DIR):
                if os.path.splitext(f)[0] == font_name:
                    custom_font_path = os.path.join(FONTS_DIR, f)
                    break
        
        if custom_font_path and os.path.exists(custom_font_path):
            font_file = custom_font_path
        else:
            font_file = font_map.get(font_name, "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf")
        
        try:
            font = ImageFont.truetype(font_file, font_size)
        except:
            try:
                font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", font_size)
            except:
                font = ImageFont.load_default()
                
        lines = content.split('\n')
        padding_x = int(font_size * 0.4)
        padding_y = int(font_size * 0.4)
        y_offset = padding_y
        x_offset = padding_x
        
        if shadow:
            shadow_img = Image.new('RGBA', (4000, 4000), (0, 0, 0, 0))
            d_shadow = ImageDraw.Draw(shadow_img)
            sy_offset = y_offset
            for line in lines:
                d_shadow.text((x_offset, sy_offset), line, font=font, fill=(0,0,0,255))
                sy_offset += int(font_size * 1.2)
            
            blur_radius = max(3, int(font_size * 0.12))
            shadow_img = shadow_img.filter(ImageFilter.GaussianBlur(radius=blur_radius))
            
            shadow_offset_x = max(2, int(font_size*0.06))
            shadow_offset_y = max(2, int(font_size*0.06))
            for _ in range(4):
                img.alpha_composite(shadow_img, (shadow_offset_x, shadow_offset_y))

        for line in lines:
            d.text((x_offset, y_offset), line, font=font, fill=color)
            y_offset += int(font_size * 1.2)
            
        bbox = img.getbbox()
        if bbox:
            img = img.crop((0, 0, bbox[2] + padding_x, bbox[3] + padding_y))
        else:
            img = Image.new('RGBA', (10, 10), (255, 255, 255, 0))
            
        img.save(output_path)
        return img.width, img.height
    except Exception as e:
        print(f"Error creating text image: {e}")
        img = Image.new('RGBA', (10, 10), (255, 255, 255, 0))
        img.save(output_path)
        return 10, 10

async def process_video_ffmpeg(
    job_id: str, 
    input_path: str, 
    output_path: str, 
    x: int, y: int, w: int, h: int, 
    quality: str, 
    muteAudio: str,
    useGpu: str = "false",
    outputFormat: str = "mp4",
    trimStart: float = None,
    trimEnd: float = None,
    logo_paths: List[str] = None,
    logoXs: List[int] = None,
    logoYs: List[int] = None,
    logoWs: List[int] = None,
    logoHs: List[int] = None,
    logoRotations: List[float] = None,
    logoOpacities: List[int] = None,
    text_paths: List[str] = None,
    textXs: List[int] = None,
    textYs: List[int] = None,
    textWs: List[int] = None,
    textHs: List[int] = None,
    textRotations: List[float] = None,
    subtitle_ass_path: str = None
):
    logo_paths = logo_paths or []
    logoXs = logoXs or []
    logoYs = logoYs or []
    logoWs = logoWs or []
    logoHs = logoHs or []
    logoRotations = logoRotations or []
    logoOpacities = logoOpacities or []
    
    text_paths = text_paths or []
    textXs = textXs or []
    textYs = textYs or []
    textWs = textWs or []
    textHs = textHs or []
    textRotations = textRotations or []
    duration = get_video_duration(input_path)
    if trimStart is not None and trimEnd is not None:
        duration = trimEnd - trimStart
        
    job_status[job_id] = "processing"
    job_progress[job_id] = 0
    
    is_gif = outputFormat == "gif"
    if is_gif:
        useGpu = "false"
    
    # Quality settings
    if useGpu == "true":
        qp = "20" if quality == "high" else "24"
    else:
        crf = "18" if quality == "high" else "23"
        preset = "slow" if quality == "high" else "fast"
    
    cmd = ["ffmpeg", "-y"]
    
    if useGpu == "true":
        cmd.extend(["-vaapi_device", "/dev/dri/renderD128"])
        
    if trimStart is not None and trimStart > 0:
        cmd.extend(["-ss", str(trimStart)])
    if trimEnd is not None and trimEnd > 0:
        cmd.extend(["-t", str(trimEnd)])
        
    cmd.extend(["-i", input_path])
    
    # Ensure dimensions are even for libx264
    w = w - (w % 2)
    h = h - (h % 2)
    x = max(0, x)
    y = max(0, y)
    filters = [f"[0:v]crop={w}:{h}:{x}:{y}[bg]"]
    current_bg = "[bg]"
    input_idx = 1
    
    for i, logo_path in enumerate(logo_paths):
        cmd.extend(["-i", logo_path])
        rotation = logoRotations[i] if i < len(logoRotations) and logoRotations[i] is not None else 0
        rotation_expr = f"{rotation}*PI/180"
        
        opacity = logoOpacities[i] if i < len(logoOpacities) and logoOpacities[i] is not None else 100
        opacity_val = opacity / 100.0
        opacity_filter = f",format=rgba,colorchannelmixer=aa={opacity_val}" if opacity_val < 1.0 else ""
        
        lW = logoWs[i] if i < len(logoWs) else 150
        lH = logoHs[i] if i < len(logoHs) else None
        lX = logoXs[i] if i < len(logoXs) else 0
        lY = logoYs[i] if i < len(logoYs) else 0
        
        if lH is not None:
            rotate_filter = f"rotate={rotation_expr}:c=none:ow='hypot(iw,ih)':oh='hypot(iw,ih)'"
            overlay_expr = f"{lX}+{lW}/2-w/2:{lY}+{lH}/2-h/2"
        else:
            rotate_filter = f"rotate={rotation_expr}:c=none"
            overlay_expr = f"{lX}:{lY}"
            
        filters.append(f"[{input_idx}:v]scale={lW}:-1{opacity_filter}[l_scaled_{i}]")
        filters.append(f"[l_scaled_{i}]{rotate_filter}[l_rotated_{i}]")
        filters.append(f"{current_bg}[l_rotated_{i}]overlay={overlay_expr}[bg_with_logo_{i}]")
        current_bg = f"[bg_with_logo_{i}]"
        input_idx += 1
        
    for i, text_path in enumerate(text_paths):
        cmd.extend(["-i", text_path])
        rotation = textRotations[i] if i < len(textRotations) and textRotations[i] is not None else 0
        rotation_expr = f"{rotation}*PI/180"
        
        tW = textWs[i] if i < len(textWs) else 100
        tH = textHs[i] if i < len(textHs) else None
        tX = textXs[i] if i < len(textXs) else 0
        tY = textYs[i] if i < len(textYs) else 0
        
        if tH is not None:
            rotate_filter = f"rotate={rotation_expr}:c=none:ow='hypot(iw,ih)':oh='hypot(iw,ih)'"
            overlay_expr = f"{tX}+{tW}/2-w/2:{tY}+{tH}/2-h/2"
        else:
            rotate_filter = f"rotate={rotation_expr}:c=none"
            overlay_expr = f"{tX}:{tY}"
            
        filters.append(f"[{input_idx}:v]scale={tW}:-1[t_scaled_{i}]")
        filters.append(f"[t_scaled_{i}]{rotate_filter}[t_rotated_{i}]")
        filters.append(f"{current_bg}[t_rotated_{i}]overlay={overlay_expr}[bg_with_text_{i}]")
        current_bg = f"[bg_with_text_{i}]"
        input_idx += 1
        
    # Palette-based gif encoding for smaller, higher quality gifs
    gif_filters = "mpdecimate,fps=10,scale=480:-1:flags=lanczos,split[gif_s0][gif_s1];[gif_s0]palettegen=max_colors=128:stats_mode=diff[gif_p];[gif_s1][gif_p]paletteuse=dither=floyd_steinberg:diff_mode=rectangle[gifout]"

    if current_bg != "[bg]":
        if subtitle_ass_path and os.path.exists(subtitle_ass_path):
            filters.append(f"{current_bg}ass='{subtitle_ass_path}':fontsdir='{FONTS_DIR}'[hw_with_subs]")
            current_bg = "[hw_with_subs]"

        if is_gif:
            filters.append(f"{current_bg}{gif_filters}")
        elif useGpu == "true":
            filters.append(f"{current_bg}format=nv12,hwupload[hw]")
            current_bg = "[hw]"
            
        filter_str = "; ".join(filters)
        if is_gif:
            cmd.extend(["-filter_complex", filter_str, "-map", "[gifout]"])
        else:
            cmd.extend(["-filter_complex", filter_str, "-map", current_bg])
            if muteAudio != "true":
                cmd.extend(["-map", "0:a?"])
    else:
        if is_gif:
            base_vf = f"[0:v]crop={w}:{h}:{x}:{y}"
            if subtitle_ass_path and os.path.exists(subtitle_ass_path):
                base_vf += f",ass='{subtitle_ass_path}':fontsdir='{FONTS_DIR}'"
            cmd.extend(["-filter_complex", f"{base_vf},{gif_filters}", "-map", "[gifout]"])
        elif subtitle_ass_path and os.path.exists(subtitle_ass_path):
            if useGpu == "true":
                cmd.extend(["-vf", f"crop={w}:{h}:{x}:{y},ass='{subtitle_ass_path}':fontsdir='{FONTS_DIR}',format=nv12,hwupload"])
            else:
                cmd.extend(["-vf", f"crop={w}:{h}:{x}:{y},ass='{subtitle_ass_path}':fontsdir='{FONTS_DIR}'"])
        else:
            if useGpu == "true":
                cmd.extend(["-vf", f"crop={w}:{h}:{x}:{y},format=nv12,hwupload"])
            else:
                cmd.extend(["-vf", f"crop={w}:{h}:{x}:{y}"])
        
    if is_gif:
        cmd.extend(["-loop", "0", "-an"])
    elif useGpu == "true":
        cmd.extend(["-c:v", "h264_vaapi", "-qp", qp])
    else:
        cmd.extend(["-c:v", "libx264", "-crf", crf, "-preset", preset])
    
    if not is_gif:
        if muteAudio == "true":
            cmd.append("-an")
        else:
            cmd.extend(["-c:a", "copy"])
        
    cmd.append(output_path)
    
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    
    time_regex = re.compile(r"time=(\d{2}):(\d{2}):(\d{2}\.\d{2})")
    
    stderr_log = []
    while True:
        line = await process.stderr.readline()
        if not line:
            break
        
        line_str = line.decode('utf-8')
        stderr_log.append(line_str)
        if job_id in active_connections:
            for ws in active_connections[job_id]:
                try:
                    await ws.send_json({"progress": 0, "status": "processing", "log": line_str})
                except:
                    pass
        match = time_regex.search(line_str)
        if match and duration > 0:
            hrs, mins, secs = match.groups()
            current_time = int(hrs)*3600 + int(mins)*60 + float(secs)
            progress = int((current_time / duration) * 100)
            progress = min(99, progress)
            job_progress[job_id] = progress
            
            if job_id in active_connections:
                for ws in active_connections[job_id]:
                    try:
                        await ws.send_json({"progress": progress, "status": "processing"})
                    except:
                        pass
                        
    await process.wait()
    
    if process.returncode == 0:
        job_status[job_id] = "completed"
        job_progress[job_id] = 100
        if job_id in active_connections:
            for ws in active_connections[job_id]:
                try:
                    await ws.send_json({"progress": 100, "status": "completed"})
                except:
                    pass
    else:
        full_err = "".join(stderr_log)
        print(f"FFMPEG ERROR:\n{full_err}", flush=True)
        job_status[job_id] = "error"
        if job_id in active_connections:
            for ws in active_connections[job_id]:
                try:
                    await ws.send_json({"progress": 0, "status": "error", "detail": "FFmpeg failed"})
                except:
                    pass

async def process_video_pipeline(
    job_id: str, input_path: str, output_path: str, 
    x: int, y: int, w: int, h: int, quality: str, muteAudio: str, useGpu: str, outputFormat: str,
    trimStart: float, trimEnd: float, logo_paths: list, logoXs: list, logoYs: list, 
    logoWs: list, logoHs: list, logoRotations: list, logoOpacities: list,
    text_paths: list, textXs: list, textYs: list, textWs: list, textHs: list, textRotations: list,
    subtitleEnabled: str, subtitleModel: str, subtitleFont: str, subtitleFontSize: int, subtitleColor: str, 
    subtitleHighlight: str, subtitleStroke: str, subtitleX: int, subtitleY: int, 
    subtitleW: int, subtitleH: int
):
    try:
        subtitle_ass_path = None
        if subtitleEnabled == "true":
            job_status[job_id] = "transcribing"
            if job_id in active_connections:
                for ws in active_connections[job_id]:
                    try: await ws.send_json({"progress": 0, "status": "transcribing"})
                    except: pass
                    
            loop = asyncio.get_running_loop()
            def sync_log(msg):
                if job_id in active_connections:
                    for ws in active_connections[job_id]:
                        asyncio.run_coroutine_threadsafe(
                            ws.send_json({"progress": 0, "status": "transcribing", "log": msg}),
                            loop
                        )

            words = await asyncio.to_thread(transcribe_audio, input_path, subtitleModel, sync_log)
            
            if words:
                subtitle_ass_path = os.path.join(UPLOAD_DIR, f"{job_id}_subs.ass")
                
                ass_font_name = subtitleFont
                
                possible_paths = [
                    os.path.join(FONTS_DIR, f"{subtitleFont}.ttf"),
                    os.path.join(FONTS_DIR, f"{subtitleFont}.otf"),
                    os.path.join(FONTS_DIR, subtitleFont)
                ]
                for font_path in possible_paths:
                    if os.path.exists(font_path):
                        ass_font_name = get_font_family_name(font_path, subtitleFont)
                        break
                
                generate_tiktok_ass(
                    words, subtitle_ass_path, w, h,
                    subtitleX, subtitleY, subtitleW, subtitleH,
                    ass_font_name, subtitleFontSize, subtitleColor, subtitleHighlight, subtitleStroke
                )
                
        await process_video_ffmpeg(
            job_id, input_path, output_path, x, y, w, h, quality, muteAudio, useGpu, outputFormat,
            trimStart, trimEnd, logo_paths, logoXs, logoYs, logoWs, logoHs, logoRotations, logoOpacities,
            text_paths, textXs, textYs, textWs, textHs, textRotations, subtitle_ass_path
        )
    except Exception as e:
        import traceback
        print(f"Pipeline error: {e}", flush=True)
        traceback.print_exc()
        job_status[job_id] = "error"
        if job_id in active_connections:
            for ws in active_connections[job_id]:
                try: await ws.send_json({"progress": 0, "status": "error", "detail": str(e)})
                except: pass

@app.post("/api/process")
async def process_video(
    file: UploadFile = Form(...),
    x: int = Form(...),
    y: int = Form(...),
    width: int = Form(...),
    height: int = Form(...),
    quality: str = Form("high"),
    muteAudio: str = Form("false"),
    useGpu: str = Form("false"),
    outputFormat: str = Form("mp4"),
    trimStart: float = Form(None),
    trimEnd: float = Form(None),
    logoFiles: List[UploadFile] = File([]),
    logoXs: List[int] = Form([]),
    logoYs: List[int] = Form([]),
    logoWs: List[int] = Form([]),
    logoHs: List[int] = Form([]),
    logoRotations: List[float] = Form([]),
    logoOpacities: List[int] = Form([]),
    textFiles: List[UploadFile] = File([]),
    textXs: List[int] = Form([]),
    textYs: List[int] = Form([]),
    textWs: List[int] = Form([]),
    textHs: List[int] = Form([]),
    textRotations: List[float] = Form([]),
    subtitleEnabled: str = Form("false"),
    subtitleModel: str = Form("base"),
    subtitleFont: str = Form("Arial"),
    subtitleFontSize: int = Form(40),
    subtitleColor: str = Form("#ffffff"),
    subtitleHighlight: str = Form("#ffff00"),
    subtitleStroke: str = Form("#000000"),
    subtitleX: int = Form(0),
    subtitleY: int = Form(0),
    subtitleW: int = Form(0),
    subtitleH: int = Form(0),
    customFilename: str = Form(None)
):
    job_id = str(uuid.uuid4())
    ext = os.path.splitext(file.filename)[1] or ".mp4" if file.filename else ".mp4"
    input_filename = f"{job_id}_in{ext}"
    input_path = os.path.join(UPLOAD_DIR, input_filename)
    
    content = await file.read()
    with open(input_path, "wb") as f:
        f.write(content)
        
    logo_paths = []
    if logoFiles:
        for i, lf in enumerate(logoFiles):
            if not lf or not lf.filename: continue
            logo_ext = os.path.splitext(lf.filename)[1] or ".png"
            logo_filename = f"{job_id}_logo_{i}{logo_ext}"
            logo_path = os.path.join(UPLOAD_DIR, logo_filename)
            logo_content = await lf.read()
            with open(logo_path, "wb") as f:
                f.write(logo_content)
            logo_paths.append(logo_path)
            
    text_paths = []
    if textFiles:
        for i, tf in enumerate(textFiles):
            if not tf or not tf.filename: continue
            text_ext = os.path.splitext(tf.filename)[1] or ".png"
            text_filename = f"{job_id}_text_{i}{text_ext}"
            text_path = os.path.join(UPLOAD_DIR, text_filename)
            text_content = await tf.read()
            with open(text_path, "wb") as f:
                f.write(text_content)
            text_paths.append(text_path)
        
    out_ext = ".gif" if outputFormat == "gif" else ".mp4"
    if customFilename:
        safe_name = "".join(c for c in customFilename if c.isalnum() or c in " .-_()")
        for known_ext in (".mp4", ".gif"):
            if safe_name.lower().endswith(known_ext):
                safe_name = safe_name[:-len(known_ext)]
                break
        safe_name += out_ext
            
        output_filename = f"{job_id}_{safe_name}"
    else:
        output_filename = f"{job_id}_out{out_ext}"
        
    output_path = os.path.join(EXPORT_DIR, output_filename)
    
    asyncio.create_task(process_video_pipeline(
        job_id, input_path, output_path, 
        x, y, width, height, quality, muteAudio, useGpu, outputFormat,
        trimStart, trimEnd, logo_paths, logoXs, logoYs, logoWs, logoHs, logoRotations, logoOpacities,
        text_paths, textXs, textYs, textWs, textHs, textRotations,
        subtitleEnabled, subtitleModel, subtitleFont, subtitleFontSize, subtitleColor, subtitleHighlight,
        subtitleStroke, subtitleX, subtitleY, subtitleW, subtitleH
    ))
    
    return {"job_id": job_id}

@app.post("/api/automate")
async def automate_process(
    video: UploadFile = File(...),
    config: UploadFile = File(...)
):
    job_id = str(uuid.uuid4())
    ext = os.path.splitext(video.filename)[1] or ".mp4" if video.filename else ".mp4"
    input_filename = f"{job_id}_in{ext}"
    input_path = os.path.join(UPLOAD_DIR, input_filename)
    
    # Save video
    content = await video.read()
    with open(input_path, "wb") as f:
        f.write(content)
        
    # Read config
    config_content = await config.read()
    settings = json.loads(config_content.decode('utf-8'))
    
    vw, vh = get_video_dimensions(input_path)
    vr = vw / vh
    
    preset = settings.get("selectedPreset", {})
    if preset.get("id") == "custom":
        tr = settings.get("customRatioW", 16) / settings.get("customRatioH", 9)
    else:
        tr = preset.get("ratio", 9/16)
        
    if tr > vr:
        cw = vw
        ch = vw / tr
    else:
        ch = vh
        cw = vh * tr
        
    cw = int(cw)
    ch = int(ch)
    x = int((vw - cw) / 2)
    y = int((vh - ch) / 2)
    
    logo_paths, logoXs, logoYs, logoWs, logoHs, logoRotations, logoOpacities = [], [], [], [], [], [], []
    for i, w in enumerate(settings.get("watermarks", [])):
        if "fileBase64" in w and w["fileBase64"]:
            b64 = w["fileBase64"].split(",")[1] if "," in w["fileBase64"] else w["fileBase64"]
            img_data = base64.b64decode(b64)
            logo_ext = ".png"
            if w.get("fileMime") == "image/jpeg": logo_ext = ".jpg"
            elif w.get("fileMime") == "image/svg+xml": logo_ext = ".svg"
            
            logo_path = os.path.join(UPLOAD_DIR, f"{job_id}_logo_{i}{logo_ext}")
            with open(logo_path, "wb") as f:
                f.write(img_data)
                
            try:
                with Image.open(logo_path) as im:
                    im_w, im_h = im.size
                logo_w = int(150 * w.get("relativeScale", 0) * cw)
                logo_h = int(logo_w * (im_h / im_w)) if im_w > 0 else logo_w
            except:
                logo_w = int(150 * w.get("relativeScale", 0) * cw)
                logo_h = None
                
            logo_paths.append(logo_path)
            logoWs.append(logo_w)
            logoHs.append(logo_h)
            logoXs.append(int(w.get("relativeX", 0) * cw))
            logoYs.append(int(w.get("relativeY", 0) * ch))
            logoRotations.append(w.get("rotation", 0))
            logoOpacities.append(w.get("opacity", 100))
            
    text_paths, textXs, textYs, textWs, textHs, textRotations = [], [], [], [], [], []
    for i, t in enumerate(settings.get("texts", [])):
        if t.get("content"):
            text_path = os.path.join(UPLOAD_DIR, f"{job_id}_text_{i}.png")
            img_w, img_h = create_text_image(t, text_path, cw)
            text_paths.append(text_path)
            textWs.append(img_w)
            textHs.append(img_h)
            textXs.append(int(t.get("relativeX", 0) * cw))
            textYs.append(int(t.get("relativeY", 0) * ch))
            textRotations.append(t.get("rotation", 0))
            
    outputFormat = settings.get("outputFormat", "mp4")
    out_ext = ".gif" if outputFormat == "gif" else ".mp4"
    customFilename = settings.get("customFilename")
    if customFilename:
        safe_name = "".join(c for c in customFilename if c.isalnum() or c in " .-_()")
        for known_ext in (".mp4", ".gif"):
            if safe_name.lower().endswith(known_ext):
                safe_name = safe_name[:-len(known_ext)]
                break
        safe_name += out_ext
            
        output_filename = f"{job_id}_{safe_name}"
    else:
        output_filename = f"{job_id}_out{out_ext}"
        
    output_path = os.path.join(EXPORT_DIR, output_filename)
    quality = settings.get("quality", "high")
    muteAudio = "true" if settings.get("muteAudio") else "false"
    useGpu = "true" if settings.get("useGpu") else "false"
    trimStart = settings.get("trimStart")
    trimEnd = settings.get("trimEnd")
    
    await process_video_ffmpeg(
        job_id, input_path, output_path,
        x, y, cw, ch, quality, muteAudio, useGpu, outputFormat,
        trimStart, trimEnd, logo_paths, logoXs, logoYs, logoWs, logoHs, logoRotations, logoOpacities,
        text_paths, textXs, textYs, textWs, textHs, textRotations
    )
    
    if os.path.exists(output_path):
        media_type = "image/gif" if out_ext == ".gif" else "video/mp4"
        video_base = os.path.splitext(video.filename or "video")[0]
        return FileResponse(output_path, media_type=media_type, filename=f"automated_{video_base}{out_ext}")
    return JSONResponse(status_code=500, content={"detail": "Video processing failed"})

@app.websocket("/ws/progress/{job_id}")
async def progress_ws(websocket: WebSocket, job_id: str):
    await websocket.accept()
    if job_id not in active_connections:
        active_connections[job_id] = []
    active_connections[job_id].append(websocket)
    
    try:
        if job_id in job_progress:
            await websocket.send_json({
                "progress": job_progress.get(job_id, 0),
                "status": job_status.get(job_id, "processing")
            })
        
        while True:
            await asyncio.sleep(1)
            status = job_status.get(job_id)
            if status in ["completed", "error"]:
                break
    except WebSocketDisconnect:
        if websocket in active_connections.get(job_id, []):
            active_connections[job_id].remove(websocket)

@app.get("/api/download/{job_id}")
async def download_video(job_id: str, filename: str = None):
    output_filename = None
    
    for f in os.listdir(EXPORT_DIR):
        if f.startswith(f"{job_id}_") and f.lower().endswith(('.mp4', '.gif')):
            output_filename = f
            break
                    
    if output_filename and os.path.exists(os.path.join(EXPORT_DIR, output_filename)):
        media_type = "image/gif" if output_filename.lower().endswith(".gif") else "video/mp4"
        return FileResponse(os.path.join(EXPORT_DIR, output_filename), media_type=media_type, filename=filename or output_filename)
    return JSONResponse(status_code=404, content={"detail": "File not found"})

@app.get("/api/media")
async def list_media():
    uploads = []
    if os.path.exists(UPLOAD_DIR):
        for f in os.listdir(UPLOAD_DIR):
            path = os.path.join(UPLOAD_DIR, f)
            if os.path.isfile(path):
                uploads.append({
                    "filename": f,
                    "size": os.path.getsize(path),
                    "created": os.path.getctime(path)
                })
    
    exports = []
    if os.path.exists(EXPORT_DIR):
        for f in os.listdir(EXPORT_DIR):
            path = os.path.join(EXPORT_DIR, f)
            if os.path.isfile(path):
                metadata = {}
                transcript = ""
                if f.lower().endswith(('.mp4', '.mov', '.webm', '.avi', '.gif')):
                    metadata = get_video_metadata(path)
                    job_id = None
                    if "_" in f:
                        job_id = f.split("_", 1)[0]

                    if job_id:
                        ass_path = os.path.join(UPLOAD_DIR, f"{job_id}_subs.ass")
                        transcript = parse_ass_transcript(ass_path)
                        
                exports.append({
                    "filename": f,
                    "size": os.path.getsize(path),
                    "created": os.path.getctime(path),
                    "metadata": metadata,
                    "transcript": transcript
                })
                
    # Sort descending by creation time
    uploads.sort(key=lambda x: x["created"], reverse=True)
    exports.sort(key=lambda x: x["created"], reverse=True)
    
    return {"uploads": uploads, "exports": exports}

@app.delete("/api/media/{folder}/{filename}")
async def delete_media(folder: str, filename: str):
    if folder == "uploads":
        file_path = os.path.join(UPLOAD_DIR, filename)
    elif folder == "exports":
        file_path = os.path.join(EXPORT_DIR, filename)
    else:
        return JSONResponse(status_code=400, content={"detail": "Invalid folder"})
        
    if os.path.exists(file_path):
        try:
            os.remove(file_path)
            return {"status": "success"}
        except Exception as e:
            return JSONResponse(status_code=500, content={"detail": f"Failed to delete file: {str(e)}"})
    return JSONResponse(status_code=404, content={"detail": "File not found"})

@app.delete("/api/media/{folder}")
async def delete_all_media(folder: str):
    if folder == "uploads":
        target_dir = UPLOAD_DIR
    elif folder == "exports":
        target_dir = EXPORT_DIR
    else:
        return JSONResponse(status_code=400, content={"detail": "Invalid folder"})
        
    if os.path.exists(target_dir):
        try:
            for f in os.listdir(target_dir):
                file_path = os.path.join(target_dir, f)
                if os.path.isfile(file_path):
                    os.remove(file_path)
            return {"status": "success"}
        except Exception as e:
            return JSONResponse(status_code=500, content={"detail": f"Failed to delete files: {str(e)}"})
    return JSONResponse(status_code=404, content={"detail": "Folder not found"})

@app.get("/api/fonts")
async def list_fonts():
    fonts = ["Fira Code", "Arial", "Verdana", "Tahoma", "Inter"]
    custom_fonts = []
    if os.path.exists(FONTS_DIR):
        for f in os.listdir(FONTS_DIR):
            if f.lower().endswith(('.ttf', '.otf')):
                font_name = os.path.splitext(f)[0]
                if font_name not in fonts:
                    custom_fonts.append(font_name)
    
    return {"static": fonts, "custom": custom_fonts}

@app.post("/api/fonts")
async def upload_font(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(('.ttf', '.otf')):
        return JSONResponse(status_code=400, content={"detail": "Only .ttf and .otf files are allowed"})
        
    font_path = os.path.join(FONTS_DIR, file.filename)
    content = await file.read()
    with open(font_path, "wb") as f:
        f.write(content)
        
    return {"status": "success", "font": os.path.splitext(file.filename)[0]}

@app.get("/api/whisper/models")
async def get_whisper_models():
    return {
        "models": ["tiny", "tiny.en", "base", "base.en", "small", "small.en", "medium", "medium.en", "large-v3"]
    }

@app.get("/api/fonts/{font_name}/file")
async def get_font_file(font_name: str):
    font_map = {
        "Fira Code": "/usr/share/fonts/truetype/firacode/FiraCode-Regular.ttf",
        "Arial": "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "Verdana": "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "Tahoma": "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "Inter": "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"
    }
    
    if os.path.exists(FONTS_DIR):
        for f in os.listdir(FONTS_DIR):
            if os.path.splitext(f)[0] == font_name:
                return FileResponse(os.path.join(FONTS_DIR, f))
                
    if font_name in font_map and os.path.exists(font_map[font_name]):
        return FileResponse(font_map[font_name])
        
    return JSONResponse(status_code=404, content={"detail": "Font file not found"})

@app.delete("/api/fonts/{font_name}")
async def delete_font(font_name: str):
    if os.path.exists(FONTS_DIR):
        for f in os.listdir(FONTS_DIR):
            if os.path.splitext(f)[0] == font_name:
                os.remove(os.path.join(FONTS_DIR, f))
                return {"status": "success"}
    return JSONResponse(status_code=404, content={"detail": "Font not found"})

@app.get("/api/fonts")
async def get_fonts():
    fonts = [f for f in os.listdir(FONTS_DIR) if f.lower().endswith(('.ttf', '.otf', '.woff', '.woff2'))]
    return JSONResponse(content={"fonts": fonts})

def get_dir_size(path='.'):
    total = 0
    with os.scandir(path) as it:
        for entry in it:
            if entry.is_file():
                total += entry.stat().st_size
            elif entry.is_dir():
                total += get_dir_size(entry.path)
    return total

def format_size(size_bytes):
    if size_bytes == 0:
        return "0B"
    size_name = ("B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB")
    i = int(math.floor(math.log(size_bytes, 1024)))
    p = math.pow(1024, i)
    s = round(size_bytes / p, 2)
    return f"{s} {size_name[i]}"

@app.get("/api/models")
async def list_models():
    if not os.path.exists(MODELS_DIR):
        return {"models": []}
        
    models = []
    for item in os.listdir(MODELS_DIR):
        item_path = os.path.join(MODELS_DIR, item)
        if os.path.isdir(item_path) and item.startswith("models--"):
            parts = item.split("--")
            name = parts[-1] if len(parts) >= 3 else item
            
            size_bytes = get_dir_size(item_path)
            size_str = format_size(size_bytes)
            
            models.append({
                "id": item,
                "name": name,
                "size_bytes": size_bytes,
                "size_str": size_str
            })
            
    models.sort(key=lambda x: x["size_bytes"], reverse=True)
    return {"models": models}

@app.delete("/api/models/{model_id}")
async def delete_model(model_id: str):
    if not model_id.startswith("models--") or ".." in model_id:
        raise HTTPException(status_code=400, detail="Invalid model ID")
        
    model_path = os.path.join(MODELS_DIR, model_id)
    if os.path.exists(model_path) and os.path.isdir(model_path):
        try:
            shutil.rmtree(model_path)
            return {"status": "success"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    else:
        raise HTTPException(status_code=404, detail="Model not found")

# Try to serve frontend statically if the directory exists
# This is useful for production (Docker)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")
app.mount("/exports", StaticFiles(directory=EXPORT_DIR), name="exports")

if os.path.exists(FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")

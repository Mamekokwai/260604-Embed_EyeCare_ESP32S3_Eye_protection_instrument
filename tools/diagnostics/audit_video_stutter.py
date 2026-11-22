"""Collect reproducible, read-only evidence for the November video investigation."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile
from datetime import datetime


ROOT = Path(__file__).resolve().parents[2]


def run(args, **kwargs):
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", **kwargs)


def git(*args):
    result = run(["git", *args], check=True)
    return result.stdout.strip()


def riff_chunks(data):
    chunks = []
    offset = 12
    while offset + 8 <= len(data):
        tag = data[offset:offset + 4]
        size = struct.unpack_from("<I", data, offset + 4)[0]
        chunks.append({"tag": tag.decode("ascii", "replace"), "offset": offset,
                       "size": size, "list_type":
                       data[offset + 8:offset + 12].decode("ascii", "replace")
                       if tag == b"LIST" else None})
        offset += 8 + size + (size & 1)
    return chunks


def audit_video(path, baseline):
    probe = run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries",
                 "stream=codec_name,width,height,avg_frame_rate,nb_frames,duration,time_base",
                 "-show_entries", "packet=size,duration_time", "-of", "json",
                 str(path)], check=True)
    info = json.loads(probe.stdout)
    sizes = sorted(int(packet["size"]) for packet in info["packets"])
    data = path.read_bytes()
    avih = data.index(b"avih")
    fields = struct.unpack_from("<IIIIIIIIII", data, avih + 8)
    relative = path.relative_to(ROOT).as_posix()
    decode = run(["ffmpeg", "-v", "error", "-i", str(path),
                  "-map", "0:v:0", "-f", "null", "-"])
    return {
        "path": relative, "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "git_blob": git("hash-object", str(path)),
        "baseline_blob": git("rev-parse", f"{baseline}:{relative}"),
        "unchanged_from_baseline": git("hash-object", str(path)) ==
        git("rev-parse", f"{baseline}:{relative}"),
        "stream": info["streams"][0], "packet_count": len(sizes),
        "packet_duration_values": sorted({p.get("duration_time", "unknown")
                                          for p in info["packets"]}),
        "avih_usec_per_frame": fields[0], "avih_total_frames": fields[4],
        "avih_streams": fields[6], "jpeg_max_bytes": max(sizes),
        "jpeg_p95_bytes": sizes[int(.95 * (len(sizes) - 1))],
        "jpeg_over_96KiB": sum(size > 96 * 1024 for size in sizes),
        "decode_exit_code": decode.returncode, "decode_errors": decode.stderr,
        "riff_chunks": riff_chunks(data),
    }


def profile_calls(source):
    start = source.index("if (g_vp.frame_count % PROF_EVERY == 0)")
    end = source.index("g_vp.stats_ts = now;", start)
    return re.findall(r"ESP_LOGI\(TAG,.*?\);", source[start:end], re.S)


def profile_budget(source, baseline, config):
    # Format real source log calls; this models UART wire time, not device timing.
    calls = profile_calls(source)
    compiler = shutil.which("gcc")
    result = {"info_log_calls": len(calls), "frames_per_report": 100,
              "nominal_report_period_s_at_30fps": 100 / 30,
              "baseline_calls_identical": calls == profile_calls(
                  git("show", f"{baseline}:main/video_player.c")),
              "model_only_not_hardware_measurement": True}
    if not compiler:
        result["model_status"] = "gcc unavailable"
        return result
    fields = sorted(set(re.findall(r"g_vp\.(\w+)", "\n".join(calls))))
    declarations = ";".join(f"long long {field}" for field in fields) + ";"
    c = r'''
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#define PROF_EVERY 100
static unsigned total;
static void log_sample(const char *format, ...) {
    char output[1024];
    va_list args; va_start(args, format);
    int length = vsnprintf(output, sizeof(output), format, args);
    va_end(args);
    total += strlen("I (100000) video_player: ") + length + 2;
}
#define ESP_LOGI(tag, ...) log_sample(__VA_ARGS__)
'''
    c += "struct {" + declarations + "} g_vp;\n"
    c += "int main(void) {\n"
    c += "g_vp.frame_count=100;g_vp.loop_wraps=0;\n"
    c += "long long window=3333300,decode=1000000,cache=1000;\n"
    c += "double percent_scale=100.0/window;\n"
    c += "\n".join(calls)
    c += '\nprintf("%u",total);return 0;}\n'
    with tempfile.TemporaryDirectory(prefix="yt06-profile-budget-") as directory:
        path = Path(directory) / "profile.c"
        executable = path.with_suffix(".exe" if __import__("os").name == "nt" else "")
        path.write_text(c, encoding="utf-8")
        subprocess.run([compiler, "-std=c99", str(path), "-o", str(executable)],
                       check=True, capture_output=True)
        byte_count = int(subprocess.check_output([str(executable)], text=True))
    baud_match = re.search(r"^CONFIG_ESP_CONSOLE_UART_BAUDRATE=(\d+)$", config, re.M)
    baud = int(baud_match[1]) if baud_match else 115200
    result.update({"sample_formatted_bytes_including_prefix_crlf": byte_count,
                   "sample_prefix": "I (100000) video_player: ",
                   "baud": baud, "uart_8n1_wire_time_ms": byte_count * 10e3 / baud,
                   "optimistic_wait_ms_after_128_byte_fifo":
                   max(0, byte_count - 128) * 10e3 / baud,
                   "nominal_video_frame_ms": 1000 / 30,
                   "caveat": "Example numeric values and prefix. Actual log length, FIFO state, USB waits, preemption and driver setup must be measured on device."})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", default="74a8b78")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--firmware", type=Path)
    args = parser.parse_args()
    source = (ROOT / "main/video_player.c").read_text(encoding="utf-8")
    config = (ROOT / "sdkconfig").read_text(encoding="utf-8")
    media = ROOT / "Public/SD_Source"
    evidence = {
        "collected_at": datetime.now().astimezone().isoformat(),
        "repository": str(ROOT), "head": git("rev-parse", "HEAD"),
        "baseline": git("rev-parse", args.baseline),
        "scope": "Local source, local media and host tools; no flashing or device performance measurement.",
        "videos": [audit_video(path, args.baseline)
                   for path in sorted((media / "video").glob("*.avi"))],
        "profile_log_budget": profile_budget(source, args.baseline, config),
        "console_config": [line for line in config.splitlines()
                           if re.match(r"CONFIG_(ESP_CONSOLE_|LOG_DEFAULT_LEVEL|LOG_COLORS|FREERTOS_HZ)", line)],
    }
    bgm = run(["ffprobe", "-v", "error", "-show_entries",
               "format=duration:stream=codec_name,sample_rate,channels,bit_rate",
               "-of", "json", str(media / "music/1.mp3")], check=True)
    evidence["background_music"] = json.loads(bgm.stdout)
    if args.firmware:
        binary = args.firmware.read_bytes()
        evidence["inspected_firmware"] = {
            "path": str(args.firmware), "bytes": len(binary),
            "sha256": hashlib.sha256(binary).hexdigest(),
            "contains_video_profile_log": b"--- VID profile" in binary,
            "contains_audio_supply_log": b"supply: max_gap=" in binary,
            "caveat": "Presence confirms compiled strings, not the customer's currently flashed image or runtime log filtering.",
        }
    try:
        import serial.tools.list_ports
        evidence["serial_inventory"] = [
            {"device": port.device, "description": port.description, "hwid": port.hwid}
            for port in serial.tools.list_ports.comports()]
    except ImportError:
        evidence["serial_inventory"] = "pyserial unavailable; device not inspected"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(json.dumps({"output": str(args.output), "videos": len(evidence["videos"]),
                      "all_video_checks_pass": len(evidence["videos"]) == 4 and all(
                          v["unchanged_from_baseline"] and v["decode_exit_code"] == 0
                          and not v["decode_errors"] and v["jpeg_over_96KiB"] == 0
                          and v["stream"]["codec_name"] == "mjpeg"
                          and v["stream"]["width"] == 320 and v["stream"]["height"] == 320
                          and v["stream"]["avg_frame_rate"] == "30/1"
                          and v["avih_usec_per_frame"] == 33333
                          and v["packet_count"] == v["avih_total_frames"]
                          for v in evidence["videos"]),
                      "profile_log_budget": evidence["profile_log_budget"]},
                     ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()

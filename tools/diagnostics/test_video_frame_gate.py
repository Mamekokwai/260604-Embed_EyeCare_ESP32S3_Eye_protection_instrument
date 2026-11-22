"""Exercise the real AVI rate/LCD gate source before and after November's fix."""

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]


def rate_gate(source):
    tick = source.index("player_ret_t video_player_tick(void)")
    start = source.index("        if (!g_vp.first_vf)", tick)
    end = source.index("        g_vp.strip_ys = 0;", start)
    return source[start:end]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    baseline = subprocess.check_output(
        ["git", "show", "74a8b78:main/video_player.c"], cwd=ROOT,
        text=True, encoding="utf-8")
    current = (ROOT / "main/video_player.c").read_text(encoding="utf-8")
    pre = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#define PLAYER_BUSY 0
#define PLAYER_OK 1
typedef struct {
    bool first_vf;
    int64_t next_ts,rate_wait_started_at,prof_rate_wait;
    int64_t lcd_gate_wait_started_at,prof_lcd_gate_wait;
    struct { int64_t SecPerFrame; } avi;
} context_t;
static context_t g_vp;
static int64_t clock_us;
static bool gate_ready;
static int64_t esp_timer_get_time(void) { return clock_us; }
static bool lcd_is_ready(void) { return gate_ready; }
static void spilcd_wait_te(void) {}
'''
    post = r'''
static void reset(void) {
    memset(&g_vp,0,sizeof(g_vp));g_vp.first_vf=true;
    g_vp.avi.SecPerFrame=33333;g_vp.next_ts=33333;clock_us=0;gate_ready=true;
}
int main(void) {
    int i;
    for(i=0;i<2;i++) {
        int (*gate)(void)=i?new_gate:old_gate;
        reset();
        for(int frame=0;frame<100;frame++) {
            clock_us=(int64_t)frame*33333;
            assert(gate()==PLAYER_OK && g_vp.next_ts==clock_us+33333);
        }
        reset();assert(gate()==PLAYER_OK);
        clock_us=2000000;assert(gate()==PLAYER_OK);
        assert(g_vp.next_ts==66666);
    }
    reset();gate_ready=false;assert(old_gate()==PLAYER_BUSY);
    gate_ready=true;clock_us=1000;assert(old_gate()==PLAYER_BUSY);
    clock_us=33333;assert(old_gate()==PLAYER_OK);
    reset();gate_ready=false;assert(new_gate()==PLAYER_BUSY);
    assert(g_vp.first_vf && g_vp.next_ts==33333);
    gate_ready=true;clock_us=1000;assert(new_gate()==PLAYER_OK);
    assert(g_vp.next_ts==34333);
    reset();assert(old_gate()==PLAYER_OK);clock_us=33333;
    gate_ready=false;assert(old_gate()==PLAYER_BUSY);
    gate_ready=true;clock_us=35000;assert(old_gate()==PLAYER_BUSY);
    clock_us=66666;assert(old_gate()==PLAYER_OK);
    reset();assert(new_gate()==PLAYER_OK);clock_us=33333;
    gate_ready=false;assert(new_gate()==PLAYER_BUSY);
    assert(g_vp.next_ts==33333);
    gate_ready=true;clock_us=35000;assert(new_gate()==PLAYER_OK);
    puts("{\"normal_100_frames_both_versions\":\"PASS\","
         "\"startup_gate_busy_old_accept_us\":33333,"
         "\"startup_gate_busy_new_accept_us\":1000,"
         "\"second_frame_gate_busy_old_accept_us\":66666,"
         "\"second_frame_gate_busy_new_accept_us\":35000,"
         "\"long_stall_catchup_policy_same_in_both\":true,"
         "\"gate_retry_regression\":\"PASS\"}");
}
'''
    c = pre + "\nstatic int old_gate(void){\n" + rate_gate(baseline)
    c += "\nreturn PLAYER_OK;}\nstatic int new_gate(void){\n" + rate_gate(current)
    c += "\nreturn PLAYER_OK;}\n" + post
    compiler = shutil.which("gcc")
    if not compiler:
        raise RuntimeError("gcc is required for this source-level host test")
    with tempfile.TemporaryDirectory(prefix="yt06-frame-gate-") as directory:
        path = Path(directory) / "frame_gate.c"
        executable = path.with_suffix(".exe" if __import__("os").name == "nt" else "")
        path.write_text(c, encoding="utf-8")
        subprocess.run([compiler, "-std=c99", str(path), "-o", str(executable)],
                       check=True, capture_output=True)
        result = json.loads(subprocess.check_output([str(executable)], text=True))
    result["scope"] = "Real rate/LCD gate source excerpt; clock and LCD readiness stubbed. Not device performance evidence."
    result["conclusion"] = "500be5b prevents deadline advance on a rejected gate; steady no-gate timing and late catch-up policy are unchanged."
    output = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()

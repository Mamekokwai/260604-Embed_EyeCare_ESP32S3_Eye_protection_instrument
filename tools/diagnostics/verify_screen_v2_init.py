"""Compare the compiled screen V2 profile against the supplier's init sequence."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
REFERENCE_NAME = "C_JD9855_Truly1.54_HE454-015T2BZZ_320x320_MCU8_V1.0_G2.2_20260521(2).c"


def supplier_steps(path):
    source = re.sub(r"//[^\n]*|/\*.*?\*/", "",
                    path.read_text(encoding="utf-8-sig"), flags=re.S)
    pattern = r"(SSD_CMD|SSD_PAR|Delayms)\s*\(\s*(0x[0-9a-fA-F]+|\d+)\s*\)"
    steps = []
    for match in re.finditer(pattern, source):
        kind, value = match[1], int(match[2], 0)
        if kind == "SSD_CMD":
            steps.append({"cmd": value, "data": [], "delay_ms": 0})
        elif kind == "SSD_PAR":
            steps[-1]["data"].append(value)
        else:
            steps[-1]["delay_ms"] += value
    if re.sub(pattern, "", source).strip("; \r\n\t"):
        raise ValueError("Supplier source contains unparsed code")
    return steps


def compiled_steps():
    compiler = shutil.which("gcc")
    if not compiler:
        raise RuntimeError("gcc is required to inspect the compiled init table")
    stub = """
#include <stddef.h>
#include <stdint.h>
typedef struct { int cmd; const void *data; size_t data_bytes; unsigned delay_ms; } jd9855_lcd_init_cmd_t;
typedef struct { const jd9855_lcd_init_cmd_t *init_cmds; uint16_t init_cmds_size; } jd9855_vendor_config_t;
"""
    program = r'''
#include <assert.h>
#include <stdio.h>
#include "screen_v2_init.h"
int main(void) {
    const jd9855_vendor_config_t *config = &gd150hv184r_a_vendor_config;
    assert(config->init_cmds == gd150hv184r_a_init_cmds);
    printf("[");
    for(unsigned i=0;i<config->init_cmds_size;i++) {
        const jd9855_lcd_init_cmd_t *cmd=&config->init_cmds[i];
        const uint8_t *data=cmd->data;
        printf("%s{\"cmd\":%d,\"data\":[",i?",":"",cmd->cmd);
        for(size_t j=0;j<cmd->data_bytes;j++) printf("%s%u",j?",":"",data[j]);
        printf("],\"delay_ms\":%u}",cmd->delay_ms);
    }
    printf("]");return 0;
}
'''
    with tempfile.TemporaryDirectory(prefix="yt06-screen-v2-") as directory:
        temp = Path(directory)
        (temp / "esp_lcd_jd9855.h").write_text(stub, encoding="utf-8")
        path = temp / "profile.c"
        path.write_text(program, encoding="utf-8")
        executable = path.with_suffix(".exe" if os.name == "nt" else "")
        subprocess.run([compiler, "-std=c99", "-I", str(temp), "-I",
                        str(ROOT / "components/BSP/SPILCD"), str(path),
                        "-o", str(executable)], check=True, capture_output=True)
        return json.loads(subprocess.check_output([str(executable)], text=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path,
                        default=ROOT.parent.parent / "info/屏幕V2" / REFERENCE_NAME)
    args = parser.parse_args()
    expected = supplier_steps(args.reference)
    te_steps = [step for step in expected if step["cmd"] == 0x35]
    assert len(expected) == 48 and len(te_steps) == 1 and not te_steps[0]["data"]
    # The board routes GPIO1 to its backlight, not the panel TE output.
    te_steps[0]["cmd"] = 0x34
    actual = compiled_steps()
    assert actual == expected, "Compiled command, parameter, order or delay differs from supplier profile"
    assert {"cmd": 0x3A, "data": [0x55], "delay_ms": 0} in actual
    for cmd in (0x2A, 0x2B):
        assert {"cmd": cmd, "data": [0, 0, 1, 0x3F], "delay_ms": 0} in actual
    assert actual[-2:] == [{"cmd": 0x11, "data": [], "delay_ms": 120},
                          {"cmd": 0x29, "data": [], "delay_ms": 20}]
    bsp = (ROOT / "components/BSP/SPILCD/spilcd.c").read_text(encoding="utf-8")
    assert ".vendor_config = (void *)&gd150hv184r_a_vendor_config" in bsp
    print(json.dumps({"result": "PASS", "command_count": len(actual),
                      "supplier_sha256": hashlib.sha256(args.reference.read_bytes()).hexdigest(),
                      "total_command_delay_ms": sum(step["delay_ms"] for step in actual),
                      "intentional_board_adaptation": "TE ON 0x35 -> TE OFF 0x34; no parameters",
                      "scope": "Real compiled init table and BSP selection; API types stubbed. Full SDK build and panel hardware checks are separate."},
                     ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()

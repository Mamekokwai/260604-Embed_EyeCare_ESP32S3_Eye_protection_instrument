"""Read every local AVI twice with the real firmware parser and stream reader."""

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]


def function(source, name):
    match = re.search(r"static (?:esp_err_t|void|bool) " + name + r"\([^)]*\)\s*\{", source)
    offset = source.index("{", match.start()) + 1
    depth = 1
    while depth:
        depth += (source[offset] == "{") - (source[offset] == "}")
        offset += 1
    return source[match.start():offset]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    source = (ROOT / "main/video_player.c").read_text(encoding="utf-8")
    pre = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>
#include "avi.h"
#define DMA_BUF_SIZE (16*1024)
#define MAX_JPEG_SIZE (96*1024)
#define MALLOC_CAP_INTERNAL 0
#define MALLOC_CAP_DMA 0
#define heap_caps_malloc(bytes,flags) malloc(bytes)
typedef unsigned int UINT;
typedef int FRESULT;
typedef FILE FIL;
typedef struct { FIL *file; uint8_t *buf; size_t buf_size,pos,valid; } sr_t;
static uint8_t *s_refill_buf;
static unsigned read_calls;
static FRESULT f_read(FIL *file,void *data,UINT size,UINT *read_bytes) {
    read_calls++;*read_bytes=(UINT)fread(data,1,size,file);return ferror(file)?1:0;
}
static int f_lseek(FIL *file,unsigned long offset) { return fseek(file,(long)offset,SEEK_SET); }
'''
    post = r'''
#ifdef _WIN32
int wmain(int argc,wchar_t **argv) {
    assert(argc==2);FILE *file=_wfopen(argv[1],L"rb");
#else
int main(int argc,char **argv) {
    assert(argc==2);FILE *file=fopen(argv[1],"rb");
#endif
    assert(file);sr_t sr;AVI_INFO avi;
    assert(sr_open(&sr,file,DMA_BUF_SIZE)==ESP_OK);
    assert(sr_refill(&sr)==ESP_OK && avi_init(sr.buf,(uint32_t)sr.valid,&avi)==AVI_OK);
    uint8_t *jpeg=malloc(MAX_JPEG_SIZE);assert(jpeg);
    uint32_t hashes[2];unsigned counts[2],calls[2];
    for(int loop=0;loop<2;loop++) {
        assert(f_lseek(file,avi.MoviOffset+4)==0);sr.pos=sr.valid=0;
        read_calls=0;unsigned frames=0;uint32_t hash=2166136261U;size_t size;
        while(read_one_chunk(&sr,&avi,jpeg,&size)) {
            assert(size>=2 && jpeg[0]==0xff && jpeg[1]==0xd8);
            for(size_t i=0;i<size;i++) hash=(hash^jpeg[i])*16777619U;
            frames++;
        }
        counts[loop]=frames;hashes[loop]=hash;calls[loop]=read_calls;
        assert(frames==avi.TotalFrame);
    }
    assert(hashes[0]==hashes[1] && counts[0]==counts[1]);
    printf("{\"frames_per_loop\":%u,\"second_loop_frames\":%u,"
           "\"ordered_jpeg_hash\":%u,\"loop_hash_equal\":true,"
           "\"read_calls_first_loop\":%u,\"read_calls_second_loop\":%u,\"result\":\"PASS\"}",
           counts[0],counts[1],hashes[0],calls[0],calls[1]);
    free(jpeg);sr_close(&sr);free(s_refill_buf);fclose(file);return 0;
}
'''
    names = ["sr_open", "sr_close", "sr_refill", "sr_ensure", "sr_read",
             "sr_read_bytes", "sr_skip_pad", "read_one_chunk"]
    c = pre + "\n" + "\n".join(function(source, name) for name in names) + post
    compiler = shutil.which("gcc")
    if not compiler:
        raise RuntimeError("gcc is required for this source-level host test")
    results = []
    with tempfile.TemporaryDirectory(prefix="yt06-stream-loop-") as directory:
        temp = Path(directory)
        (temp / "esp_err.h").write_text(
            "typedef int esp_err_t;\n#define ESP_OK 0\n#define ESP_FAIL -1\n#define ESP_ERR_NOT_FOUND -2\n#define ESP_ERR_NO_MEM -3\n",
            encoding="utf-8")
        (temp / "esp_log.h").write_text(
            "#define ESP_LOGI(...) ((void)0)\n#define ESP_LOGW(...) ((void)0)\n#define ESP_LOGE(...) ((void)0)\n",
            encoding="utf-8")
        path = temp / "stream_loop.c"
        path.write_text(c, encoding="utf-8")
        executable = path.with_suffix(".exe" if os.name == "nt" else "")
        command = [compiler, "-std=c99", "-I", str(temp), "-I", str(ROOT / "main/include"),
                   str(path), str(ROOT / "main/avi.c"), "-o", str(executable)]
        if os.name == "nt":
            command.append("-municode")
        subprocess.run(command, check=True, capture_output=True)
        for video in sorted((ROOT / "Public/SD_Source/video").glob("*.avi")):
            result = json.loads(subprocess.check_output([str(executable), str(video)], text=True))
            results.append({"file": video.relative_to(ROOT).as_posix(), **result})
    output = json.dumps({"scope": "Real avi.c + stream functions; FatFS/heap wrapped by host stdio/malloc. Verifies parsing and repeat order, not SD latency or DMA concurrency.",
                         "results": results}, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()

// SPDX-License-Identifier: GPL-2.0-only
/* Exercise the real queue allocator without opening a device. */
#define ioctl fake_ioctl
#define mmap fake_mmap
#define munmap fake_munmap
#define main video_client_main
#include "decode-one-frame.c"
#undef main
#include <stdarg.h>
#include <assert.h>

static unsigned int granted, plane_bytes, plane_count, query_error_index;
static unsigned int map_calls, unmap_calls, map_fail_index;
static void reset(void)
{
    granted=4; plane_bytes=7077888; plane_count=1; query_error_index=UINT32_MAX;
    map_calls=unmap_calls=0; map_fail_index=UINT32_MAX;
}
int fake_ioctl(int fd, unsigned long request, ...)
{
    (void)fd;
    va_list args; va_start(args,request); void *arg=va_arg(args,void *); va_end(args);
    if (request==VIDIOC_REQBUFS) {
        ((struct v4l2_requestbuffers *)arg)->count=granted; return 0;
    }
    assert(request==VIDIOC_QUERYBUF);
    struct v4l2_buffer *b=arg;
    if (b->index==query_error_index) { errno=EIO; return -1; }
    b->length=plane_count; b->m.planes[0].length=plane_bytes;
    return 0;
}
void *fake_mmap(void *address, size_t length, int prot, int flags, int fd, off_t offset)
{
    (void)address; (void)prot; (void)flags; (void)fd; (void)offset;
    if (map_calls++==map_fail_index) { errno=ENOMEM; return MAP_FAILED; }
    return calloc(1,length);
}
int fake_munmap(void *address, size_t length)
{
    assert(address && length); unmap_calls++; free(address); return 0;
}
static void reject(unsigned int size, unsigned int mapped)
{
    struct queue q={.type=OUTPUT};
    assert(allocate_queue(-1,&q,4,size)==-1);
    assert(stop(-1,&q)); assert(unmap_calls==mapped);
}
int main(void)
{
    /* iris_dec_bitstream_buffer_size(): 4096*2304*3/2/2, not 2 MiB. */
    reset(); struct queue q={.type=OUTPUT};
    assert(allocate_queue(-1,&q,4,7077888)==0 && q.count==4);
    assert(map_calls==4 && stop(-1,&q) && unmap_calls==4);
    reset(); reject(0,0);
    reset(); reject(MAX_INPUT_BYTES+1,0);
    reset(); granted=0; reject(7077888,0);
    reset(); granted=MAX_BUFFERS+1; reject(7077888,0);
    reset(); granted=10; reject(7077888,0); /* aggregate RAM bound */
    reset(); plane_count=2; reject(7077888,0);
    reset(); plane_bytes=7077887; reject(7077888,0);
    reset(); plane_bytes=MAX_INPUT_BYTES+1; reject(7077888,0);
    reset(); query_error_index=2; reject(7077888,2);
    reset(); map_fail_index=2; reject(7077888,2);
    reset(); q=(struct queue){.type=CAPTURE};
    assert(allocate_queue(-1,&q,4,7077888)==-1 && map_calls==0);
    reset(); plane_bytes=151552; q=(struct queue){.type=CAPTURE};
    assert(allocate_queue(-1,&q,16,151552)==0 && stop(-1,&q));
    puts("VIDEO_QUEUE_ALLOCATOR_SELFTEST_PASS_NO_DEVICE_OPEN");
    return 0;
}

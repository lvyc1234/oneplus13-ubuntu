// SPDX-License-Identifier: GPL-2.0-only
/* Bounded stateful V4L2 AVC/NV12 test. Input is a generated public test card. */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <linux/videodev2.h>
#include <poll.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#define WIDTH 320U
#define HEIGHT 240U
#define MAX_BUFFERS 24U
#define MAX_INPUT_BYTES (8U * 1024U * 1024U)
#define MAX_CAPTURE_BYTES (2U * 1024U * 1024U)
#define MAX_QUEUE_BYTES (64U * 1024U * 1024U)
#define OUTPUT V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE
#define CAPTURE V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE

struct mapped { void *data; size_t length; };
struct queue { enum v4l2_buf_type type; unsigned int count; bool streaming;
               struct mapped buffers[MAX_BUFFERS]; };
static const char *stage = "entry";

static int call_ioctl(int fd, unsigned long request, void *argument)
{
    int result;
    do { result = ioctl(fd, request, argument); } while (result < 0 && errno == EINTR);
    return result;
}

static double now(void)
{
    struct timespec t;
    if (clock_gettime(CLOCK_MONOTONIC, &t)) return 1e30;
    return (double)t.tv_sec + (double)t.tv_nsec / 1e9;
}

static bool verify_luma(const uint8_t *data, size_t length, unsigned int stride,
                        unsigned int top, unsigned int left)
{
    const unsigned int values[4] = {81, 145, 41, 235};
    if (stride < WIDTH || stride > 2048 || top > 32 || left > 128 || left + WIDTH > stride)
        return false;
    if ((size_t)(top + HEIGHT - 1) * stride + left + WIDTH > length) return false;
    /* Inspect all interior pixels, leaving each compressed bar edge eight pixels. */
    for (unsigned int y = 8; y < HEIGHT - 8; y++)
        for (unsigned int bar = 0; bar < 4; bar++)
            for (unsigned int x = bar * 80 + 8; x < (bar + 1) * 80 - 8; x++) {
                int difference = (int)data[(size_t)(y + top) * stride + x + left] -
                                 (int)values[bar];
                if (difference < -6 || difference > 6) return false;
            }
    return true;
}

static int selftest(void)
{
    const unsigned int stride = 384, height = 256, values[4] = {81,145,41,235};
    size_t bytes = (size_t)stride * height;
    uint8_t *data = calloc(1, bytes);
    if (!data) return 1;
    for (unsigned int y = 0; y < HEIGHT; y++)
        for (unsigned int x = 0; x < WIDTH; x++) data[y * stride + x] = values[x / 80];
    bool pass = verify_luma(data, bytes, stride, 0, 0) &&
                !verify_luma(data, 20, stride, 0, 0) &&
                !verify_luma(data, bytes, 319, 0, 0) &&
                !verify_luma(data, bytes, stride, 33, 0) &&
                !verify_luma(data, bytes, stride, 0, 128);
    data[20 * stride + 20] += 7;
    pass = pass && !verify_luma(data, bytes, stride, 0, 0);
    memset(data, 0, bytes);
    pass = pass && !verify_luma(data, bytes, stride, 0, 0);
    free(data);
    if (pass) puts("VIDEO_PIXEL_VALIDATOR_SELFTEST_PASS_NO_DEVICE_OPEN");
    return pass ? 0 : 1;
}

static int allocate_queue(int fd, struct queue *q, unsigned int requested,
                          unsigned int sizeimage)
{
    unsigned int limit = q->type == OUTPUT ? MAX_INPUT_BYTES : MAX_CAPTURE_BYTES;
    if (!sizeimage || sizeimage > limit) { errno=EOVERFLOW; return -1; }
    struct v4l2_requestbuffers request = {.type=q->type, .memory=V4L2_MEMORY_MMAP,
                                         .count=requested};
    if (call_ioctl(fd, VIDIOC_REQBUFS, &request)) return -1;
    fprintf(stderr,"IRIS_QUEUE type=%u count=%u sizeimage=%u\n",
            q->type,request.count,sizeimage);
    if (!request.count || request.count > MAX_BUFFERS) { errno=EOVERFLOW; return -1; }
    if (request.count > MAX_QUEUE_BYTES / sizeimage) { errno=EOVERFLOW; return -1; }
    q->count = request.count;
    for (unsigned int i=0; i<q->count; i++) {
        struct v4l2_plane plane = {0};
        struct v4l2_buffer b = {.type=q->type, .memory=V4L2_MEMORY_MMAP,
                               .index=i, .length=1, .m.planes=&plane};
        if (call_ioctl(fd, VIDIOC_QUERYBUF, &b)) return -1;
        fprintf(stderr,"IRIS_BUFFER type=%u index=%u planes=%u bytes=%u\n",
                q->type,i,b.length,plane.length);
        if (b.length!=1 || plane.length<sizeimage || plane.length>limit ||
            plane.length>MAX_QUEUE_BYTES/q->count) {
            errno=EOVERFLOW; return -1;
        }
        void *mapping=mmap(NULL,plane.length,PROT_READ|PROT_WRITE,MAP_SHARED,fd,plane.m.mem_offset);
        if (mapping==MAP_FAILED) return -1;
        q->buffers[i]=(struct mapped){.data=mapping,.length=plane.length};
    }
    return 0;
}

static int enqueue(int fd, struct queue *q, unsigned int index, size_t used)
{
    if (index>=q->count || used>q->buffers[index].length) { errno=EOVERFLOW; return -1; }
    struct v4l2_plane plane = {.bytesused=used,.length=q->buffers[index].length};
    struct v4l2_buffer b = {.type=q->type,.memory=V4L2_MEMORY_MMAP,.index=index,
                           .length=1,.m.planes=&plane,.timestamp={.tv_sec=12,.tv_usec=345000}};
    return call_ioctl(fd,VIDIOC_QBUF,&b);
}

static int start(int fd, struct queue *q)
{
    if (call_ioctl(fd,VIDIOC_STREAMON,&q->type)) return -1;
    q->streaming=true;
    return 0;
}

static bool stop(int fd, struct queue *q)
{
    bool pass=true;
    if (q->streaming && call_ioctl(fd,VIDIOC_STREAMOFF,&q->type)) pass=false;
    q->streaming=false;
    for (unsigned int i=0; i<q->count; i++)
        if (q->buffers[i].data) {
            if (munmap(q->buffers[i].data,q->buffers[i].length)) pass=false;
            q->buffers[i].data=NULL;
        }
    return pass;
}

static int decode(const char *device, const char *fixture)
{
    int fd=-1, input=-1, error=0;
    bool verified=false, cleaned=false, source_change=false, stop_sent=false;
    struct queue output={.type=OUTPUT}, capture={.type=CAPTURE};
    unsigned int stride=0, top=0, left=0;
    uint8_t compressed[16384];
    size_t compressed_bytes=0;
    struct stat st;
    double deadline=now()+20;
    /* The Python owner/session/DT/hash guard must execute first. */
    if (geteuid()!=0 || !getenv("ONEPLUS13_GUARDED_VIDEO_TEST") ||
        strcmp(getenv("ONEPLUS13_GUARDED_VIDEO_TEST"),"1")) return 2;
    stage="fixture";
    input=open(fixture,O_RDONLY|O_CLOEXEC|O_NOFOLLOW);
    if (input<0 || fstat(input,&st) || !S_ISREG(st.st_mode) || st.st_size!=905) goto fail;
    ssize_t n=read(input,compressed,sizeof(compressed));
    if (n!=st.st_size) goto fail;
    compressed_bytes=(size_t)n;
    if (close(input)) { input=-1; goto fail; }
    input=-1;
    stage="device-stat";
    if (strncmp(device,"/dev/video",10) || stat(device,&st) || !S_ISCHR(st.st_mode)) goto fail;
    stage="firmware-open";
    fd=open(device,O_RDWR|O_NONBLOCK|O_CLOEXEC|O_NOFOLLOW);
    if (fd<0) goto fail;
    struct v4l2_capability capability={0};
    stage="querycap";
    if (call_ioctl(fd,VIDIOC_QUERYCAP,&capability) ||
        strcmp((char *)capability.driver,"iris_driver") ||
        strcmp((char *)capability.card,"Iris Decoder") ||
        !(capability.device_caps & V4L2_CAP_VIDEO_M2M_MPLANE) ||
        !(capability.device_caps & V4L2_CAP_STREAMING)) goto fail;
    puts("IRIS_FIRMWARE_SESSION_OPENED"); fflush(stdout);
    stage="subscribe-source-change";
    struct v4l2_event_subscription subscription={.type=V4L2_EVENT_SOURCE_CHANGE};
    if (call_ioctl(fd,VIDIOC_SUBSCRIBE_EVENT,&subscription)) goto fail;
    stage="output-format";
    struct v4l2_format format={.type=OUTPUT};
    format.fmt.pix_mp.width=WIDTH; format.fmt.pix_mp.height=HEIGHT;
    format.fmt.pix_mp.pixelformat=V4L2_PIX_FMT_H264;
    format.fmt.pix_mp.field=V4L2_FIELD_NONE; format.fmt.pix_mp.num_planes=1;
    format.fmt.pix_mp.plane_fmt[0].sizeimage=1024*1024;
    if (call_ioctl(fd,VIDIOC_S_FMT,&format) || format.fmt.pix_mp.pixelformat!=V4L2_PIX_FMT_H264 ||
        format.fmt.pix_mp.num_planes!=1) goto fail;
    stage="output-buffers";
    if (allocate_queue(fd,&output,4,format.fmt.pix_mp.plane_fmt[0].sizeimage) ||
        output.buffers[0].length<compressed_bytes) goto fail;
    memcpy(output.buffers[0].data,compressed,compressed_bytes);
    if (enqueue(fd,&output,0,compressed_bytes) || start(fd,&output)) goto fail;
    while (now()<deadline && !verified) {
        struct pollfd p={.fd=fd,.events=POLLIN|POLLPRI|POLLOUT};
        stage="poll-frame";
        int ready=poll(&p,1,200);
        if (ready<0 && errno!=EINTR) goto fail;
        if (p.revents&POLLNVAL) goto fail;
        if (p.revents&POLLPRI) {
            struct v4l2_event event={0};
            if (call_ioctl(fd,VIDIOC_DQEVENT,&event)) { if (errno!=EAGAIN) goto fail; }
            else if (event.type==V4L2_EVENT_SOURCE_CHANGE &&
                     event.u.src_change.changes & V4L2_EVENT_SRC_CH_RESOLUTION) source_change=true;
        }
        if (source_change && !capture.streaming) {
            stage="capture-format";
            memset(&format,0,sizeof(format)); format.type=CAPTURE;
            format.fmt.pix_mp.width=WIDTH; format.fmt.pix_mp.height=HEIGHT;
            format.fmt.pix_mp.pixelformat=V4L2_PIX_FMT_NV12;
            format.fmt.pix_mp.field=V4L2_FIELD_NONE;
            if (call_ioctl(fd,VIDIOC_S_FMT,&format) || format.fmt.pix_mp.num_planes!=1 ||
                format.fmt.pix_mp.pixelformat!=V4L2_PIX_FMT_NV12 ||
                format.fmt.pix_mp.width<WIDTH || format.fmt.pix_mp.width>512 ||
                format.fmt.pix_mp.height<HEIGHT || format.fmt.pix_mp.height>288) goto fail;
            stride=format.fmt.pix_mp.plane_fmt[0].bytesperline;
            struct v4l2_selection selection={.type=V4L2_BUF_TYPE_VIDEO_CAPTURE,
                                            .target=V4L2_SEL_TGT_COMPOSE};
            if (call_ioctl(fd,VIDIOC_G_SELECTION,&selection) || selection.r.width!=WIDTH ||
                selection.r.height!=HEIGHT || selection.r.left<0 || selection.r.top<0) goto fail;
            left=selection.r.left; top=selection.r.top;
            stage="capture-buffers";
            if (allocate_queue(fd,&capture,16,format.fmt.pix_mp.plane_fmt[0].sizeimage)) goto fail;
            for (unsigned int i=0; i<capture.count; i++) if (enqueue(fd,&capture,i,0)) goto fail;
            if (start(fd,&capture)) goto fail;
        }
        if (capture.streaming && !stop_sent) {
            stage="decoder-drain";
            struct v4l2_decoder_cmd command={.cmd=V4L2_DEC_CMD_STOP};
            if (call_ioctl(fd,VIDIOC_DECODER_CMD,&command)) goto fail;
            stop_sent=true;
        }
        struct v4l2_plane plane={0};
        struct v4l2_buffer b={.type=OUTPUT,.memory=V4L2_MEMORY_MMAP,.length=1,.m.planes=&plane};
        if (call_ioctl(fd,VIDIOC_DQBUF,&b) && errno!=EAGAIN) goto fail;
        if (capture.streaming) {
            memset(&plane,0,sizeof(plane)); memset(&b,0,sizeof(b));
            b.type=CAPTURE; b.memory=V4L2_MEMORY_MMAP; b.length=1; b.m.planes=&plane;
            stage="capture-frame";
            if (!call_ioctl(fd,VIDIOC_DQBUF,&b)) {
                if (b.index>=capture.count || b.flags&V4L2_BUF_FLAG_ERROR ||
                    b.timestamp.tv_sec!=12 || b.timestamp.tv_usec!=345000 ||
                    plane.bytesused>capture.buffers[b.index].length ||
                    plane.data_offset>plane.bytesused) goto fail;
                if (plane.bytesused) verified=verify_luma(
                    (uint8_t *)capture.buffers[b.index].data+plane.data_offset,
                    plane.bytesused-plane.data_offset,stride,top,left);
                if (!verified) { errno=EBADMSG; goto fail; }
            } else if (errno!=EAGAIN) goto fail;
        }
        usleep(5000);
    }
    if (!verified) { errno=ETIMEDOUT; goto fail; }
    error=0;
    goto cleanup;
fail:
    error=errno ? errno : EPROTO;
    fprintf(stderr,"IRIS_VIDEO_FAILURE stage=%s errno=%d\n",stage,error);
cleanup:
    if (fd>=0) {
        bool capture_stop=stop(fd,&capture), output_stop=stop(fd,&output);
        int close_result=close(fd);
        cleaned=capture_stop && output_stop && close_result==0;
    }
    if (input>=0) close(input);
    printf("IRIS_FRAME_RESULT {\"frame_verified\":%s,\"streams_stopped\":%s,"
           "\"software_fallback\":false,\"frame_width\":320,\"frame_height\":240} \n",
           verified ? "true":"false",cleaned ? "true":"false");
    return !error && verified && cleaned ? 0 : 1;
}

int main(int argc, char **argv)
{
    if (argc==2 && !strcmp(argv[1],"--selftest")) return selftest();
    if (argc==4 && !strcmp(argv[1],"--decode")) return decode(argv[2],argv[3]);
    fputs("Use --selftest, or guarded --decode /dev/videoN generated-frame.h264\n",stderr);
    return 2;
}

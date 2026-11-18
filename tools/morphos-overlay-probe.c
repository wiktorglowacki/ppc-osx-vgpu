/* Bounded cgxvideo overlay diagnostic; no guest files or libraries changed.
 * SDK cgxvideo.doc specifies Y0,U,Y1,V bytes and little-endian RGB565.
 * Pass this source as the third argument to the bitmap probe builder.
 */
#include <stdio.h>
#include <proto/exec.h>
#include <proto/dos.h>
#include <proto/intuition.h>
#include <proto/cgxvideo.h>
#include <cybergraphx/cgxvideo.h>

struct IntuitionBase *IntuitionBase;
struct Library *CGXVideoBase;

int main(void)
{
    struct Screen *screen = 0;
    struct Window *window = 0;
    struct VLayerHandle *layer = 0;
    ULONG features, formats, error = 0, completed = 0;
    int result = 1, attached = 0;
    const unsigned char yuv[4][3] = {{81,90,240},{145,54,34},{41,240,110},{235,128,128}};
    const unsigned short rgb565[4] = {0xf800,0x07e0,0x001f,0xffff};
    IntuitionBase = (struct IntuitionBase *)OpenLibrary("intuition.library", 50);
    CGXVideoBase = OpenLibrary("cgxvideo.library", 43);
    if (!IntuitionBase || !CGXVideoBase) { puts("OVERLAY_FAIL libraries"); goto done; }
    screen = LockPubScreen(0);
    if (!screen) { puts("OVERLAY_FAIL screen"); goto done; }
    features = QueryVLayerAttr(screen, VSQ_SupportedFeatures);
    formats = QueryVLayerAttr(screen, VSQ_SupportedFormats);
    printf("OVERLAY capabilities=%08lx formats=%08lx cgxvideo=%u.%u\n",
           (unsigned long)features, (unsigned long)formats,
           CGXVideoBase->lib_Version, CGXVideoBase->lib_Revision);
    if (!(features & VSQ_FEAT_OVERLAY)) { puts("OVERLAY_FAIL unavailable"); goto done; }
    window = OpenWindowTags(0, WA_PubScreen, (IPTR)screen, WA_Left, 100, WA_Top, 120,
                           WA_InnerWidth, 256, WA_InnerHeight, 192, WA_DragBar, TRUE,
                           WA_DepthGadget, TRUE, WA_Activate, TRUE,
                           WA_Title, (IPTR)"MorphOS overlay probe", TAG_DONE);
    if (!window) { puts("OVERLAY_FAIL window"); goto done; }
    for (unsigned format = 0; format < 2; format++) {
        ULONG bit = format ? VSQ_FMT_R5G6B5_LE : VSQ_FMT_YUYV;
        ULONG db = !!(features & VSQ_FEAT_DOUBLEBUFFER);
        struct TagItem tags[] = {
            {VOA_SrcType, format ? SRCFMT_R5G6B5PC : SRCFMT_YCbCr16},
            {VOA_SrcWidth, 64}, {VOA_SrcHeight, 64}, {VOA_Modulo, 128},
            {VOA_UseFilter, FALSE}, {VOA_UseColorKey, TRUE},
            {VOA_UseBackfill, TRUE}, {VOA_DoubleBuffer, db},
            {VOA_Error, (IPTR)&error}, {TAG_DONE, 0}
        };
        if (!(formats & bit)) { printf("OVERLAY_SKIP format=%u\n", format); continue; }
        layer = CreateVLayerHandleTagList(screen, tags);
        if (!layer) { printf("OVERLAY_FAIL create=%lu\n", (unsigned long)error); goto done; }
        if (AttachVLayerTagList(layer, window, 0)) { puts("OVERLAY_FAIL attach"); goto done; }
        attached = 1;
        for (unsigned frame = 0; frame < 8; frame++) {
            if (!LockVLayer(layer)) { puts("OVERLAY_FAIL lock"); goto done; }
            volatile unsigned char *pixels = (APTR)GetVLayerAttr(layer, VOA_BaseAddress);
            ULONG stride = GetVLayerAttr(layer, VOA_Modulo);
            ULONG width = GetVLayerAttr(layer, VOA_Width), height = GetVLayerAttr(layer, VOA_Height);
            printf("OVERLAY frame format=%u frame=%u base=%p stride=%lu size=%lux%lu index=%lu\n",
                   format, frame, (void *)pixels, (unsigned long)stride,
                   (unsigned long)width, (unsigned long)height,
                   (unsigned long)GetVLayerAttr(layer, VOA_FrameIndex));
            if (!pixels || stride < 128 || width < 64 || height < 64) {
                UnlockVLayer(layer); puts("OVERLAY_FAIL layout"); goto done;
            }
            for (unsigned y = 0; y < 64; y++) for (unsigned x = 0; x < 64; x += 2) {
                unsigned bar = ((x / 16) + frame) & 3;
                volatile unsigned char *p = pixels + y * stride + x * 2;
                if (format) {
                    p[0] = p[2] = rgb565[bar] & 255;
                    p[1] = p[3] = rgb565[bar] >> 8;
                } else {
                    p[0] = p[2] = yuv[bar][0]; p[1] = yuv[bar][1]; p[3] = yuv[bar][2];
                }
            }
            UnlockVLayer(layer);
            if (db) SwapVLayerBuffer(layer);
            fflush(stdout);
            if (frame == 4) MoveWindow(window, 80, 40);
            Delay(frame == 0 || frame == 4 ? 100 : 15);
        }
        DetachVLayer(layer); attached = 0;
        DeleteVLayerHandle(layer); layer = 0; completed++;
    }
    result = completed ? 0 : 1;
    printf("OVERLAY_DONE formats=%lu result=%d\n", (unsigned long)completed, result);
done:
    if (attached) DetachVLayer(layer);
    if (layer) DeleteVLayerHandle(layer);
    if (window) CloseWindow(window);
    if (screen) UnlockPubScreen(0, screen);
    if (CGXVideoBase) CloseLibrary(CGXVideoBase);
    if (IntuitionBase) CloseLibrary((struct Library *)IntuitionBase);
    return result;
}

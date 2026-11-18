/* Diagnostics on a newly allocated bitmap; does not change guest libraries/files.
 * Native SDK: ppc-morphos-gcc -noixemul -O2 morphos-bitmap-probe.c -lGL -o bitmap-probe
 * Optional argument: argb or rgba; default requests bgra. Logs the actual format.
 * Second argument 3d-only samples BMF_3DTARGET CPU/2D access, without GL bitmap init.
 * gl_error=ffff marks samples taken before a GL bitmap context exists.
 */
#include <stdio.h>
#include <string.h>
#include <proto/exec.h>
#include <proto/intuition.h>
#include <proto/graphics.h>
#include <proto/cybergraphics.h>
#include <proto/tinygl.h>
#include <tgl/gl.h>
#include <tgl/gla.h>
#include <graphics/rpattr.h>

struct IntuitionBase *IntuitionBase;
struct GfxBase *GfxBase;
struct Library *CyberGfxBase;
struct Library *TinyGLBase;
GLContext *__tglContext;

static const unsigned char rgba[4] = { 32, 96, 176, 224 };
static const unsigned char rect_rgba[4] = { 48, 112, 192, 232 };
static const unsigned char gpu_rgba[4] = { 80, 144, 208, 240 };

static APTR lock_bitmap(struct BitMap *bitmap, ULONG *format, ULONG *stride,
                        APTR *base)
{
    struct TagItem tags[] = {
        { LBMI_PIXFMT, (IPTR)format },
        { LBMI_BYTESPERROW, (IPTR)stride },
        { LBMI_BASEADDRESS, (IPTR)base },
        { TAG_DONE, 0 }
    };
    return LockBitMapTagList(bitmap, tags);
}

static int cpu_write(struct BitMap *bitmap, int raw_bgra)
{
    ULONG format = 0, stride = 0;
    APTR base = 0, handle = lock_bitmap(bitmap, &format, &stride, &base);
    unsigned char pixel[4];
    if (!handle) return 0;
    if (!base || stride < 32*4) {
        puts("CPU_WRITE invalid locked base/stride");
        UnLockBitMap(handle);
        return 0;
    }
    if (raw_bgra) {
        /* Observe how CGX decodes explicit bytes; do not assume private format flags. */
        pixel[0]=rgba[2]; pixel[1]=rgba[1]; pixel[2]=rgba[0]; pixel[3]=rgba[3];
    } else switch (format) {
    case PIXFMT_ARGB32:
        pixel[0]=rgba[3]; pixel[1]=rgba[0]; pixel[2]=rgba[1]; pixel[3]=rgba[2];
        break;
    case PIXFMT_BGRA32:
        pixel[0]=rgba[2]; pixel[1]=rgba[1]; pixel[2]=rgba[0]; pixel[3]=rgba[3];
        break;
    case PIXFMT_RGBA32:
        memcpy(pixel, rgba, 4);
        break;
    default:
        printf("CPU_WRITE unsupported format %lu\n", (unsigned long)format);
        UnLockBitMap(handle);
        return 0;
    }
    for (unsigned int y=0; y<32; ++y)
        for (unsigned int x=0; x<32; ++x)
            memcpy((unsigned char *)base+y*stride+x*4, pixel, 4);
    UnLockBitMap(handle);
    return 1;
}

static int sample(const char *stage, struct BitMap *bitmap, struct RastPort *rp,
                  int read_gl, const unsigned char *expected)
{
    ULONG format = 0, stride = 0;
    APTR base = 0, handle;
    unsigned char raw[4] = {0}, cgx[4] = {0}, gl[4] = {0};
    GLenum error = 0xffff;
    if (read_gl) {
        glFinish();
        glPixelStorei(GL_PACK_ALIGNMENT, 1);
        glReadPixels(4, 4, 1, 1, GL_RGBA, GL_UNSIGNED_BYTE, gl);
        error = glGetError();
    }
    ReadPixelArray(cgx, 0, 0, 4, rp, 4, 4, 1, 1, RECTFMT_RGBA);
    handle = lock_bitmap(bitmap, &format, &stride, &base);
    if (!handle || !base || stride < 32*4) {
        printf("CHECK_FAILED %s invalid bitmap lock/base/stride\n", stage);
        if (handle) UnLockBitMap(handle);
        return 0;
    }
    memcpy(raw, (unsigned char *)base+4*stride+4*4, 4);
    UnLockBitMap(handle);
    printf("%s fmt=%lu stride=%lu base=%p raw=%02x%02x%02x%02x "
           "cgx_rgba=%02x%02x%02x%02x gl_rgba=%02x%02x%02x%02x gl_error=%04x\n",
           stage, (unsigned long)format, (unsigned long)stride, base,
           raw[0], raw[1], raw[2], raw[3], cgx[0], cgx[1], cgx[2], cgx[3],
           gl[0], gl[1], gl[2], gl[3], (unsigned int)error);
    fflush(stdout);
    if (expected && (memcmp(cgx, expected, 4) ||
                     (read_gl && (error != GL_NO_ERROR || memcmp(gl, expected, 4))))) {
        printf("CHECK_FAILED %s expected=%02x%02x%02x%02x\n", stage,
               expected[0], expected[1], expected[2], expected[3]);
        return 0;
    }
    if (expected && format == PIXFMT_BGRA32 &&
        (raw[0] != expected[2] || raw[1] != expected[1] ||
         raw[2] != expected[0] || raw[3] != expected[3])) {
        printf("CHECK_FAILED %s BGRA bitmap bytes differ\n", stage);
        return 0;
    }
    return 1;
}

int main(int argc, char **argv)
{
    struct Screen *screen = 0;
    struct BitMap *bitmap = 0;
    struct RastPort rp;
    int context_created = 0, result = 1;
    ULONG requested_format = argc > 1 && argv[1][0] == 'a' ? PIXFMT_ARGB32 :
                             argc > 1 && argv[1][0] == 'r' ? PIXFMT_RGBA32 : PIXFMT_BGRA32;
    int target_only = argc > 2 && !strcmp(argv[2], "3d-only");
    puts("BITMAP_PROBE starting");
    fflush(stdout);
    IntuitionBase = (struct IntuitionBase *)OpenLibrary("intuition.library", 50);
    GfxBase = (struct GfxBase *)OpenLibrary("graphics.library", 50);
    CyberGfxBase = OpenLibrary("cybergraphics.library", 50);
    TinyGLBase = OpenLibrary("tinygl.library", 53);
    if (TinyGLBase) __tglContext = GLInit();
    if (!IntuitionBase || !GfxBase || !CyberGfxBase || !TinyGLBase || !__tglContext) {
        puts("Missing graphics or TinyGL library/context");
        goto done;
    }
    TGLSetAutomaticContextVersion(TinyGLBase, __tglContext);
    printf("TinyGL %u.%u context=%u; expected_rgba=2060b0e0\n",
           TinyGLBase->lib_Version, TinyGLBase->lib_Revision,
           TGLGetContextVersion(__tglContext));
    screen = LockPubScreen(0);
    if (!screen) { puts("No public screen"); goto done; }
    printf("screen_fmt=%lu bytes_per_pixel=%lu\n",
           (unsigned long)GetCyberMapAttr(screen->RastPort.BitMap, CYBRMATTR_PIXFMT),
           (unsigned long)GetCyberMapAttr(screen->RastPort.BitMap, CYBRMATTR_BPPIX));
    bitmap = AllocBitMap(32, 32, 32, BMF_CLEAR | BMF_REQUESTVMEM |
                        (target_only ? BMF_3DTARGET : 0) |
                        BMF_SPECIALFMT | SHIFT_PIXFMT(requested_format),
                        screen->RastPort.BitMap);
    if (!bitmap) { puts("AllocBitMap failed"); goto done; }
    InitRastPort(&rp);
    rp.BitMap = bitmap;
    printf("bitmap=%p requested_fmt=%lu declared_fmt=%lu bytes_per_pixel=%lu\n", bitmap,
           (unsigned long)requested_format,
           (unsigned long)GetCyberMapAttr(bitmap, CYBRMATTR_PIXFMT),
           (unsigned long)GetCyberMapAttr(bitmap, CYBRMATTR_BPPIX));
    printf("bitmap_width=%lu height=%lu linear=%ld\n",
           (unsigned long)GetCyberMapAttr(bitmap, CYBRMATTR_WIDTH),
           (unsigned long)GetCyberMapAttr(bitmap, CYBRMATTR_HEIGHT),
           (long)GetCyberMapAttr(bitmap, CYBRMATTR_ISLINEARMEM));
    if (!cpu_write(bitmap, target_only)) goto done;
    if (!sample(target_only ? "CPU_RAW_BGRA_3DTARGET" : "CPU", bitmap, &rp,
                0, target_only ? 0 : rgba)) goto done;
    struct TagItem rp_tags[] = {
        { RPTAG_PenMode, FALSE }, { RPTAG_FgColor, 0xe83070c0 }, { TAG_DONE, 0 }
    };
    SetRPAttrsA(&rp, rp_tags);
    RectFill(&rp, 0, 0, 31, 31);
    if (!sample("RECTFILL", bitmap, &rp, 0, target_only ? 0 : rect_rgba)) goto done;
    if (target_only) { puts("3DTARGET observations only; private format flags not interpreted"); result = 0; goto done; }
    puts("TINYGL_CONTEXT begin");
    fflush(stdout);
    struct TagItem context_tags[] = {
        { TGL_CONTEXT_BITMAP, (IPTR)bitmap },
        { TGL_CONTEXT_NODEPTH, TRUE }, { TAG_DONE, 0 }
    };
    context_created = GLAInitializeContext(__tglContext, context_tags);
    if (!context_created) { puts("Bitmap TinyGL context failed"); goto done; }
    sample("TINYGL_INITIAL", bitmap, &rp, 1, 0);
    puts("TINYGL_DRAW expected_rgba=5090d0f0 (different from CPU/RectFill)");
    glViewport(0, 0, 32, 32);
    glMatrixMode(GL_PROJECTION); glLoadIdentity(); glOrtho(0, 32, 0, 32, -1, 1);
    glMatrixMode(GL_MODELVIEW); glLoadIdentity();
    glDisable(GL_BLEND); glDisable(GL_DEPTH_TEST); glDisable(GL_DITHER);
    glDisable(GL_LIGHTING); glDisable(GL_TEXTURE_2D);
    glColor4ub(gpu_rgba[0], gpu_rgba[1], gpu_rgba[2], gpu_rgba[3]);
    glBegin(GL_QUADS);
    glVertex2i(0, 0); glVertex2i(32, 0); glVertex2i(32, 32); glVertex2i(0, 32);
    glEnd();
    if (!sample("TINYGL_BEFORE_SWAP", bitmap, &rp, 1, gpu_rgba)) goto done;
    GLASwapBuffers(__tglContext);
    if (!sample("TINYGL_AFTER_SWAP", bitmap, &rp, 1, gpu_rgba)) goto done;
    puts("CHECK_PASSED CPU, distinct RectFill and distinct TinyGL RGBA");
    result = 0;
done:
    if (context_created) GLADestroyContext(__tglContext);
    if (__tglContext) GLClose(__tglContext);
    if (bitmap) FreeBitMap(bitmap);
    if (screen) UnlockPubScreen(0, screen);
    if (CyberGfxBase) CloseLibrary(CyberGfxBase);
    if (TinyGLBase) CloseLibrary(TinyGLBase);
    if (GfxBase) CloseLibrary((struct Library *)GfxBase);
    if (IntuitionBase) CloseLibrary((struct Library *)IntuitionBase);
    return result;
}

/* Linux readback probe; compile with Vulkan headers, -shared -fPIC -pthread -ldl. */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <vulkan/vulkan.h>

static pthread_once_t once = PTHREAD_ONCE_INIT;
static PFN_vkCmdCopyImageToBuffer real_copy;
static FILE *trace;

static void initialize(void)
{
    const char *path = getenv("TRACE_FILE");

    real_copy = (PFN_vkCmdCopyImageToBuffer)dlsym(RTLD_NEXT, "vkCmdCopyImageToBuffer");
    if (!real_copy || !path || !*path || !(trace = fopen(path, "a"))) {
        fprintf(stderr, "depth-copy-trace: cannot resolve Vulkan copy or open TRACE_FILE\n");
        abort();
    }
}

VKAPI_ATTR void VKAPI_CALL vkCmdCopyImageToBuffer(
    VkCommandBuffer command, VkImage image, VkImageLayout layout,
    VkBuffer buffer, uint32_t count, const VkBufferImageCopy *regions)
{
    uint32_t depth = 0, stencil = 0, aspects = 0;

    pthread_once(&once, initialize);
    for (uint32_t i = 0; i < count; i++) {
        VkImageAspectFlags mask = regions[i].imageSubresource.aspectMask;
        aspects |= mask;
        depth += !!(mask & VK_IMAGE_ASPECT_DEPTH_BIT);
        stencil += !!(mask & VK_IMAGE_ASPECT_STENCIL_BIT);
    }
    flockfile(trace);
    fprintf(trace, "COPY DEPTH=%u STENCIL=%u COUNT=%u ASPECTS=0x%x\n",
            depth, stencil, count, aspects);
    fflush(trace);
    funlockfile(trace);
    real_copy(command, image, layout, buffer, count, regions);
}

# MorphOS fork release preparation

The local tested builds use AMD-specific compiler flags and are not portable releases. The portable build recipe is separate from those binaries.

The bundle expects the QEMU fork in a sibling repository named
`poweremu-qemu`. Its pinned commit must be available at that remote before
a recursive clone can build the emulator.

1. Publish the reviewed QEMU pin before distributing the matching bundle.
2. Commit the selected QEMU pin and bundle changes. The packaging script rejects dirty source trees and a QEMU HEAD that differs from the committed bundle pin. It records the actual QEMU and bundle commits in `VERSION` and `SOURCE.txt`.
3. Keep the complete verified Mesa notice in QEMU's register-name header and the binary artifact. Initial source archives exclude inherited firmware binaries and optional guest content; the portable binary artifact excludes them too. Supply firmware separately using the [verified primary download](../firmware/PROVENANCE.md). Resolve exact source/build records and notices before adding any excluded content to a later release.
4. Build from clean public checkouts with `tools/build-linux-qemu.sh /tmp/morphos-qemu.tar.xz`. The variables `PPCOSX_SOURCE_URL` and `PPCOSX_BUNDLE_SOURCE_URL` may supply the published HTTPS repository URLs when origin is a local mirror. The artifact includes QEMU and bundle license files, the Mesa notice, the SPIRV-Cross copyright notices and Apache 2.0 license, the build recipe, exact source identifiers and SHA-256 checksums. Downloaded and cached archives must match the pinned QEMU commit and pass the checksum manifest; legacy archives without it fall back to source. Verify public source availability and dependency notices before publishing the artifact.
5. Check a new recursive checkout through the actual public URLs. Build/install the artifact and run a visible mac99 boot with user-provided firmware/ISO and disposable disks. CI checks checksums and device/frontend availability; those checks do not prove a guest boot, rendering correctness or performance.

The CI branch filters cover main and morphos-radeon. Pushes build and check artifacts; publishing release assets requires a manual workflow dispatch after approval. Public commit reachability remains unverified until publication. The source archive of this bundle alone does not include the QEMU submodule; provide recursive checkout instructions and retain access to both exact source commits.

For a disposable local checkout, set the three paths below to local mirrors and a new destination. Offline recursion also needs local URL overrides for any nested submodules; this checks local commit availability only, not public reachability.

```sh
git clone "$LOCAL_BUNDLE_MIRROR" "$CHECKOUT"
git -C "$CHECKOUT" -c protocol.file.allow=always \
    -c submodule.qemu.url="$LOCAL_QEMU_MIRROR" \
    submodule update --init --recursive
```

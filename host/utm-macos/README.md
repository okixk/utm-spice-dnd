# macOS host build

This directory builds a development UTM application containing the target-aware Finder drop integration.

```sh
./host/utm-macos/build.sh
open dist/UTM-SPICE-DnD.app
```

The build uses these exact bases:

- UTM `048ca7498ea3a374439149d51739d94c5300bcda`;
- CocoaSpice `ff3fb176c8b7ff13acf9e3832347ec7ffab2fa1c`.

The script provisions the UTM macOS arm64 dependency sysroot using UTM's own `scripts/build_dependencies.sh` when `UTM_SYSROOT_DIR` is not set. That path requires UTM's documented native build prerequisites, including Homebrew-provided tools; the script does not install them automatically. If you already have a compatible sysroot, point the build at it to avoid rebuilding dependencies:

```sh
UTM_SYSROOT_DIR=/path/to/sysroot-macOS-arm64 ./host/utm-macos/build.sh
```

The script resolves UTM's normal package dependencies in `.build/SourcePackages`, then replaces only that isolated CocoaSpice checkout with the patched checkout. It does not alter `Package.resolved`, the upstream repositories, or `/Applications/UTM.app`.

The resulting app is an ad-hoc/development build. It may lack Apple's normal UTM networking entitlements, so a VM configured for vmnet shared networking may need to use QEMU user networking for development. SPICE DnD does not depend on guest IP networking.
